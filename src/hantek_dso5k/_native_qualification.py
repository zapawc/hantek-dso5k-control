"""Isolated, supervised ACQ-10H candidate. Imports and tests never access USB."""

import argparse
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shutil
import sys
from time import perf_counter
from uuid import uuid4

from ._graphics import READ_BYTES
from ._host import Blocked, assess_host, finalization_guard, host_snapshot, lease, require_no_finalization
from ._native_finalization import create_record, prepare, publish
from ._pair_windows import _claim_pair, _source_commit
from ._receive import PACKET_BYTES, ReceiveError, ReceiveStream
from ._retrieve import file_record, record_cleanup
from ._screens import write_json_new
from ._session import state_path, utc
from .acquisition import _engineering, _positive, build_native_result


REQUEST = bytes.fromhex("53 04 00 02 01 04 5E")
WINDOW = 12
MAX_RX = 32768
MAX_FRAMES = 256
MAX_SAMPLES = 10000
MODEL = "DSO5102P"
FIRMWARE = "3.40.0 (151009.0)"
CHANNELS = ("CH1", "CH2")
EXIT = {"PASS": 0, "PASS_WITH_STATUS": 0, "FAIL": 4, "BLOCKED": 3}


class ObservationError(RuntimeError):
    """The transport or observation budget made further reads unsafe."""


def _fixture(raw):
    config = json.loads(raw)
    if not isinstance(config, dict) or not isinstance(config.get("setup"), str) or not config["setup"].strip():
        raise ValueError("Fixture requires a recorded setup")
    if config.get("running") is not True or config.get("math_enabled") is not False:
        raise ValueError("Fixture requires running acquisition and MATH off")
    expectations = config.get("expectations")
    scaling = config.get("channel_scaling")
    if not isinstance(expectations, dict) or not isinstance(scaling, dict):
        raise ValueError("Fixture requires per-channel expectations and scaling")
    if set(expectations) != set(CHANNELS) or set(scaling) != set(CHANNELS):
        raise ValueError("Fixture requires exactly CH1 and CH2")
    for ch in CHANNELS:
        scale = scaling[ch]
        wanted = expectations[ch]
        if not isinstance(wanted, dict) or not isinstance(wanted.get("reference"), str) or not wanted["reference"].strip():
            raise ValueError(f"{ch} requires an independent reference")
        for name in ("vpp_v", "period_s"):
            interval = wanted.get(name)
            if not isinstance(interval, list) or len(interval) != 2 or not all(map(_positive, interval)):
                raise ValueError(f"{ch} requires a positive {name} interval")
            if interval[0] >= interval[1]:
                raise ValueError(f"{ch} requires ordered {name} limits")
        count = scale.get("sample_count") if isinstance(scale, dict) else None
        viewport = scale.get("viewport") if isinstance(scale, dict) else None
        if (type(count) is not int or not 1 <= count <= MAX_SAMPLES
                or not isinstance(viewport, str) or not viewport.strip()):
            raise ValueError(f"{ch} requires an expected count and viewport")
        engineering, reason = _engineering((0,) * count, scale)
        if engineering is None:
            raise ValueError(f"{ch} fixture {reason}")
    first, second = sorted(expectations[ch]["vpp_v"] for ch in CHANNELS)
    if first[1] >= second[0]:
        raise ValueError("Use disjoint expected CH1 and CH2 voltage ranges to witness attribution")
    json.dumps(config, allow_nan=False)
    return config


def _window(link, clock, started, raw, log, result, *, silent=False):
    """Keep bounded raw capture after classification fails. Never scan for a new boundary."""
    deadline = started + WINDOW
    result.update(start=started, deadline=deadline, raw_bytes=0, frames=0, statuses=[],
                  complete=False, classification_error=None, first=None, end=None)
    if not silent:
        def classify(event, **fields):
            if event in ("primary_frame", "recognized_status"):
                result["frames"] += 1
                if result["frames"] > MAX_FRAMES:
                    raise ObservationError("frame budget exceeded")
            if event == "recognized_status":
                result["statuses"].append(fields)
            if (event == "primary_frame" and fields["subtype"] == 0 and fields["wire_bytes"] == 9
                    and int.from_bytes(stream.pending[5:8], "little") > MAX_SAMPLES):
                raise ObservationError("declared per-slot sample bound exceeded")
            log(event, **fields)

        stream = ReceiveStream(classify, {"recognized_status_frames": 0, "primary_frames_seen": 0},
                               primary_opcode=0x82, max_frame_bytes=MAX_SAMPLES + 7)
    log("window_started", deadline=deadline, silent=silent)
    poll = 0
    completed = started
    while (now := clock()) < deadline:
        capacity = min(READ_BYTES, (MAX_RX - result["raw_bytes"]) // PACKET_BYTES * PACKET_BYTES)
        if capacity <= 0:
            raise ObservationError("RX budget exhausted for aligned read")
        remaining_ms = math.ceil((deadline - now) * 1000)
        timeout = min(250, remaining_ms)
        poll += 1
        try:
            data = link.read(capacity, timeout_ms=timeout)
        except TimeoutError:
            completed = clock()
            log("poll_timeout", poll=poll, started=now, completed=completed, timeout_ms=timeout)
            # Millisecond API quantization may put a no-data final timeout just after the deadline.
            if completed > deadline + 0.001:
                raise ObservationError("read timeout exceeded the absolute observation deadline")
            continue
        completed = clock()
        stamp = utc()
        offset = result["raw_bytes"]
        raw.write(data)
        raw.flush()
        result["raw_bytes"] += len(data)
        if result["first"] is None:
            result["first"] = {"utc": stamp, "monotonic_s": completed}
        log("receive", poll=poll, started=now, completed=completed, raw_offset=offset,
            capture_utc=stamp, length=len(data), requested=capacity, timeout_ms=timeout)
        if not data or len(data) > capacity or completed > deadline:
            raise ObservationError("invalid read size or data outside the absolute deadline")
        if silent:
            raise ObservationError("traffic during strict startup silence")
        if result["classification_error"] is None:
            try:
                stream.feed(data, poll, completed, stamp)
            except ReceiveError as exc:
                result["classification_error"] = str(exc)
    if completed < deadline - 0.001:
        raise ObservationError("observation ended without a final receive poll through the deadline")
    if not silent and result["classification_error"] is None:
        try:
            stream.finish()
        except ReceiveError as exc:
            result["classification_error"] = str(exc)
    result["end"] = {"utc": utc(), "monotonic_s": clock()}
    result["complete"] = True
    log("window_completed", raw_bytes=result["raw_bytes"], frames=result["frames"])


def _measure(engineering, limits):
    values = engineering["values"]
    low, high = min(values), max(values)
    vpp = high - low
    if not math.isfinite(vpp):
        return {"vpp_v": None, "periods_s": [], "voltage_in_range": False, "period_in_range": False}
    midpoint = low + vpp / 2
    rises = [i + (midpoint - a) / (b - a)
             for i, (a, b) in enumerate(zip(values, values[1:])) if a < midpoint <= b]
    periods = [(b - a) * engineering["sample_interval_s"] for a, b in zip(rises, rises[1:])]
    volts = limits["vpp_v"][0] <= vpp <= limits["vpp_v"][1]
    timing = bool(periods) and all(limits["period_s"][0] <= value <= limits["period_s"][1]
                                   for value in periods)
    return {"vpp_v": vpp, "periods_s": periods, "voltage_in_range": volts, "period_in_range": timing}


def _validate_response(path, result, config, evidence):
    observation = result["response"]
    recorded = file_record(path)
    artifact = {"role": "raw_rx", "locator": path.name, **recorded}
    acquisition = build_native_result(
        [path.read_bytes()], selector=4, run_id=evidence.name,
        instrument={"model": MODEL, "firmware": FIRMWARE, "provenance": result["identity_provenance"]},
        timing={"clock_context": f"perf_counter and host UTC for run {evidence.name}",
                "request_write": result["write_completed"], "receive_first": observation["first"],
                "receive_end": observation["end"]},
        configuration_snapshot=config, artifacts=[artifact],
    )
    write_json_new(evidence / "acquisition.json", acquisition)
    result["wire_integrity"] = acquisition["transport_integrity"]
    if observation["classification_error"] or not acquisition["terminal_seen"]:
        return
    first, second, math_slot = acquisition["channels"]
    if math_slot["availability"] != "unavailable" or any(ch["availability"] != "available" for ch in (first, second)):
        return
    result["primary_complete"] = True
    result["channel_attribution"] = "slot_order_only"
    for ch in (first, second):
        key = ch["channel"]
        result["conversion"][key] = {"status": ch["conversion_status"], "reason": ch["conversion_reason"]}
        if ch["engineering"] is not None:
            result["measurements"][key] = _measure(ch["engineering"], config["expectations"][key])
    result["measurement_pass"] = len(result["measurements"]) == 2 and all(
        m["voltage_in_range"] and m["period_in_range"] for m in result["measurements"].values()
    )
    if len(result["measurements"]) == 2 and all(m["voltage_in_range"] for m in result["measurements"].values()):
        result["channel_attribution"] = "slot_order_and_distinct_voltage"


def _run(evidence, fixture_raw, *, source, authorization, identity, hold, claim, screen, clock,
         marker, continued):
    """One session. The production wrapper is the only caller providing a WinUSB backend."""
    result = {
        "schema_version": "0.2-native-supervised", "status": "BLOCKED", "source_commit": source,
        "authorization": authorization, "identity_provenance": identity, "start_utc": utc(),
        "fixture_sha256": sha256(fixture_raw).hexdigest(), "device_generation_id": None,
        "acquisition_coherence": "UNPROVEN", "request_attempts": 0, "retries": 0,
        "write_bytes": None, "write_completed": None, "startup": {}, "response": {},
        "transport": "NOT_RUN", "wire_integrity": "NOT_CHECKED", "primary_complete": False,
        "channel_attribution": "UNVERIFIED", "conversion": {}, "measurements": {},
        "measurement_pass": False, "instrument_continuation": "NOT_CHECKED",
        "cleanup": dict(lease_acquired=False, lease_released=False, interface_claimed=False,
                        interface_released=False),
        "session_id": uuid4().hex, "marker": "NOT_CHECKED", "admission_guard": "NOT_CHECKED",
        "acquisition_outcome": "NOT_COMPLETED", "finalization": "NOT_STARTED", "errors": [],
    }
    evidence, marker = Path(evidence).resolve(), Path(marker).absolute()
    guard = finalization_guard(marker)
    guard_identity = None
    created = False
    stage = "fixture"
    try:
        config = _fixture(fixture_raw)
        stage = "evidence"
        if (evidence.drive.startswith("\\\\") or evidence == marker.resolve() or evidence in marker.resolve().parents
                or evidence == guard.resolve() or evidence in guard.resolve().parents
                or not evidence.parent.is_dir() or shutil.disk_usage(evidence.parent).free < 4 * 1024 * 1024):
            raise Blocked("Use a fresh evidence directory with a local parent and room for evidence")
        evidence.mkdir()
        created = True
        (evidence / "fixture.json").write_bytes(fixture_raw)
        with (evidence / "journal.jsonl").open("x", encoding="utf-8") as journal:
            def log(event, **fields):
                journal.write(json.dumps({"utc": utc(), "monotonic": clock(), "stage": stage,
                                          "event": event, **fields}) + "\n")
                journal.flush()

            stage = "ownership"
            with record_cleanup(hold(), result["cleanup"], "lease_released"):
                result["cleanup"]["lease_acquired"] = True
                require_no_finalization(marker)
                result["admission_guard"] = "ABSENT"
                if os.path.lexists(marker):
                    result["marker"] = "EXISTING"
                    raise Blocked("Prior interruption marker requires external review")
                result["marker"] = "ABSENT"
                result["host"] = screen()
                if result["host"]["blocked"]:
                    raise Blocked(result["host"]["blocked"])
                stage = "marking"
                marker.parent.mkdir(parents=True, exist_ok=True)
                if marker.parent.stat().st_dev != evidence.stat().st_dev:
                    raise Blocked("Evidence and marker must support same-volume finalization")
                marker_identity = create_record(marker, {
                    "run_id": evidence.name, "session_id": result["session_id"],
                    "source_commit": source, "operation": "ACQ-10H", "utc": utc(),
                })
                result["marker"] = "RETAINED"
                result["marker_identity"] = {
                    "volume": marker_identity[0][0], "file_id": str(marker_identity[0][1]),
                    "sha256": sha256(marker_identity[1]).hexdigest(),
                }
                stage = "claim"
                with record_cleanup(claim(), result["cleanup"], "interface_released") as link:
                    result["cleanup"]["interface_claimed"] = True
                    stage = "startup"
                    with (evidence / "startup_rx.bin").open("xb") as raw:
                        _window(link, clock, clock(), raw, log, result["startup"], silent=True)
                    stage = "write"
                    with (evidence / "response_rx.bin").open("xb") as raw:
                        (evidence / "request_attempt.bin").write_bytes(REQUEST)
                        log("write_attempt", wire_hex=REQUEST.hex(" ").upper())
                        result["request_attempts"] = 1
                        result["write_bytes"] = link.write(REQUEST)
                        write_time = clock()
                        result["write_completed"] = {"utc": utc(), "monotonic_s": write_time}
                        log("write_returned", count=result["write_bytes"], completed=write_time)
                        if result["write_bytes"] != len(REQUEST):
                            raise ObservationError("short or ambiguous S02 write")
                        stage = "response"
                        _window(link, clock, write_time, raw, log, result["response"])
                        result["transport"] = "PASS"
                    stage = "interface_cleanup"
                stage = "validation"
                _validate_response(evidence / "response_rx.bin", result, config, evidence)
                stage = "continuation"
                result["instrument_continuation"] = "CONFIRMED" if continued() is True else "UNCONFIRMED"
                log("operator_continuation", outcome=result["instrument_continuation"])
                if (result["transport"] == "PASS" and result["primary_complete"] and result["measurement_pass"]
                        and result["instrument_continuation"] == "CONFIRMED"):
                    result["acquisition_outcome"] = "PASS_WITH_STATUS" if result["response"]["statuses"] else "PASS"
                    stage = "prepare_finalization"
                    guard_identity = prepare(evidence, marker, marker_identity, guard, result)
                stage = "lease_cleanup"
        if guard_identity is None:
            result["status"] = "FAIL"
            if not result["primary_complete"]:
                result["errors"].append({"stage": "validation", "detail": "native primary did not match the fixture profile"})
            elif not result["measurement_pass"]:
                result["errors"].append({"stage": "measurement", "detail": "conversion or independent expectation failed"})
            if result["instrument_continuation"] != "CONFIRMED":
                result["errors"].append({"stage": "continuation", "detail": "operator did not confirm normal continuation"})
    except (Exception, KeyboardInterrupt) as exc:
        result["errors"].append({"stage": stage, "type": type(exc).__name__, "detail": str(exc)})
        if stage == "marking" and os.path.lexists(marker):
            result["marker"] = "CREATION_UNCONFIRMED"
        if stage in ("write", "response"):
            result["transport"] = "FAIL"
        cleanup = result["cleanup"]
        incomplete_cleanup = (
            (cleanup["interface_claimed"] and not cleanup["interface_released"])
            or (cleanup["lease_acquired"] and not cleanup["lease_released"])
        )
        if result["request_attempts"] or incomplete_cleanup:
            result["status"] = "FAIL"
        if result["finalization"] == "PENDING" or stage == "prepare_finalization":
            result["finalization"] = "FAILED"
            if os.path.lexists(guard):
                result["admission_guard"] = "RETAINED"
    result["end_utc"] = utc()
    if created:
        if guard_identity is not None and result["cleanup"]["lease_released"] and not result["errors"]:
            try:
                publish(evidence, marker, guard, guard_identity, result)
                return result
            except (Exception, KeyboardInterrupt) as exc:
                if result["finalization"] == "COMPLETE":
                    return result
                result["status"] = "FAIL"
                result["finalization"] = "FAILED"
                result["errors"].append({"stage": "publish_final_manifest", "type": type(exc).__name__,
                                         "detail": str(exc)})
        try:
            if "artifacts" not in result:
                result["artifacts"] = {path.name: file_record(path) for path in evidence.iterdir() if path.is_file()}
            create_record(evidence / "native_manifest.json", result)
        except (Exception, KeyboardInterrupt) as exc:
            result["status"] = "FAIL"
            result["errors"].append({"stage": "manifest", "type": type(exc).__name__, "detail": str(exc)})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Experimental supervised ACQ-10H, separate run approval required")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--authorization", required=True, help="Separate Architecture run authorization identifier")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--fixture-sha256", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if sys.platform != "win32" or not sys.stdin.isatty() or not sys.stderr.isatty():
            raise Blocked("Use a supervised interactive Windows console")
        if not args.authorization.strip():
            raise Blocked("Separate, run-specific Architecture authorization is required")
        source = _source_commit(args.source_commit)
        with args.fixture.open("rb") as fixture:
            fixture_raw = fixture.read(65537)
        if len(fixture_raw) > 65536 or sha256(fixture_raw).hexdigest() != args.fixture_sha256.lower():
            raise Blocked("Fixture exceeds its size bound or differs from the approved digest")
        _fixture(fixture_raw)
        print(f"ACQ-10H {args.authorization}. Approved evidence destination: {args.evidence.resolve()}", file=sys.stderr)
        print(f"Confirm {MODEL}, original firmware {FIRMWARE}, correct rear USB, exclusive ownership, settled "
              "history, approved fixture and evidence path, running disposable data and MATH off. Confirm "
              "monitoring is suspended and Save/Recall is idle. Accept possible clipping, temporary disturbance, "
              "or a lockup needing supervised operator recovery. Type ACCEPT ACQ-10H to authorize one write:",
              file=sys.stderr, flush=True)
        if input().strip() != "ACCEPT ACQ-10H":
            raise Blocked("Operator did not authorize the one-write experiment")
        _source_commit(source)

        def continuation():
            print("Without sending another command, verify continuous acquisition and unchanged recorded "
                  "configuration at the scope. Type CONTINUING only if both are observed:",
                  file=sys.stderr, flush=True)
            return input().strip() == "CONTINUING"

        result = _run(args.evidence, fixture_raw, source=source, authorization=args.authorization,
                      identity="operator confirmed model, firmware, fixture and risk in console",
                      hold=lease, claim=_claim_pair, screen=lambda: assess_host(
                          host_snapshot(), os.getpid(), (sys.executable, sys._base_executable)),
                      clock=perf_counter, marker=state_path(), continued=continuation)
    except (Exception, KeyboardInterrupt) as exc:
        result = {"status": "BLOCKED", "request_attempts": 0, "retries": 0, "error": str(exc)}
    print(json.dumps(result, indent=2))
    return EXIT[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
