"""Run the ACQ-12 bench fixture with the existing Hantek lease and evidence journal."""

import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
from statistics import median
import sys
from time import monotonic, sleep

from fixture import command, inspect_capture, open_fixture
from logic_reference import analyze_reference, compare_reference, record_reference
from hantek_dso5k._acq11 import RUN_STOP, SINGLE, _running, describe, run
from hantek_dso5k._acq12 import (
    DIAGNOSTIC, EXTERNAL, apply_profile, close_frozen_file_list, restore, restore_panel, screen_status, set_timebase,
)
from hantek_dso5k._framing import read_frames
from hantek_dso5k._host import assess_host, host_snapshot, lease
from hantek_dso5k._native_finalization import create_record
from hantek_dso5k._retrieve import file_record
from hantek_dso5k._pair_windows import _claim_pair, _source_commit
from hantek_dso5k._session import state_path, utc


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


def external(session, outcome, device, event_ids, ready_digest, path, *, baseline=None,
             restoration_required=None, timebase=19, profile=EXTERNAL, logic_cli=None):
    if baseline is None:
        baseline = session.settings("acq12_initial")
    outcome["initial"] = describe(baseline)
    if _running(baseline) or baseline[171] or baseline[179] or baseline[185] or baseline[188]:
        raise RuntimeError("Start in stopped ordinary YT sample mode with Math disabled")
    outcome["fixture"] = fixture_request(device, "INFO")
    if (outcome["fixture"].get("firmware"), outcome["fixture"].get("version")) != ("acq12-esp8266", "0.2.0"):
        raise RuntimeError("Unexpected fixture firmware")
    idle = confirm_idle(device)
    if event_ids and (event_ids != sorted(set(event_ids)) or event_ids[0] <= idle["last_accepted_id"]):
        raise RuntimeError("Event IDs must increase past the fixture's last accepted ID")
    if restoration_required is not None:
        restoration_required(True)
    restored = None
    try:
        configured = apply_profile(session, "acq12_external_settings", baseline, profile)
        configured = set_timebase(session, configured, timebase)
        outcome["configured"] = describe(configured)
        outcome["trials"] = trials = []
        for event_id in event_ids or [None]:
            row = {"requested_event_id": event_id, "fixture_idle": confirm_idle(device), "started_at": utc()}
            trials.append(row)
            session.key("acq12_single", SINGLE)
            row["arming_requested"] = True
            first = session.settings("acq12_after_single")
            display = screen_status(session, "acq12_arm_display")
            digest = display["trigger_status_sha256"]
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
            if session.job_check is not None:
                session.job_check()
            reference = record_reference(logic_cli, session.evidence, event_id) if logic_cli else nullcontext(None)
            with reference as logic_capture:
                if logic_cli and session.settings("acq12_ready_after_logic_start")[20] != 1:
                    raise RuntimeError("Scope left the ready state while starting the timing reference")
                row["event_requested_host_utc"] = utc()
                row["event_requested_host_monotonic"] = monotonic()
                row["fixture_ack"] = fixture_request(device, f"EVENT {event_id}")
            for _ in range(4):
                frozen = session.settings("acq12_wait_for_stop")
                if _running(frozen):
                    continue
                row["completion_state"] = frozen[20]
                row["scope_stop_observed_at"] = utc()
                break
            else:
                row["outcome"] = "no_trigger_timeout"
                break
            decoded = {}
            if path != "frozen":
                native = session.capture(f"event_{event_id}", frozen)
                row["native"] = native
                row["decoded"] = decoded = inspect_capture(native, event_id)
            accepted = set(decoded) == {"CH1", "CH2"} and all(ch["accepted"] for ch in decoded.values())
            if not accepted and path != "native" and frozen[206:208] == b"\x30\x01":
                row["frozen_csv"] = exported = {}
                session.frozen(frozen, preserved=exported, summary_prefix=f"frozen_{event_id}")
                csv_capture = {"availability": {channel: True for channel in exported},
                               "channels": {channel: {**record, "clipped": None}
                                            for channel, record in exported.items()}}
                row["frozen_decoded"] = decoded = inspect_capture(csv_capture, event_id)
                accepted = set(decoded) == {"CH1", "CH2"} and all(ch["accepted"] for ch in decoded.values())
                row["retrieval_method"] = "frozen_csv"
                recovery_screen = screen_status(session, "acq12_after_frozen_csv_display")
                row["file_list_close_requested"] = close_frozen_file_list(session, frozen, recovery_screen)
            else:
                row["retrieval_method"] = "native_s02"
            row["outcome"] = "common_event" if accepted else "unproven_waveform"
            if not accepted:
                break
            first, second = [
                next(packet["pulses_us"] for packet in decoded[channel]["decoded_candidates"]
                     if packet["event_id"] == event_id and packet["channel"] == int(channel[-1]))
                for channel in ("CH1", "CH2")
            ]
            delays = [b[0] - a[0] for a, b in zip(first, second)]
            row["time_relationship"] = {
                "rising_ch2_minus_ch1_us": delays,
                "median_us": median(delays),
                "sample_resolution_bound_us": sum(ch["interval_s"] for ch in decoded.values()) * 1e6,
                "independent_reference": False,
            }
            row["waveform_preserved_at"] = utc()
            row["fixture_end"] = fixture_request(device, "STATUS")
            if row["fixture_end"]["last_completed_id"] != event_id or row["fixture_end"]["high_mask"]:
                raise RuntimeError("Fixture did not report completed and idle after capture")
            if logic_capture is not None:
                try:
                    if logic_capture.get("error"):
                        raise RuntimeError(logic_capture["error"])
                    reference = analyze_reference((session.evidence / f"logic_{event_id}.bin").read_bytes(), event_id)
                    row["independent_reference"] = reference
                    row["independent_comparison"] = compare_reference(reference, decoded, event_id)
                except (OSError, ValueError, RuntimeError) as exc:
                    row["independent_reference_error"] = str(exc)
    finally:
        try:
            outcome["terminal_fixture"] = confirm_idle(device)
        finally:
            if session.settled:
                restored = restore(session, baseline, outcome, profile)
                if restoration_required is not None:
                    restoration_required(False)
    return restored


def serve(argv):
    """Start the existing service with one locally configured qualifier and serial fixture."""
    from hantek_dso5k import service
    from hantek_dso5k._service_capture import Unsupported, instrument

    parser = argparse.ArgumentParser(description=serve.__doc__, allow_abbrev=False)
    parser.add_argument("--fixture-port", required=True)
    parser.add_argument("--ready-digest", required=True)
    config, service_args = parser.parse_known_args(argv)
    if len(config.ready_digest) != 64 or any(c not in "0123456789abcdef" for c in config.ready_digest):
        parser.error("supply the observed ready status digest as 64 lowercase hex digits")

    def capture(session, baseline, request, result, restoration_required):
        with open_fixture(config.fixture_port) as device:
            restored = external(session, result, device, [request["event_id"]], config.ready_digest,
                                request["path"], baseline=baseline, restoration_required=restoration_required)
        trial = result["trials"][0]
        result.update(restored_raw=restored, method=trial.get("retrieval_method"),
                      common_event=trial["outcome"] == "common_event")
        result["acquisition_outcome"] = "succeeded" if result["common_event"] else "failed"
        artifacts = {item.name: file_record(item) for item in session.evidence.iterdir()
                     if item.suffix in (".csv", ".rx", ".rgb565")}
        create_record(session.evidence / "external-event.json", {
            "schema_version": "1", "fixture": result["fixture"], "instrument": instrument(),
            "acquisition_id": session.evidence.name, "configuration": result["configured"],
            "trial": trial, "restoration_verified": result.get("restoration_verified"),
            "artifacts": artifacts, "time_reference": "relative record start and host UTC",
        })
        if not result["common_event"]:
            raise Unsupported(trial["outcome"])

    return service.main(service_args, external_fixture=capture)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--mode", choices=["diagnostic", "external", "restore-panel"], default="diagnostic")
    parser.add_argument("--event", action="append", default=[], type=int)
    parser.add_argument("--ready-digest")
    parser.add_argument("--path", choices=["auto", "native", "frozen"], default="auto")
    parser.add_argument("--original-settings-rx", type=Path)
    parser.add_argument("--contrast", action="store_true", help="8 ms/div, 144 mV EXT and swapped vertical scales")
    parser.add_argument("--logic-cli", type=Path, help="Approved sigrok CLI for an attached three-channel FX2LP reference")
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--overwrite-stopped", action="store_true", required=True)
    args = parser.parse_args()
    if any(not 1 <= event <= 65535 for event in args.event):
        parser.error("event must be 1–65535")
    if args.mode == "diagnostic" and len(args.event) != 1:
        parser.error("diagnostic requires one event ID")
    if args.mode == "external" and args.event and not args.ready_digest:
        parser.error("external stimulation requires an independently observed ready screen digest")
    if (args.mode == "restore-panel") != (args.original_settings_rx is not None):
        parser.error("restore-panel requires the original settings exchange")
    if args.contrast and args.mode != "external":
        parser.error("contrast applies only to an external trial")
    if args.logic_cli and (args.mode != "external" or not args.event or not args.logic_cli.is_file()):
        parser.error("logic reference requires an existing sigrok CLI and a requested external event")
    source = os.environ["HANTEK_ACQ12_SOURCE"]
    _source_commit(source)
    with open_fixture(args.port) as device:
        def execute(session, mode, outcome):
            if mode == "diagnostic":
                diagnostic(session, outcome, device, args.event[0])
            elif mode == "restore-panel":
                observed = [body for _, opcode, body in read_frames([args.original_settings_rx.read_bytes()])
                            if opcode == 0x81 and len(body) == 208]
                if len(observed) != 1:
                    raise RuntimeError("Original evidence must hold one complete S81 settings reply")
                outcome["terminal_fixture"] = confirm_idle(device)
                restore_panel(session, observed[0], outcome)
            else:
                profile, timebase = EXTERNAL, 19
                if args.contrast:
                    profile = {**EXTERNAL, 1: EXTERNAL[11], 11: EXTERNAL[1],
                               8: EXTERNAL[18], 18: EXTERNAL[8], 25: b"\x12\x00"}
                    timebase = 20
                external(session, outcome, device, args.event, args.ready_digest, args.path,
                         profile=profile, timebase=timebase, logic_cli=args.logic_cli)
                if args.event and (len(outcome["trials"]) != len(args.event)
                                   or any(trial["outcome"] != "common_event" for trial in outcome["trials"])):
                    raise RuntimeError("The requested series did not preserve a verified event on both channels")
                if args.logic_cli and any("independent_comparison" not in trial for trial in outcome["trials"]):
                    raise RuntimeError("The independent timing comparison is incomplete")

        result = run(
            args.evidence, source, args.mode, hold=lease, claim=_claim_pair,
            screen=lambda: assess_host(host_snapshot(), os.getpid(), (sys.executable, sys._base_executable)),
            marker=state_path(), execute=execute, operation="ACQ-12",
        )
    print(json.dumps({key: value for key, value in result.items() if key not in
                      ("artifacts", "pattern_capture", "idle_capture", "pattern_decoded", "trials")}, indent=2))
    for trial in result.get("trials", []):
        print(json.dumps({key: trial[key] for key in (
            "requested_event_id", "outcome", "scope_states_before_event", "arm_ready",
            "display_trigger_status_sha256", "retrieval_method", "time_relationship",
        ) if key in trial}))
    for channel, row in result.get("pattern_decoded", {}).items():
        print(json.dumps({"channel": channel, "decoded": row["accepted"],
                          "candidate_ids": [packet["event_id"] for packet in row["decoded_candidates"]],
                          "vpp_v": row["vpp_v"], "sample_count": row["samples"],
                          "sample_interval_s": row["interval_s"], "clipped": row["clipped"]}))
    return {"PASS": 0, "FAIL": 4, "BLOCKED": 3}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(serve(sys.argv[2:]) if sys.argv[1:2] == ["serve"] else main())
