"""Run the ACQ-12 bench fixture with the existing Hantek lease and evidence journal."""

import argparse
import csv
import json
import os
from pathlib import Path
import sys
from time import monotonic, sleep

from fixture import command, decode_pulses, open_fixture
from hantek_dso5k._acq11 import RUN_STOP, _running, describe, run
from hantek_dso5k._acq12 import DIAGNOSTIC, apply_profile, restore
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


def inspect_capture(folder, name, capture, expected):
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
    if baseline[171] or baseline[179] or baseline[185]:
        raise RuntimeError("Diagnostic requires ordinary YT sample mode with Math disabled")
    try:
        configured = apply_profile(session, "acq12_diagnostic_settings", baseline, DIAGNOSTIC)
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
        outcome["pattern_decoded"] = inspect_capture(session.evidence, "diagnostic_pattern", captured, expected)
    finally:
        try:
            outcome["terminal_fixture"] = confirm_idle(device)
        finally:
            if session.settled:
                restore(session, baseline, outcome, DIAGNOSTIC)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--event", required=True, type=int)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--overwrite-stopped", action="store_true", required=True)
    args = parser.parse_args()
    if not 1 <= args.event <= 65535:
        parser.error("event must be 1–65535")
    source = os.environ["HANTEK_ACQ12_SOURCE"]
    _source_commit(source)
    with open_fixture(args.port) as device:
        result = run(
            args.evidence, source, "diagnostic", hold=lease, claim=_claim_pair,
            screen=lambda: assess_host(host_snapshot(), os.getpid(), (sys.executable, sys._base_executable)),
            marker=state_path(), execute=lambda session, mode, outcome: diagnostic(session, outcome, device, args.event),
            operation="ACQ-12",
        )
    print(json.dumps({key: value for key, value in result.items() if key not in
                      ("artifacts", "pattern_capture", "idle_capture", "pattern_decoded")}, indent=2))
    for channel, row in result.get("pattern_decoded", {}).items():
        print(json.dumps({"channel": channel, "decoded": row["accepted"],
                          "candidate_ids": [packet["event_id"] for packet in row["decoded_candidates"]],
                          "vpp_v": row["vpp_v"], "sample_count": row["samples"],
                          "sample_interval_s": row["interval_s"], "clipped": row["clipped"]}))
    return {"PASS": 0, "FAIL": 4, "BLOCKED": 3}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
