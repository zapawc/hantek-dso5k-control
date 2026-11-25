"""Local, cooperative WinUSB instrument service for the retained DSO5102P."""

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from threading import BoundedSemaphore, Condition, Thread
from time import monotonic
from uuid import uuid4

from . import _dw_profile as dw

from ._acq11 import Session, _running, describe
from ._host import assess_host, finalization_guard, host_snapshot, lease, require_no_finalization
from ._native_finalization import create_record, prepare, publish
from ._pair_windows import _claim_pair, _source_commit
from ._retrieve import file_record, record_cleanup
from ._service_capture import Unsupported, instrument, s22_probe, set_run_state, snapshot, triggered, verify_profile
from ._session import state_path


ITEMS = {"mean": (3, "V"), "minimum": (6, "V"), "maximum": (7, "V"),
         "vpp": (4, "V"), "frequency": (1, "Hz"), "period": (2, "s")}
CHANNELS = ("CH1", "CH2")
S22 = "s22_instrument_cache"
WAVE = "native_waveform"
WAVE_ITEMS = {"mean": ("mean_v", "V"), "minimum": ("min_v", "V"), "maximum": ("max_v", "V"),
              "vpp": ("vpp_v", "V"), "frequency": ("frequency_hz", "Hz"),
              "period": ("period_s", "s"), "duty_cycle": ("duty_cycle", "1")}
DISPLAY = re.compile(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([pnumkKMµ]?)\s*(V|Hz|hz|s)")
PREFIX = {"": 1., "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6,
          "m": .001, "k": 1000., "K": 1000., "M": 1e6}
STATUS_SCHEMA = "hantek.service.v1"
JOB_ID = re.compile(r"[0-9a-f]{32}")


class JobTimeout(RuntimeError):
    """The accepted execution budget expired at a command boundary."""


class Unavailable(Unsupported):
    """An operation needs a usable device session."""


def utc():
    return datetime.now(timezone.utc).isoformat()


def measurement_value(text, units):
    match = DISPLAY.fullmatch(text.strip())
    if not match or match[3].lower() != units.lower():
        return None, "device_unavailable" if text.strip() in ("", "?", "---") else "invalid_display_value"
    value = float(match[1]) * PREFIX[match[2]]
    if not math.isfinite(value):
        return None, "invalid_display_value"
    if value == 0:
        # The retained device has returned formatted zero for a missing channel cache.
        return None, "zero_unverified"
    if units != "V" and value < 0:
        return None, "invalid_display_value"
    return value, None


class Service:
    def __init__(self, evidence, *, interval, stale_after, measurements, clock=monotonic,
                 channels=CHANNELS, source="s22", settings_every=None, waveform_every=1.,
                 instrument_id="hantek-dso5102p", rapid_reads=False, physical_probes=None,
                 detailed=False, configured_slots_only=False, priority=()):
        self.evidence = Path(evidence)
        self.interval, self.stale_after, self.clock = interval, stale_after, clock
        self.channels = channels
        self.sensors = [(ch, name) for name in measurements if name in ITEMS for ch in channels]
        self.poll_order = list(self.sensors)
        preferred = [sensor for sensor in self.sensors if sensor[1] in priority]
        ordinary = [sensor for sensor in self.sensors if sensor[1] not in priority]
        if preferred and ordinary:
            self.poll_order = [sensor for index, other in enumerate(ordinary)
                               for sensor in (preferred[index % len(preferred)], other)]
            self.poll_order.extend(preferred[len(ordinary):])
        self.wave_quantities = tuple(measurements)
        self.source = source
        self.settings_every, self.waveform_every = settings_every, waveform_every
        self.instrument_id, self.instance_id = instrument_id, uuid4().hex
        self.rapid_reads, self.physical_probes = rapid_reads, physical_probes
        self.detailed, self.configured_slots_only = detailed, configured_slots_only
        self.raw_settings = None
        self.next_settings = self.next_wave = 0.
        self.monitor_batch, self.monitor_turn = -1, 0
        self.changed = Condition()
        self.state = "STARTING"
        self.connected = False
        self.stopping = False
        self.done = False
        self.restoration_owed = False
        self.error = None
        self.settings = None
        self.cache = {}
        self.jobs = {}
        self.pending = None
        self.active = None
        self.poll_count = 0
        self.resume_count = 0
        self.started = clock()
        self.refresh_after = self.started

    def status(self):
        with self.changed:
            return {"schema": STATUS_SCHEMA, "instrument": instrument(), "state": self.state,
                    "connected": self.connected, "polling_suspended": self.state != "MONITORING",
                    "poll_count": self.poll_count, "resume_count": self.resume_count, "active_job": self.active,
                    "queued_job": self.pending, "stopping": self.stopping, "worker_done": self.done,
                    "error": self.error, "uptime_s": self.clock() - self.started}

    def current_settings(self):
        with self.changed:
            return deepcopy(self.settings)

    def series(self):
        rows = [(channel, name, S22, ITEMS[name][1]) for channel, name in self.sensors if self.source != "waveform"]
        if self.source != "s22":
            rows.extend((channel, name, WAVE, WAVE_ITEMS[name][1])
                        for channel in self.channels for name in self.wave_quantities)
        return rows

    def latest(self, *, all_sources=False):
        with self.changed:
            now = self.clock()
            results = []
            for original in self.cache.values():
                if not all_sources and original["source"] != S22:
                    continue
                row = deepcopy(original)
                observed = row.pop("received_monotonic")
                row["transport_age_s"] = max(0., now - observed)
                row["configuration_current"] = row["configuration"]["stable_sha256"] == self.settings["stable_sha256"]
                row["stale"] = (self.state != "MONITORING" or row["transport_age_s"] > self.stale_after
                                or not row["configuration_current"] or observed < self.refresh_after)
                if row["last_good"] is not None:
                    row["last_good"]["age_s"] = max(0., now - row["last_good"].pop("received_monotonic"))
                    row["last_good"]["stale"] = row["stale"] or not row["available"]
                results.append(row)
            return {"schema": STATUS_SCHEMA, "instrument": instrument(), "state": self.state,
                    "connected": self.connected, "error": self.error, "measurements": results}

    def submit(self, request, *, budget=None):
        with self.changed:
            if budget is not None and (self.state in ("STARTING", "FAULT") or not self.connected):
                raise Unavailable("Instrument session is unavailable")
            if self.stopping or self.state != "MONITORING" or self.pending is not None:
                raise Unsupported("Worker is not available for a new operation")
            if budget is not None:
                if self.raw_settings is None:
                    raise Unavailable("Settings have not been observed")
                kind = request["kind"]
                stopped = self.raw_settings[20] in (0, 5)
                if stopped and (kind == "triggered" or (kind == "run-state" and request["running"])):
                    if not request["allow_overwrite"]:
                        raise Unsupported("Existing stopped acquisition requires explicit overwrite consent")
                if kind == "triggered":
                    verify_profile(self.raw_settings, request["path"])
            if len(self.jobs) == 64:
                del self.jobs[next(iter(self.jobs))]
            job_id = uuid4().hex
            job = {"id": job_id, "request": request, "state": "QUEUED", "queued_at": utc()}
            if budget is not None:
                job["deadline"] = self.clock() + budget
            self.jobs[job_id] = job
            self.pending = job_id
            self.changed.notify_all()
            return deepcopy(job)

    def job(self, job_id):
        with self.changed:
            job = self.jobs.get(job_id)
            return deepcopy(job)

    def shutdown(self):
        with self.changed:
            self.stopping = True
            if self.pending:
                self.jobs[self.pending].update(state="CANCELLED", ended_at=utc())
                self.pending = None
            if self.state not in ("FAULT", "STOPPED"):
                self.state = "STOPPING"
            self.changed.notify_all()

    def _settings(self, raw):
        snapshot = describe(raw)
        # The acquisition-state and cached counter are runtime values.
        stable = raw[:20] + raw[21:27] + raw[35:]
        snapshot.update(observed_at=utc(), s81_sha256=sha256(raw).hexdigest(),
                        stable_sha256=sha256(stable).hexdigest())
        with self.changed:
            self.settings = snapshot
            self.raw_settings = raw

    def _poll(self, session, index):
        now = self.clock()
        if (self.raw_settings is None or (self.settings_every is None and index == 0 and self.source == "s22")
                or (self.settings_every is not None and now >= self.next_settings)):
            self._settings(session.settings("telemetry_settings"))
            self.next_settings = self.clock() + (self.settings_every or 0.)
        if self.source == "waveform" or (self.source == "both" and now >= self.next_wave):
            self._poll_waveform(session)
            self.next_wave = self.clock() + self.waveform_every
            if self.source == "waveform":
                return
        channel, kind = self.poll_order[index]
        settings = self.current_settings()
        code, units = ITEMS[kind]
        started = self.clock()
        reason, value, raw = "channel_disabled", None, None
        if self.configured_slots_only and [CHANNELS.index(channel), code] not in settings["measurement_slots"]:
            reason = "slot_not_configured"
        elif settings["channels"][channel]["enabled"]:
            raw = session.measurement(code, CHANNELS.index(channel))
            value, reason = measurement_value(raw, units)
            if kind == "vpp" and value is not None and value < 0:
                value, reason = None, "invalid_display_value"
        now = self.clock()
        configuration = {key: settings[key] for key in ("s81_sha256", "stable_sha256", "observed_at")}
        configuration["channel"] = settings["channels"][channel]
        row = {"channel": channel, "measurement": kind, "unit": units, "value": value, "raw": raw,
               "source": S22, "device_freshness": "UNKNOWN",
               "acquisition_state_code": settings["trigger_state_code"], "available": value is not None,
               "validity": "display_value_only" if value is not None else "unavailable",
               "error": reason, "observed_at": utc(), "received_monotonic": now,
               "configuration": configuration, "observation_s": now - started}
        self._cache(row)
        session.log("telemetry", channel=channel, measurement=kind, available=row["available"],
                    observation_s=row["observation_s"])

    def _cache(self, row):
        key = (row["channel"], row["measurement"])
        if row["source"] != S22:
            key += (row["source"],)
        with self.changed:
            prior = self.cache.get(key, {})
            row["last_good"] = ({field: row[field] for field in
                                 ("value", "observed_at", "received_monotonic", "configuration")}
                                if row["available"] else prior.get("last_good"))
            self.cache[key] = row
            self.poll_count += 1

    def _poll_waveform(self, session):
        setup = self.current_settings()
        observation = None
        if _running(self.raw_settings):
            observation = snapshot(session, self.raw_settings, uuid4().hex, retain=False,
                                   physical_probes=self.physical_probes)
        now, observed_at = self.clock(), utc()
        rows = []
        for channel in self.channels:
            metrics = observation["derived_measurements"].get(channel, {}) if observation else {}
            for name in self.wave_quantities:
                metric, unit = WAVE_ITEMS[name]
                value = metrics.get(metric)
                reason = None
                if observation is None:
                    reason = "acquisition_stopped"
                elif not observation["availability"].get(channel):
                    reason = "channel_unavailable"
                elif not metrics:
                    reason = "conversion_unverified"
                elif metrics["clipped"]:
                    reason = "clipped"
                elif unit == "V" and not metrics["physical_voltage_verified"]:
                    reason = "probe_attenuation_unverified"
                elif value is None:
                    reason = "insufficient_edges"
                rows.append({"channel": channel, "measurement": name, "unit": unit,
                             "source": WAVE, "raw": None, "device_freshness": "UNKNOWN",
                             "acquisition_state_code": setup["trigger_state_code"],
                             "value": None if reason else value, "available": reason is None,
                             "error": reason, "validity": "waveform_estimate" if not reason else "unavailable",
                             "observed_at": observed_at, "received_monotonic": now,
                             "configuration": {key: setup[key] for key in ("s81_sha256", "stable_sha256", "observed_at")},
                             "waveform": ({"acquisition_id": observation["acquisition_id"],
                                           "timing": observation["timing"],
                                           **{k: metrics.get(k) for k in ("sample_count", "sample_interval_s",
                                                                         "coverage_s")}} if observation else {})})
        with self.changed:
            for row in rows:
                self._cache(row)

    @contextmanager
    def _monitor_evidence(self, session):
        if self.detailed or not isinstance(session, Session):
            yield
            return
        batch, turn = divmod(self.monitor_turn, 16)
        self.monitor_turn += 1
        folder = self.evidence / f"monitor-{batch % 4}"
        if turn == 0:
            if folder.exists():
                for entry in folder.iterdir():
                    entry.unlink()
            else:
                folder.mkdir()
        with (folder / "journal.jsonl").open("a", encoding="utf-8") as journal:
            original = session.evidence, session.journal
            session.evidence, session.journal = folder, journal
            try:
                yield
            finally:
                session.evidence, session.journal = original

    def _requires_restore(self, needed):
        self.restoration_owed = needed

    def _perform(self, session, job_id):
        job = self.job(job_id)
        request = job["request"]
        deadline = job.get("deadline")

        def check_budget():
            if deadline is not None and self.clock() >= deadline:
                raise JobTimeout("Execution budget expired at a command boundary")
        root = session.evidence
        dest = root / job_id
        dest.mkdir()
        session.evidence = dest
        result = {"same_shot": "UNPROVEN", "kind": request["kind"]}
        error = None
        fatal = None
        started = self.clock()
        if isinstance(session, Session):
            session.job_check = check_budget
        try:
            check_budget()
            baseline = session.settings("job_initial")
            result["initial"] = describe(baseline)
            if request["kind"] == "snapshot":
                result["native"] = snapshot(session, baseline, job_id)
                result["method"] = "native_s02"
                result["acquisition_outcome"] = ("succeeded" if any(result["native"].get("availability", {}).values())
                                                 else "failed")
                if deadline is not None and result["acquisition_outcome"] == "failed":
                    raise Unsupported("No native waveform records were returned")
            elif request["kind"] == "triggered":
                triggered(session, baseline, job_id, request, result, self._requires_restore)
            elif request["kind"] == "run-state":
                set_run_state(session, baseline, request, result, self._requires_restore)
            else:
                s22_probe(session, baseline, request, result, self._requires_restore)
            check_budget()
        except (Exception, KeyboardInterrupt) as exc:
            error = {"type": type(exc).__name__, "detail": str(exc)}
            if self.restoration_owed or not session.settled:
                fatal = exc
        finally:
            if isinstance(session, Session):
                session.job_check = None
            if request["kind"] == "triggered":
                if result.get("restoration_verified"):
                    result["restoration_outcome"] = "succeeded"
                elif self.restoration_owed:
                    result["restoration_outcome"] = "failed" if session.settled else "unknown"
                result.setdefault("acquisition_outcome", "failed" if session.settled else "unknown")
            raw = result.pop("restored_raw", None)
            if raw is not None:
                self._settings(raw)
            session.evidence = root
            artifacts = {entry.name: file_record(entry) for entry in dest.iterdir() if entry.is_file()
                         and entry.suffix in (".json", ".csv")}
            result.update(artifacts=artifacts, error=error, duration_s=self.clock() - started)
            create_record(dest / "result.json", result)
        return result, fatal

    def loop(self, session):
        index, next_poll = 0, self.clock()
        with self.changed:
            self.connected = True
            self.state = "STOPPING" if self.stopping else "MONITORING"
        while True:
            with self.changed:
                while not self.stopping and self.pending is None and self.clock() < next_poll:
                    self.changed.wait(timeout=next_poll - self.clock())
                if self.stopping:
                    return
                job_id = self.pending
                if job_id:
                    self.pending = None
                    self.active = job_id
                    self.state = "CAPTURING"
                    self.jobs[job_id].update(state="RUNNING", started_at=utc())
            if job_id is None:
                started = self.clock()
                with self._monitor_evidence(session):
                    self._poll(session, index)
                index = (index + 1) % max(1, len(self.poll_order))
                next_poll = (max(self.clock() + self.interval, started + self.waveform_every)
                             if self.source == "waveform" else self.clock() + self.interval)
            else:
                result, fatal = self._perform(session, job_id)
                index, next_poll = 0, self.clock()
                with self.changed:
                    job = self.jobs[job_id]
                    job.update(state="FAILED" if result["error"] else "SUCCEEDED", ended_at=utc(), result=result)
                    self.active = None
                    if not self.stopping and fatal is None:
                        self.resume_count += 1
                        self.refresh_after = self.clock()
                        self.next_settings = self.next_wave = 0.
                        self.state = "MONITORING"
                if fatal is not None:
                    raise fatal

    def worker(self, connect):
        try:
            connect(self)
        except (Exception, KeyboardInterrupt) as exc:
            with self.changed:
                self.error = {"type": type(exc).__name__, "detail": str(exc)}
                if self.active:
                    self.jobs[self.active].update(state="FAILED", error=self.error, ended_at=utc())
                    self.active = None
                if self.pending:
                    self.jobs[self.pending].update(state="CANCELLED", ended_at=utc())
                    self.pending = None
                self.state = "FAULT"
        finally:
            with self.changed:
                self.connected = False
                if self.state != "FAULT":
                    self.state = "STOPPED"
                self.done = True
                self.changed.notify_all()


def owned(service, source, *, marker, hold=lease, claim=_claim_pair,
          screen=lambda: assess_host(host_snapshot(), os.getpid(), (sys.executable, sys._base_executable))):
    """Own the shared mutex and claimed interface on the worker thread until shutdown."""
    folder = service.evidence
    guard = finalization_guard(marker)
    outcome = {"source_commit": source, "session_id": uuid4().hex, "acquisition_outcome": "PASS",
               "status": "BLOCKED", "marker": "NOT_CHECKED",
               "cleanup": {"interface_released": False, "lease_released": False}}
    try:
        with record_cleanup(hold(), outcome["cleanup"], "lease_released"):
            require_no_finalization(marker)
            if os.path.lexists(marker):
                raise RuntimeError("Prior interruption marker requires evidence-based reconciliation")
            host = screen()
            if host["blocked"]:
                raise RuntimeError(host["blocked"])
            marker.parent.mkdir(parents=True, exist_ok=True)
            if folder.stat().st_dev != marker.parent.stat().st_dev:
                raise RuntimeError("Private evidence must share the interruption-state volume")
            identity = create_record(marker, {"session_id": outcome["session_id"], "operation": "TEL-SVC-01",
                                             "evidence": str(folder), "source_commit": source, "utc": utc()})
            outcome.update(marker="RETAINED", status="FAIL")
            with (folder / "journal.jsonl").open("x", encoding="utf-8") as journal:
                with record_cleanup(claim(), outcome["cleanup"], "interface_released") as link:
                    session = Session(link, folder, journal)
                    session.rapid_reads = service.rapid_reads
                    session.startup()
                    service.loop(session)
                if not session.settled or service.restoration_owed:
                    raise RuntimeError("Unsettled instrument history")
            outcome.update(request_attempts=session.count, poll_count=service.poll_count,
                           capture_resumptions=service.resume_count)
            ready = prepare(folder, marker, identity, guard, outcome)
        publish(folder, marker, guard, ready, outcome)
    except (Exception, KeyboardInterrupt) as exc:
        outcome.update(error={"type": type(exc).__name__, "detail": str(exc)})
        create_record(folder / "service_fault.json", outcome)
        raise


def parse_job(payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected JSON object")
    kind = payload.get("kind")
    if kind == "snapshot":
        if set(payload) != {"kind"}:
            raise ValueError("Unexpected fields")
        return payload
    if kind == "s22-probe":
        configure = payload.get("configure_fixture", False)
        slots = payload.get("configure_slots", False)
        if (set(payload) - {"kind", "configure_fixture", "configure_slots"}
                or type(configure) is not bool or type(slots) is not bool):
            raise ValueError("Expected optional boolean configure_fixture and configure_slots")
        return {"kind": kind, "configure_fixture": configure, **({"configure_slots": True} if slots else {})}
    if kind == "triggered":
        path = payload.get("path", "auto")
        overwrite = payload.get("allow_overwrite", False)
        if (set(payload) - {"kind", "path", "allow_overwrite"} or path not in ("auto", "native", "frozen")
                or type(overwrite) is not bool):
            raise ValueError("Expected auto, native or frozen path and optional boolean allow_overwrite")
        return {"kind": kind, "path": path, "allow_overwrite": overwrite}
    if kind == "run-state":
        overwrite = payload.get("allow_overwrite", False)
        if (set(payload) - {"kind", "running", "allow_overwrite"} or type(payload.get("running")) is not bool
                or type(overwrite) is not bool):
            raise ValueError("Expected boolean running and optional boolean allow_overwrite")
        return {"kind": kind, "running": payload["running"], "allow_overwrite": overwrite}
    raise ValueError("Unsupported operation")


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    timeout = .2

    def __init__(self, service, token, port=0):
        self.service, self.token = service, token
        self.clients = BoundedSemaphore(16)
        super().__init__(("127.0.0.1", port), LocalHandler)

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(5)
        return sock, address

    def process_request(self, request, address):
        if not self.clients.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.clients.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.clients.release()


class LocalHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send(self, code, body, content_type="application/json", *, location=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if location is not None:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, value, *, location=None, problem=False):
        self._send(code, json.dumps(value, allow_nan=False).encode("utf-8"),
                   "application/problem+json" if problem else "application/json", location=location)

    def _platform(self):
        return self.path == dw.PREFIX or self.path.startswith(dw.PREFIX + "/")

    def _error(self, status, code, message):
        if self._platform():
            return self._json(status, dw.problem(status, code, message), problem=True)
        return self._json(status, {"error": message})

    def _authorized(self):
        host = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get_all("Host") != [host]:
            self._error(400, "invalid_host", "Expected the loopback Host header")
            return False
        origin = self.headers.get_all("Origin")
        if ((origin is not None and origin != ["http://" + host])
                or self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site")):
            self._error(403, "invalid_origin", "Cross-origin request refused")
            return False
        expected = ("Bearer " + self.server.token).encode()
        authorization = self.headers.get_all("Authorization", [])
        if len(authorization) == 1 and compare_digest(authorization[0].encode(), expected):
            return True
        self._error(401, "invalid_credentials", "Bearer token required")
        return False

    def _artifact(self, job_id, name, entry, media):
        if entry["bytes"] > 4 * 1024 * 1024:
            return self._error(409, "artifact_size_mismatch", "Recorded artifact exceeds service bound")
        try:
            with (self.server.service.evidence / job_id / name).open("rb") as stream:
                data = stream.read(entry["bytes"] + 1)
        except OSError:
            return self._error(409, "artifact_unavailable", "Recorded artifact unavailable")
        if len(data) != entry["bytes"] or sha256(data).hexdigest() != entry["sha256"]:
            return self._error(409, "artifact_integrity_mismatch", "Recorded artifact integrity mismatch")
        return self._send(200, data, media)

    def _platform_get(self, svc):
        route = self.path[len(dw.PREFIX):]
        if route == "/identity":
            device = instrument()
            return self._json(200, {"profile_version": "0.1", "instrument_id": svc.instrument_id,
                                    "instance_id": svc.instance_id, "driver_version": "0.2.0",
                                    "firmware_version": device["firmware"], "model": device["model"]})
        if route == "/status":
            return self._json(200, dw.status(svc))
        if route == "/settings":
            return self._json(200, dw.settings(svc))
        if route == "/telemetry":
            return self._json(200, dw.telemetry(svc))
        if route == "/capabilities":
            return self._json(200, dw.capabilities(svc))
        parts = route.split("/")
        if len(parts) in (3, 5) and parts[1] == "jobs" and JOB_ID.fullmatch(parts[2]):
            job = svc.job(parts[2])
            if job is not None and len(parts) == 3:
                return self._json(200, dw.job_view(job))
            if job is not None and parts[3] == "artifacts":
                match = dw.artifact_map(job).get(parts[4])
                if match:
                    name, _ = match
                    return self._artifact(parts[2], name, job["result"]["artifacts"][name],
                                          "application/octet-stream")
        return self._error(404, "not_found", "Resource not found")

    def do_GET(self):
        if not self._authorized():
            return
        svc = self.server.service
        if self._platform():
            return self._platform_get(svc)
        if self.path == "/v1/status":
            return self._json(200, svc.status())
        if self.path == "/v1/settings":
            return self._json(200, {"settings": svc.current_settings()})
        if self.path == "/v1/measurements":
            return self._json(200, svc.latest())
        if self.path == "/v1/capabilities":
            return self._json(200, {"schema": STATUS_SCHEMA, "instrument": instrument(),
                                   "operations": ["snapshot", "triggered", "run-state", "s22-probe", "shutdown"],
                                   "triggered_paths": ["auto", "native", "frozen"], "same_shot": "UNPROVEN",
                                   "active_measurements": [list(item) for item in svc.sensors]})
        parts = self.path.split("/")
        if len(parts) in (4, 6) and parts[:3] == ["", "v1", "jobs"] and JOB_ID.fullmatch(parts[3]):
            job = svc.job(parts[3])
            if job is not None and len(parts) == 4:
                return self._json(200, job)
            if job is not None and parts[4] == "artifacts":
                name = parts[5]
                entry = job.get("result", {}).get("artifacts", {}).get(name)
                if entry and entry["bytes"] <= 4 * 1024 * 1024:
                    return self._artifact(parts[3], name, entry,
                                          "text/csv" if name.endswith(".csv") else "application/json")
        self._json(404, {"error": "Resource not found"})

    def do_POST(self):
        if not self._authorized():
            return
        svc = self.server.service
        platform = self._platform()
        prefix = dw.PREFIX if platform else "/v1"
        if self.path not in (prefix + "/jobs", prefix + "/shutdown"):
            return self._error(404, "not_found", "Operation not found")
        try:
            if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
                raise ValueError("Expected application/json with Content-Length")
            lengths = self.headers.get_all("Content-Length", [])
            size = int(lengths[0]) if len(lengths) == 1 else -1
            if not 1 <= size <= 4096:
                raise ValueError("Request body exceeds bounds or is absent")
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError("Incomplete request")
            payload = json.loads(body)
            if self.path == prefix + "/shutdown":
                if payload != {}:
                    raise ValueError("Shutdown expects an empty JSON object")
                with svc.changed:
                    svc.shutdown()
                    return self._json(202, {"state": "stopping" if platform else "STOPPING"})
            if platform:
                request, budget = dw.submission(payload, parse_job)
                job = svc.submit(request, budget=budget)
                return self._json(202, dw.job_view(job), location=f"{prefix}/jobs/{job['id']}")
            return self._json(202, svc.submit(parse_job(payload)))
        except Unavailable as exc:
            return self._error(503, "instrument_unavailable", str(exc))
        except Unsupported as exc:
            return self._error(409, "operation_conflict", str(exc))
        except (ValueError, UnicodeError, TimeoutError, RecursionError):
            return self._error(422 if platform else 400, "invalid_arguments", "Invalid operation or request body")


def write_discovery(path, settings):
    """Restrict the new file before writing the bearer credential."""
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        if sys.platform == "win32":
            sid = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                  "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value"],
                                 check=True, capture_output=True, text=True, timeout=10).stdout.strip()
            if not re.fullmatch(r"S-1-(?:\d+-)*\d+", sid):
                raise RuntimeError("Could not identify the discovery-file owner")
            subprocess.run(["icacls.exe", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(F)"],
                           check=True, capture_output=True, timeout=10)
        json.dump(settings, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True,
                        help="Explicitly opt into instrument ownership on the retained Workbench scope")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--poll-interval", type=float, default=.25,
                        help="Seconds between finished poll and the next item (minimum .25)")
    parser.add_argument("--stale-after", type=float, default=45.)
    parser.add_argument("--measurements", default="mean,minimum,maximum,vpp,frequency,period")
    parser.add_argument("--channels", choices=("CH1", "CH2", "CH1,CH2"), default="CH1,CH2")
    parser.add_argument("--telemetry-source", choices=("s22", "waveform", "both"), default="s22")
    parser.add_argument("--settings-every", type=float, help="Independent S81 refresh period in seconds")
    parser.add_argument("--waveform-every", type=float, default=1., help="Minimum waveform poll period in seconds")
    parser.add_argument("--timing", choices=("conservative", "validated-quiet"), default="conservative")
    parser.add_argument("--instrument-id", default="hantek-dso5102p", help="Stable local instrument identifier")
    parser.add_argument("--physical-probes", choices=("10,1",), help="Operator-confirmed fixture probe factors")
    parser.add_argument("--configured-slots-only", action="store_true", help="Skip S22 items absent from S81 slots")
    parser.add_argument("--priority", default="", help="Comma-separated S22 items to prefer within each rotation")
    parser.add_argument("--detailed-monitor-log", action="store_true", help="Retain every monitor exchange")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("Live service requires the verified Windows WinUSB profile")
    selection = args.measurements.split(",")
    allowed = ITEMS if args.telemetry_source == "s22" else WAVE_ITEMS
    if not set(selection) <= allowed.keys() or len(selection) != len(set(selection)):
        parser.error("Choose unique measurement names from " + ", ".join(allowed))
    if args.telemetry_source == "both" and not set(selection) & ITEMS.keys():
        parser.error("Combined telemetry requires at least one S22 measurement")
    priority = args.priority.split(",") if args.priority else []
    if not set(priority) <= set(selection) & ITEMS.keys():
        parser.error("Prioritize only selected S22 measurement names")
    if not .25 <= args.poll_interval <= 60 or not 1 <= args.stale_after <= 600 or not 0 <= args.port <= 65535:
        parser.error("Invalid interval, stale-age threshold, or port")
    if ((args.settings_every is not None and not 1 <= args.settings_every <= 600)
            or not .5 <= args.waveform_every <= 600
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", args.instrument_id)):
        parser.error("Invalid settings period, waveform period, or instrument ID")
    source = _source_commit(args.source_commit)
    folder = args.evidence.resolve()
    if folder.drive.startswith("\\\\"):
        parser.error("Use a private local evidence directory")
    folder.mkdir()
    settings_every = args.settings_every
    if settings_every is None and args.telemetry_source != "s22":
        settings_every = 10.
    svc = Service(folder, interval=args.poll_interval, stale_after=args.stale_after, measurements=selection,
                  channels=tuple(args.channels.split(",")), source=args.telemetry_source,
                  settings_every=settings_every, waveform_every=args.waveform_every,
                  instrument_id=args.instrument_id, rapid_reads=args.timing == "validated-quiet",
                  physical_probes={"CH1": 10, "CH2": 1} if args.physical_probes else None,
                  detailed=args.detailed_monitor_log, configured_slots_only=args.configured_slots_only,
                  priority=priority)
    token = secrets.token_urlsafe(32)
    with LocalServer(svc, token, args.port) as server:
        write_discovery(folder / "client.json", {"endpoint": f"http://127.0.0.1:{server.server_port}", "token": token})
        worker = Thread(target=svc.worker, args=(lambda service: owned(service, source, marker=state_path()),))
        worker.start()
        print(f"Loopback API ready. Private client configuration is in {folder / 'client.json'}", flush=True)
        try:
            while not (svc.status()["stopping"] and svc.status()["worker_done"]):
                server.handle_request()
        except KeyboardInterrupt:
            svc.shutdown()
        finally:
            svc.shutdown()
            worker.join()
        return 4 if svc.status()["state"] == "FAULT" else 0


if __name__ == "__main__":
    raise SystemExit(main())
