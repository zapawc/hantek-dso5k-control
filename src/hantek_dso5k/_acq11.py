"""Authorized Workbench acquisition for the retained DSO5102P probe fixture."""

import argparse
import json
import math
import os
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

from ._framing import STATUS, frame, read_frames
from ._host import assess_host, finalization_guard, host_snapshot, lease, require_no_finalization
from ._native_finalization import create_record, prepare, publish
from ._pair_windows import _claim_pair, _source_commit
from ._retrieve import file_record, record_cleanup
from ._s92_guard import observe_qualification_guard, qualification_guard_result
from ._session import state_path, utc
from ._settings import compare_settings
from .native_waveform import decode_native_waveform


# These S81 indices have been compared with the archived 208-byte layout.
VOLTS = (.001, .002, .005, .01, .02, .05, .1, .2, .5, 1., 2., 5., 10.)
PROBES = (1, 10, 100, 1000)
TIMEBASE = tuple(round(m * 10. ** e, 12) for e in range(-9, 2)
                 for m in (2, 4, 8) if m * 10. ** e <= 40)
WRITABLE = {1: (1, 12), 5: (0, 3), 11: (1, 12), 15: (0, 3)}
RUN_STOP, SINGLE = 19, 18
COUNTS_PER_DIVISION = 25.0


def describe(raw):
    if len(raw) != 208:
        raise ValueError("Expected the retained 208-byte S81 layout")
    channels = {}
    for name, start in (("CH1", 0), ("CH2", 10)):
        if raw[start + 1] >= len(VOLTS) or raw[start + 5] >= len(PROBES):
            raise ValueError("Unknown voltage-scale or probe index")
        channels[name] = {
            "enabled": raw[start] == 1, "coupling_code": raw[start + 2],
            "bnc_v_per_div": VOLTS[raw[start + 1]], "software_probe": PROBES[raw[start + 5]],
            "vertical_position_counts": int.from_bytes(raw[start + 8:start + 10], "little", signed=True),
        }
    if raw[160] >= len(TIMEBASE):
        raise ValueError("Unknown timebase index")
    return {
        "channels": channels, "trigger_state_code": raw[20], "trigger_type_code": raw[21],
        "trigger_source_code": raw[22], "trigger_mode_code": raw[23], "trigger_coupling_code": raw[24],
        "trigger_position_counts": int.from_bytes(raw[25:27], "little", signed=True),
        "cached_trigger_frequency_hz": int.from_bytes(raw[27:35], "little") / 1000,
        "trigger_slope_code": raw[59], "seconds_per_division": TIMEBASE[raw[160]],
        "timebase_index": raw[160], "math_enabled": raw[171] != 0,
        "display_format_code": raw[179], "acquisition_mode_code": raw[185],
        "memory_depth_code": raw[188], "display_menu_id": raw[206], "menu_visible": raw[207] == 1,
        "measurement_slots": [list(raw[i:i + 2]) for i in range(189, 205, 2)],
    }


def patch_settings(raw, edits):
    info = describe(raw)
    if info["memory_depth_code"] not in (0, 2):
        raise ValueError("Settings update requires a known ordinary dual-channel memory depth")
    modified = bytearray(raw)
    for field, value in edits.items():
        if field not in WRITABLE or type(value) is not int:
            raise ValueError("Settings update is limited to probe and scale indices")
        low, high = WRITABLE[field]
        if not low <= value <= high:
            raise ValueError("Settings index out of bounds")
        modified[field] = value
    return bytes(modified)


def analyze(samples, *, bncvdiv, probe, offset, interval):
    """Report display-code derived voltage and edge statistics on a relative time axis."""
    values = [(sample - offset) * bncvdiv * probe / COUNTS_PER_DIVISION for sample in samples]
    low, high = min(values), max(values)
    limit = (low + high) / 2
    rising, falling = [], []
    for i, (a, b) in enumerate(zip(values, values[1:])):
        if a < limit <= b:
            rising.append((i + (limit - a) / (b - a)) * interval)
        elif a >= limit > b:
            falling.append((i + (limit - a) / (b - a)) * interval)
    cycles = [(a, b) for a, b in zip(rising, rising[1:])]
    periods = [b - a for a, b in cycles]
    period = sum(periods) / len(periods) if periods else None
    duties = [(f - a) / (b - a) for a, b in cycles for f in falling if a < f < b]
    return values, {
        "samples": len(samples), "interval_s": interval, "min_v": low, "max_v": high,
        "mean_v": sum(values) / len(values), "vpp_v": high - low, "bnc_vpp_v": (high - low) / probe,
        "period_s": period, "frequency_hz": 1 / period if period else None,
        "duty_cycle": sum(duties) / len(duties) if duties else None,
        "rising_edges_s": rising, "falling_edges_s": falling,
        "clipped": min(samples) <= -127 or max(samples) >= 127,
    }


class Session:
    def __init__(self, link, evidence, journal, clock=perf_counter):
        self.link, self.evidence, self.journal, self.clock = link, evidence, journal, clock
        self.count = 0
        self.settled = True

    def log(self, event, **fields):
        self.journal.write(json.dumps({"event": event, "utc": utc(), "monotonic": self.clock(),
                                       **fields}) + "\n")
        self.journal.flush()

    def startup(self):
        self.settled = False
        report = qualification_guard_result()
        with (self.evidence / "startup_rx.bin").open("xb") as stream:
            observe_qualification_guard(self.link, self.clock, stream, self.log, report)
        self.settled = report["complete"]

    def exchange(self, name, request, opcode, *, duration=1.1, limit=1024, waveform=False):
        if not self.settled:
            raise RuntimeError("Previous command has unresolved history")
        stem = self.evidence / f"{self.count + 1:03d}-{name}"
        stem.with_suffix(".tx").write_bytes(request)
        self.count += 1
        self.settled = False
        self.log("write_attempt", command=name, bytes=request.hex(" "))
        with stem.with_suffix(".rx").open("xb") as stream:
            written = self.link.write(request)
            started = self.clock()
            if written != len(request):
                raise RuntimeError(f"Short {name} write")
            deadline = started + duration
            last_byte = started
            while (now := self.clock()) < deadline:
                try:
                    received = self.link.read(16384, timeout_ms=max(1, min(250, math.ceil((deadline - now) * 1000))))
                except TimeoutError:
                    continue
                completed = self.clock()
                stream.write(received)
                self.log("receive", command=name, bytes=len(received), completed=completed)
                if not received or stream.tell() > limit or completed > deadline:
                    raise RuntimeError(f"{name} receive bound exceeded")
                last_byte = completed
        raw = stem.with_suffix(".rx").read_bytes()
        primary, statuses = [], []
        for offset, kind, body in read_frames([raw]):
            if kind == 0x92 and body == STATUS[4:-1]:
                statuses.append(offset)
            elif kind == opcode:
                primary.append(body)
            else:
                raise RuntimeError(f"{name} received unexpected opcode or S92 shape at {offset}")
        if deadline - last_byte < 0.55:
            raise RuntimeError(f"{name} lacked a 550 ms quiet response tail")
        if waveform:
            decode_native_waveform([raw], selector=4)
        elif len(primary) != 1:
            raise RuntimeError(f"{name} expected exactly one primary response")
        self.log("settled_response", command=name, status_offsets=statuses, primary_frames=len(primary))
        self.settled = True
        return raw if waveform else primary[0]

    def settings(self, name):
        data = self.exchange(name, frame(1), 0x81)
        info = describe(data)
        create_record(self.evidence / f"{self.count:03d}-{name}.json", info)
        return data

    def set_fields(self, name, current, edits):
        edits = {offset: value for offset, value in edits.items() if current[offset] != value}
        if not edits:
            return current
        payload = patch_settings(current, edits)
        response = self.exchange(name, frame(0x11, payload), 0x91, duration=1.8)
        if response != b"\x00":
            raise RuntimeError(f"{name} returned non-success S91 payload {response.hex()}")
        observed = self.settings(name + "_readback")
        if any(observed[offset] != value for offset, value in edits.items()):
            raise RuntimeError(f"{name} settings readback differs from requested fields")
        return observed

    def key(self, name, key):
        if key not in (RUN_STOP, SINGLE):
            raise ValueError("Only the Run/Stop and SINGLE keys are enabled")
        ack = self.exchange(name, frame(0x13, bytes([key, 1])), 0x93, duration=1.4)
        if len(ack) != 1:
            raise RuntimeError("Unexpected S93 acknowledgment shape")

    def measurement(self, item, channel):
        body = self.exchange(f"measure_{channel + 1}_{item}", frame(0x22, bytes([item, channel, 0])), 0xA2)
        if len(body) > 32:
            raise RuntimeError("Measurement response exceeds display-text size")
        return body.rstrip(b"\x00").decode("ascii")

    def capture(self, name, snapshot):
        raw = self.exchange(name, frame(2, b"\x01\x04"), 0x82, duration=3., limit=32768, waveform=True)
        result = decode_native_waveform([raw], selector=4)
        info = describe(snapshot)
        channels = {}
        for slot in result.channels:
            if slot.channel == "MATH" or slot.samples is None:
                continue
            setup = info["channels"][slot.channel]
            # 800 px across the display, with the 160 px side menu taking four grid divisions.
            divisions = 16. if info["menu_visible"] else 19.2
            interval = info["seconds_per_division"] * divisions / len(slot.samples)
            values, metrics = analyze(slot.samples, bncvdiv=setup["bnc_v_per_div"], probe=setup["software_probe"],
                                      offset=setup["vertical_position_counts"], interval=interval)
            channels[slot.channel] = metrics
            with (self.evidence / f"{name}_{slot.channel}.csv").open("x", encoding="utf-8") as csv:
                csv.write("relative_time_s,raw_display_code,voltage_v\n")
                csv.writelines(f"{i * interval:.12g},{sample},{value:.9g}\n"
                               for i, (sample, value) in enumerate(zip(slot.samples, values)))
        capture = {"status_offsets": list(result.status_offsets), "terminal": result.terminal_seen,
                   "availability": {c.channel: c.samples is not None for c in result.channels},
                   "channels": channels, "time_origin": "relative sample zero", "same_shot": "UNPROVEN",
                   "counts_per_division": COUNTS_PER_DIVISION,
                   "scale_provenance": "Documented display-code gain and S81 offset; absolute voltage unverified"}
        create_record(self.evidence / f"{name}.json", capture)
        return capture


def _running(state):
    if state[20] not in (0, 1, 2, 3, 5):
        raise RuntimeError("Unrecognized acquisition state")
    return state[20] not in (0, 5)


def _periodic_pair(capture):
    channels = capture["channels"]
    return (set(channels) == {"CH1", "CH2"} and not capture["availability"]["MATH"]
            and all(not ch["clipped"] and ch["frequency_hz"] is not None
                    and 750 <= ch["frequency_hz"] <= 1250 for ch in channels.values()))


def _fixture_evidence(capture):
    if not _periodic_pair(capture):
        return {"periodic_pair": False, "voltage": "UNVERIFIED", "attenuation_ratio": None}
    first, second = (capture["channels"][channel] for channel in ("CH1", "CH2"))
    ratio = second["bnc_vpp_v"] / first["bnc_vpp_v"]
    return {"periodic_pair": True, "attenuation_ratio": ratio,
            "probe_assignment_plausible": 6. <= ratio <= 14.,
            "voltage": "NOMINAL_PLAUSIBLE" if all(3. <= ch["vpp_v"] <= 7. for ch in (first, second))
            else "OUTSIDE_NOMINAL_RANGE"}


def _scope_volts(text):
    for suffix, scale in (("mV", .001), ("V", 1.)):
        if text.endswith(suffix):
            try:
                value = float(text[:-len(suffix)])
            except ValueError:
                return None
            return value * scale if math.isfinite(value) else None
    return None


def workflow(session, mode, outcome):
    baseline = session.settings("initial")
    outcome["initial"] = describe(baseline)
    if mode == "state":
        return
    if not all(baseline[i] == value for i, value in (
            (0, 1), (2, 0), (10, 1), (12, 0), (21, 0), (22, 0), (59, 0), (171, 0), (185, 0))):
        raise RuntimeError("Initial channel, trigger or acquisition settings differ from the reviewed fixture")
    was_running = _running(baseline)
    changed = False
    try:
        changed = True
        configured = session.set_fields("fixture_scale", baseline, {1: 7, 5: 1, 11: 9, 15: 0})
        if not _running(configured):
            session.key("start_running", RUN_STOP)
        running = session.settings("running")
        if not _running(running):
            raise RuntimeError("Scope did not report running after Run/Stop")
        outcome["modified"] = describe(running)
        outcome["running"] = session.capture("running_waveform", running)
        outcome["running_validation"] = _fixture_evidence(outcome["running"])
        outcome["scope_measurements"] = {
            ch: {str(item): session.measurement(item, index) for item in (1, 4)}
            for index, ch in enumerate(("CH1", "CH2"))
        }
        if not outcome["running_validation"]["periodic_pair"]:
            raise RuntimeError("Running waveform failed dual-channel periodicity and clipping checks")
        scope_vpp = _scope_volts(outcome["scope_measurements"]["CH1"]["4"])
        if scope_vpp is not None:
            native_vpp = outcome["running"]["channels"]["CH1"]["vpp_v"]
            outcome["ch1_vpp_crosscheck"] = math.isclose(native_vpp, scope_vpp, rel_tol=0.15)
        else:
            outcome["ch1_vpp_crosscheck"] = False
        if mode == "full":
            session.key("arm_single", SINGLE)
            for attempt in range(1, 5):
                stopped = session.settings(f"single_state_{attempt}")
                if not _running(stopped):
                    outcome["single_stop_observed"] = True
                    outcome["single"] = session.capture("single_waveform", stopped)
                    outcome["single_validation"] = _fixture_evidence(outcome["single"])
                    break
            if not outcome.get("single") or not _periodic_pair(outcome["single"]):
                raise RuntimeError("SINGLE stopped waveform unavailable or failed periodicity checks")
        if not outcome["running_validation"]["probe_assignment_plausible"] or (
                mode == "full" and not outcome["single_validation"]["probe_assignment_plausible"]):
            raise RuntimeError("Measured channel ratio does not support the expected probe assignment")
    finally:
        if changed and session.settled:
            current = session.settings("before_restore")
            current = session.set_fields("restore_scale", current, {i: baseline[i] for i in WRITABLE})
            if _running(current) != was_running:
                session.key("restore_run_state", RUN_STOP)
            restored = session.settings("restored")
            outcome["restored"] = describe(restored)
            comparison = compare_settings(baseline, restored)
            outcome["restoration_comparison"] = comparison
            outcome["restoration_verified"] = (not comparison["configuration_or_unclassified_changes"]
                                                and _running(restored) == was_running)
            if not outcome["restoration_verified"]:
                raise RuntimeError("Restoration readback has configuration differences")


def run(evidence, source, mode, *, hold, claim, screen, marker):
    evidence = Path(evidence).resolve()
    guard = finalization_guard(marker)
    evidence.mkdir()
    outcome = {"session_id": uuid4().hex, "source_commit": source, "mode": mode,
               "status": "BLOCKED", "acquisition_outcome": "NOT_COMPLETED", "finalization": "NOT_STARTED",
               "marker": "NOT_CHECKED", "admission_guard": "NOT_CHECKED", "request_attempts": 0,
               "cleanup": {"lease_released": False, "interface_released": False}, "errors": []}
    identity = None
    stage = "ownership"
    session = None
    try:
        with record_cleanup(hold(), outcome["cleanup"], "lease_released"):
            require_no_finalization(marker)
            if os.path.lexists(marker):
                raise RuntimeError("Unresolved interruption marker")
            host = screen()
            if host["blocked"]:
                raise RuntimeError(host["blocked"])
            marker.parent.mkdir(parents=True, exist_ok=True)
            if evidence.stat().st_dev != marker.parent.stat().st_dev:
                raise RuntimeError("Evidence must share the interruption-state volume")
            marker_id = create_record(marker, {"session_id": outcome["session_id"], "operation": "ACQ-11",
                                               "evidence": str(evidence), "source_commit": source, "utc": utc()})
            outcome.update(status="FAIL", marker="RETAINED", admission_guard="ABSENT")
            stage = "session"
            with (evidence / "journal.jsonl").open("x", encoding="utf-8") as journal:
                try:
                    with record_cleanup(claim(), outcome["cleanup"], "interface_released") as link:
                        session = Session(link, evidence, journal)
                        session.startup()
                        workflow(session, mode, outcome)
                except (Exception, KeyboardInterrupt) as exc:
                    outcome["errors"].append({"stage": stage, "detail": str(exc), "type": type(exc).__name__})
                    outcome["acquisition_outcome"] = "FAIL"
                else:
                    outcome["acquisition_outcome"] = "PASS"
                if session is not None:
                    outcome["request_attempts"] = session.count
                outcome["history_settled"] = bool(session and session.settled)
            if (session and session.settled and outcome["cleanup"]["interface_released"]
                    and (mode == "state" and "initial" in outcome or outcome.get("restoration_verified"))):
                stage = "prepare"
                identity = prepare(evidence, marker, marker_id, guard, outcome)
            stage = "lease_release"
        if identity is not None and outcome["cleanup"]["lease_released"]:
            stage = "publish"
            publish(evidence, marker, guard, identity, outcome)
            return outcome
    except (Exception, KeyboardInterrupt) as exc:
        outcome["errors"].append({"stage": stage, "detail": str(exc), "type": type(exc).__name__})
        if outcome["finalization"] == "PENDING":
            outcome["finalization"] = "FAILED"
    if "artifacts" not in outcome:
        outcome["artifacts"] = {p.name: file_record(p) for p in evidence.iterdir() if p.is_file()}
    if not (evidence / "native_manifest.json").exists():
        create_record(evidence / "native_manifest.json", outcome)
    return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("state", "running", "full"))
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--authorization", choices=("ACQ-11",), required=True)
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("ACQ-11 instrument access requires Windows")
    source = os.environ.get("HANTEK_ACQ11_SOURCE")
    if not source:
        parser.error("Set HANTEK_ACQ11_SOURCE to the reviewed clean worktree commit")
    _source_commit(source)
    result = run(args.evidence, source, args.mode, hold=lease, claim=_claim_pair,
                 screen=lambda: assess_host(host_snapshot(), os.getpid(), (sys.executable, sys._base_executable)),
                 marker=state_path())
    print(json.dumps({key: value for key, value in result.items() if key != "artifacts"}, indent=2))
    return {"PASS": 0, "FAIL": 4, "BLOCKED": 3}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
