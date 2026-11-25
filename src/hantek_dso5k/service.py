"""Local, cooperative WinUSB instrument service for the retained DSO5102P."""

import argparse
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
import sys
from threading import Condition, Thread
from time import monotonic
from uuid import uuid4

from ._acq11 import Session, describe
from ._host import assess_host, finalization_guard, host_snapshot, lease, require_no_finalization
from ._native_finalization import create_record, prepare, publish
from ._pair_windows import _claim_pair, _source_commit
from ._retrieve import file_record, record_cleanup
from ._service_capture import Unsupported, instrument, s22_probe, set_run_state, snapshot, triggered
from ._session import state_path


ITEMS = {"mean": (3, "V"), "minimum": (6, "V"), "maximum": (7, "V"),
         "vpp": (4, "V"), "frequency": (1, "Hz"), "period": (2, "s")}
CHANNELS = ("CH1", "CH2")
DISPLAY = re.compile(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([pnumkKMµ]?)\s*(V|Hz|hz|s)")
PREFIX = {"": 1., "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6,
          "m": .001, "k": 1000., "K": 1000., "M": 1e6}
STATUS_SCHEMA = "hantek.service.v1"
JOB_ID = re.compile(r"[0-9a-f]{32}")


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
    def __init__(self, evidence, *, interval, stale_after, measurements, clock=monotonic):
        self.evidence = Path(evidence)
        self.interval, self.stale_after, self.clock = interval, stale_after, clock
        self.sensors = [(ch, name) for name in measurements for ch in CHANNELS]
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

    def latest(self):
        with self.changed:
            now = self.clock()
            results = []
            for original in self.cache.values():
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

    def submit(self, request):
        with self.changed:
            if self.stopping or self.state != "MONITORING" or self.pending is not None:
                raise Unsupported("Worker is not available for a new operation")
            if len(self.jobs) == 64:
                del self.jobs[next(iter(self.jobs))]
            job_id = uuid4().hex
            job = {"id": job_id, "request": request, "state": "QUEUED", "queued_at": utc()}
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

    def _poll(self, session, index):
        if index == 0:
            self._settings(session.settings("telemetry_settings"))
        channel, kind = self.sensors[index]
        settings = self.current_settings()
        code, units = ITEMS[kind]
        started = self.clock()
        reason, value, raw = "channel_disabled", None, None
        if settings["channels"][channel]["enabled"]:
            raw = session.measurement(code, CHANNELS.index(channel))
            value, reason = measurement_value(raw, units)
            if kind == "vpp" and value is not None and value < 0:
                value, reason = None, "invalid_display_value"
        now = self.clock()
        configuration = {key: settings[key] for key in ("s81_sha256", "stable_sha256", "observed_at")}
        configuration["channel"] = settings["channels"][channel]
        row = {"channel": channel, "measurement": kind, "unit": units, "value": value, "raw": raw,
               "source": "s22_instrument_cache", "device_freshness": "UNKNOWN",
               "acquisition_state_code": settings["trigger_state_code"], "available": value is not None,
               "validity": "display_value_only" if value is not None else "unavailable",
               "error": reason, "observed_at": utc(), "received_monotonic": now,
               "configuration": configuration, "observation_s": now - started}
        key = channel, kind
        with self.changed:
            prior = self.cache.get(key, {})
            row["last_good"] = ({field: row[field] for field in
                                 ("value", "observed_at", "received_monotonic", "configuration")}
                                if value is not None else prior.get("last_good"))
            self.cache[key] = row
            self.poll_count += 1
        session.log("telemetry", channel=channel, measurement=kind, available=row["available"],
                    observation_s=row["observation_s"])

    def _requires_restore(self, needed):
        self.restoration_owed = needed

    def _perform(self, session, job_id):
        request = self.job(job_id)["request"]
        root = session.evidence
        dest = root / job_id
        dest.mkdir()
        session.evidence = dest
        result = {"same_shot": "UNPROVEN", "kind": request["kind"]}
        error = None
        fatal = None
        started = self.clock()
        try:
            baseline = session.settings("job_initial")
            result["initial"] = describe(baseline)
            if request["kind"] == "snapshot":
                result["native"] = snapshot(session, baseline, job_id)
            elif request["kind"] == "triggered":
                triggered(session, baseline, job_id, request, result, self._requires_restore)
            elif request["kind"] == "run-state":
                set_run_state(session, baseline, request, result, self._requires_restore)
            else:
                s22_probe(session, baseline, request, result, self._requires_restore)
        except (Exception, KeyboardInterrupt) as exc:
            error = {"type": type(exc).__name__, "detail": str(exc)}
            if self.restoration_owed or not session.settled:
                fatal = exc
        finally:
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
                self._poll(session, index)
                index = (index + 1) % len(self.sensors)
                next_poll = self.clock() + self.interval
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
        if set(payload) - {"kind", "configure_fixture"} or type(configure) is not bool:
            raise ValueError("Expected optional boolean configure_fixture")
        return {"kind": kind, "configure_fixture": configure}
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
        super().__init__(("127.0.0.1", port), LocalHandler)

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(5)
        return sock, address


class LocalHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send(self, code, body, content_type="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, value):
        self._send(code, json.dumps(value, allow_nan=False).encode("utf-8"))

    def _authorized(self):
        expected = ("Bearer " + self.server.token).encode()
        if compare_digest(self.headers.get("Authorization", "").encode(), expected):
            return True
        self._json(401, {"error": "Bearer token required"})
        return False

    def do_GET(self):
        if not self._authorized():
            return
        svc = self.server.service
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
                    try:
                        with (svc.evidence / parts[3] / name).open("rb") as stream:
                            data = stream.read(entry["bytes"] + 1)
                    except OSError:
                        return self._json(409, {"error": "Recorded artifact unavailable"})
                    if len(data) != entry["bytes"] or sha256(data).hexdigest() != entry["sha256"]:
                        return self._json(409, {"error": "Recorded artifact integrity mismatch"})
                    return self._send(200, data, "text/csv" if name.endswith(".csv") else "application/json")
        self._json(404, {"error": "Resource not found"})

    def do_POST(self):
        if not self._authorized():
            return
        try:
            if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
                raise ValueError("Expected application/json with Content-Length")
            size = int(self.headers.get("Content-Length", "-1"))
            if not 1 <= size <= 4096:
                raise ValueError("Request body exceeds bounds or is absent")
            payload = json.loads(self.rfile.read(size))
            if self.path == "/v1/shutdown" and payload == {}:
                self._json(202, {"state": "STOPPING"})
                self.server.service.shutdown()
                return
            if self.path == "/v1/jobs":
                return self._json(202, self.server.service.submit(parse_job(payload)))
        except Unsupported as exc:
            return self._json(409, {"error": str(exc)})
        except (ValueError, UnicodeError) as exc:
            return self._json(400, {"error": str(exc)})
        self._json(404, {"error": "Operation not found"})


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
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("Live service requires the verified Windows WinUSB profile")
    selection = args.measurements.split(",")
    if not set(selection) <= ITEMS.keys() or len(selection) != len(set(selection)):
        parser.error("Choose unique measurement names from " + ", ".join(ITEMS))
    if not .25 <= args.poll_interval <= 60 or not 1 <= args.stale_after <= 600 or not 0 <= args.port <= 65535:
        parser.error("Invalid interval, stale-age threshold, or port")
    source = _source_commit(args.source_commit)
    folder = args.evidence.resolve()
    if folder.drive.startswith("\\\\"):
        parser.error("Use a private local evidence directory")
    folder.mkdir()
    svc = Service(folder, interval=args.poll_interval, stale_after=args.stale_after, measurements=selection)
    token = secrets.token_urlsafe(32)
    with LocalServer(svc, token, args.port) as server:
        create_record(folder / "client.json", {"endpoint": f"http://127.0.0.1:{server.server_port}", "token": token})
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
