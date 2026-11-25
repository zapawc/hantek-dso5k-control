"""Run the ACQ-12 bench fixture with the existing Hantek lease and evidence journal."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from time import monotonic, sleep

from fixture import command, decode_pulses, open_fixture
from hantek_dso5k._acq11 import RUN_STOP, SINGLE, _running, describe, run
from hantek_dso5k._acq12 import DIAGNOSTIC, EXTERNAL, apply_profile, restore, screen_status, set_timebase
from hantek_dso5k._host import assess_host, host_snapshot, lease
from hantek_dso5k._pair_windows import _claim_pair, _source_commit
from hantek_dso5k._session import state_path


def fixture_request(device, request):
    response = command(device, request)
    if not response.get("ok"):
        raise RuntimeError(f"Fixture rejected {request}: {response.get('error')}")
    return response


def confirm_idle(device):
    report = fixture_request(device, "STOP")
    if report["active"] or report["high_mask"]:
        raise RuntimeError("Fixture does not report idle")
    return report


def inspect_capture(capture, expected):
    channels = {}
    for channel in ("CH1", "CH2"):
        if not capture["availability"].get(channel):
            continue
        report = capture["channels"][channel]
        # Reuse the retained display conversion. Edges are relative to record start.
        interval = report["interval_s"]
        rises = report["rising_edges_s"]
        falls = iter(report["falling_edges_s"])
        fall = next(falls, None)
        pulses = []
        for rise in rises:
            while fall is not None and fall <= rise:
                fall = next(falls, None)
            if fall is not None:
                pulses.append((rise * 1e6, fall * 1e6))
        candidates = []
        tolerance = max(25, 2 * interval * 1e6)
        if tolerance < 100:
            for start in range(len(pulses) - 16):
                packet = pulses[start:start + 17]
                try:
                    number, decoded = decode_pulses(packet, tolerance_us=tolerance)
                except ValueError:
                    continue
                candidates.append({"channel": number, "event_id": decoded, "pulses_us": packet})
        channels[channel] = {
            **report,
            "decoded_candidates": candidates,
            "decode_tolerance_us": tolerance,
            "accepted": any(row["event_id"] == expected and row["channel"] == int(channel[-1])
                            for row in candidates) and not report["clipped"],
        }
    return channels


def diagnostic(session, outcome, device, expected):
    baseline = session.settings("acq12_initial")
    outcome["initial"] = describe(baseline)
    if _running(baseline):
        raise RuntimeError("Start diagnostics with the scope stopped")
    info = fixture_request(device, "INFO")
    if (info.get("firmware"), info.get("version"), info.get("protocol")) != ("acq12-esp8266", "0.2.0", 1):
        raise RuntimeError("Unexpected fixture identity")
    outcome["fixture"] = info
    idle = confirm_idle(device)
    if expected <= idle["last_accepted_id"]:
        raise RuntimeError("Select a new event ID above the fixture's latest accepted ID")
    if baseline[171] or baseline[179] or baseline[185] or baseline[188]:
        raise RuntimeError("Diagnostic requires ordinary YT sample mode with Math disabled")
    try:
        configured = apply_profile(session, "acq12_diagnostic_settings", baseline, DIAGNOSTIC)
        configured = set_timebase(session, configured, 19)
        outcome["configured"] = describe(configured)
        session.key("acq12_run_for_diagnostic", RUN_STOP)
        running = session.settings("acq12_diagnostic_running")
        if not _running(running):
            raise RuntimeError("Scope did not enter running mode")
        outcome["idle_capture"] = session.capture("diagnostic_idle", running)
        outcome["pattern_request_host_monotonic"] = monotonic()
        outcome["pattern_ack"] = fixture_request(device, f"PATTERN {expected}")
        sleep(0.12)
        captured = session.capture("diagnostic_pattern", running)
        outcome["pattern_capture"] = captured
        outcome["pattern_decoded"] = inspect_capture(captured, expected)
    finally:
        try:
            outcome["terminal_fixture"] = confirm_idle(device)
        finally:
            if session.settled:
                restore(session, baseline, outcome, DIAGNOSTIC)


def external(session, outcome, device, event_ids, ready_digest):
    baseline = session.settings("acq12_initial")
    outcome["initial"] = describe(baseline)
    if _running(baseline) or baseline[171] or baseline[179] or baseline[185] or baseline[188]:
        raise RuntimeError("Start in stopped ordinary YT sample mode with Math disabled")
    outcome["fixture"] = fixture_request(device, "INFO")
    idle = confirm_idle(device)
    if event_ids and (event_ids != sorted(set(event_ids)) or event_ids[0] <= idle["last_accepted_id"]):
        raise RuntimeError("Event IDs must increase past the fixture's last accepted ID")
    try:
        configured = apply_profile(session, "acq12_external_settings", baseline, EXTERNAL)
        configured = set_timebase(session, configured, 19)
        outcome["configured"] = describe(configured)
        outcome["trials"] = trials = []
        for event_id in event_ids or [None]:
            row = {"requested_event_id": event_id, "fixture_idle": confirm_idle(device)}
            trials.append(row)
            session.key("acq12_single", SINGLE)
            row["arming_requested"] = True
            first = session.settings("acq12_after_single")
            display = screen_status(session, "acq12_arm_display")
            digest = sha256(display.pop("status_pixels")).hexdigest()
            row["display"] = display
            row["display_trigger_status_sha256"] = digest
            second = session.settings("acq12_after_arm_display")
            row["scope_states_before_event"] = [first[20], second[20]]
            waiting = first[20] == second[20] == 1
            row["arm_ready"] = bool(waiting and ready_digest == digest and display["aggregate"] == "PASS")
            if event_id is None:
                row["no_trigger_observation"] = waiting
                row["outcome"] = "no_trigger_timeout" if waiting else "premature_completion"
                break
            if not row["arm_ready"]:
                row["outcome"] = "readiness_unavailable"
                break
            row["event_requested_host_monotonic"] = monotonic()
            row["fixture_ack"] = fixture_request(device, f"EVENT {event_id}")
            for _ in range(4):
                frozen = session.settings("acq12_wait_for_stop")
                if _running(frozen):
                    continue
                row["completion_state"] = frozen[20]
                break
            else:
                row["outcome"] = "no_trigger_timeout"
                break
            native = session.capture(f"event_{event_id}", frozen)
            row["native"] = native
            row["decoded"] = decoded = inspect_capture(native, event_id)
            accepted = set(decoded) == {"CH1", "CH2"} and all(ch["accepted"] for ch in decoded.values())
            if not accepted and frozen[206:208] == b"\x30\x01":
                row["frozen_csv"] = exported = {}
                session.frozen(frozen, preserved=exported)
                csv_capture = {"availability": {channel: True for channel in exported},
                               "channels": {channel: {**record, "clipped": None}
                                            for channel, record in exported.items()}}
                row["frozen_decoded"] = decoded = inspect_capture(csv_capture, event_id)
                accepted = set(decoded) == {"CH1", "CH2"} and all(ch["accepted"] for ch in decoded.values())
                row["retrieval_method"] = "frozen_csv"
            else:
                row["retrieval_method"] = "native_s02"
            row["outcome"] = "common_event" if accepted else "unproven_waveform"
            if not accepted:
                break
    finally:
        try:
            outcome["terminal_fixture"] = confirm_idle(device)
        finally:
            if session.settled:
                restore(session, baseline, outcome, EXTERNAL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--mode", choices=["diagnostic", "external"], default="diagnostic")
    parser.add_argument("--event", action="append", default=[], type=int)
    parser.add_argument("--ready-digest")
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--overwrite-stopped", action="store_true", required=True)
    args = parser.parse_args()
    if any(not 1 <= event <= 65535 for event in args.event):
        parser.error("event must be 1–65535")
    if args.mode == "diagnostic" and len(args.event) != 1:
        parser.error("diagnostic requires one event ID")
    if args.mode == "external" and args.event and not args.ready_digest:
        parser.error("external stimulation requires an independently observed ready screen digest")
    source = os.environ["HANTEK_ACQ12_SOURCE"]
    _source_commit(source)
    with open_fixture(args.port) as device:
        def execute(session, mode, outcome):
            if mode == "diagnostic":
                diagnostic(session, outcome, device, args.event[0])
            else:
                external(session, outcome, device, args.event, args.ready_digest)

        result = run(
            args.evidence, source, args.mode, hold=lease, claim=_claim_pair,
            screen=lambda: assess_host(host_snapshot(), os.getpid(), (sys.executable, sys._base_executable)),
            marker=state_path(), execute=execute, operation="ACQ-12",
        )
    print(json.dumps({key: value for key, value in result.items() if key not in
                      ("artifacts", "pattern_capture", "idle_capture", "pattern_decoded", "trials")}, indent=2))
    for trial in result.get("trials", []):
        print(json.dumps({key: value for key, value in trial.items() if key not in ("native", "decoded")}))
    for channel, row in result.get("pattern_decoded", {}).items():
        print(json.dumps({"channel": channel, "decoded": row["accepted"],
                          "candidate_ids": [packet["event_id"] for packet in row["decoded_candidates"]],
                          "vpp_v": row["vpp_v"], "sample_count": row["samples"],
                          "sample_interval_s": row["interval_s"], "clipped": row["clipped"]}))
    return {"PASS": 0, "FAIL": 4, "BLOCKED": 3}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
