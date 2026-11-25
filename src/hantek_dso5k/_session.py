"""Construct qualification evidence and maintain an interruption marker."""
from datetime import datetime, timezone
from hashlib import sha256
from importlib import metadata
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import sys
from time import perf_counter

from . import __version__
from ._host import assess_host, Blocked, host_snapshot, interruption_path as state_path, lease
from ._qualification import ClaimedLinkIO, run
from ._screens import review_session, write_json_new
from ._settings import settings
from ._winusb import open_link


CAPTURE_SCHEMA_VERSION = "2.0"


def utc():
    return datetime.now(timezone.utc).isoformat()


def installed_commit():
    try:
        record = metadata.distribution("hantek-dso5k-control").read_text("direct_url.json")
        commit = json.loads(record or "{}").get("vcs_info", {}).get("commit_id")
    except (metadata.PackageNotFoundError, ValueError, OSError):
        return None
    return commit if isinstance(commit, str) and re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", commit) else None


def summarize(directory, result):
    for stage in ("baseline", "final"):
        payload = directory / f"{stage}_payload.bin"
        if payload.is_file() and payload.stat().st_size == 208:
            write_json_new(directory / f"{stage}_settings.json", settings(payload.read_bytes()))
    if (directory / "session.json").is_file():
        review_session(directory)
    for path in sorted(directory.iterdir()):
        if path.is_file():
            with path.open("rb") as handle:
                result["artifacts"][path.name] = {"bytes": path.stat().st_size,
                                                 "sha256": sha256(handle.read()).hexdigest()}


def qualify(directory, *, confirmed, history_confirmed, hold=lease, census=host_snapshot,
            claim=open_link, clock=perf_counter, launch=run, marker=None):
    directory = Path(directory)
    directory.mkdir(exist_ok=False)
    result = {"schema_version": CAPTURE_SCHEMA_VERSION, "software_version": __version__, "source_commit": installed_commit(),
              "run_id": directory.name, "start_utc": utc(), "end_utc": None,
              "device": {"vid_pid": "049F:505A", "model": "DSO5102P",
                         "firmware_baseline": "3.40.0 (151009.0)", "firmware_verified_live": False,
                         "interface_claimed": False, "interface_released": False, "clock": None},
              "host": None, "exit_code": 3, "strict_session": "NOT RUN", "sequence_complete": False,
              "request_attempts": 0, "retries": 0, "settings_comparison": None, "steps": [],
              "trigger_state_evidence": None, "waveforms": {"CH1": None, "CH2": None},
              "shared_acquisition": None, "trigger_time_origin": None, "artifacts": {}, "error": None}
    try:
        if not confirmed or not history_confirmed:
            raise Blocked("Operator must confirm single-client ownership and settled command history")
        with hold():
            marker = Path(marker) if marker is not None else state_path()
            if marker.exists():
                raise Blocked("Prior interrupted or unresolved access requires external review")
            result["host"] = assess_host(census(), os.getpid(), (sys.executable, sys._base_executable))
            if result["host"]["blocked"]:
                raise Blocked(result["host"]["blocked"])
            marker.parent.mkdir(parents=True, exist_ok=True)
            with marker.open("x", encoding="utf-8") as handle:
                json.dump({"run_id": directory.name, "utc": utc()}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            result["exit_code"] = 4
            with claim() as link:
                result["device"]["interface_claimed"] = True
                gate = SimpleNamespace(exclusive_ownership=True, lease_held=True, target_verified=True,
                                       prior_history_settled=True, no_unresolved_s12=True)
                seen = launch(ClaimedLinkIO(link), clock, directory, gate,
                              allow_timer_consistent_status=True)
            result["device"]["interface_released"] = True
            result["sequence_complete"] = seen["sequence_complete"]
            result["strict_session"] = "PASS" if seen["strict_session"] else "FAIL"
            result["settings_comparison"] = seen.get("settings_comparison")
            result["error"] = seen.get("stop_reason")
            result["steps"] = [{"name": step["stage"], "transport": step.get("transport"),
                                "reply": step.get("reply"), "strict": step.get("strict")}
                               for step in seen["steps"]]
            if seen["sequence_complete"]:
                marker.unlink()
                result["exit_code"] = 0 if seen["strict_session"] else 5
    except (Exception, KeyboardInterrupt) as exc:
        result["error"] = str(exc) if isinstance(exc, Blocked) else f"{type(exc).__name__}: qualification stopped"
        if result["exit_code"] in (0, 5):
            result["exit_code"] = 4
    journal = directory / "journal.jsonl"
    if journal.is_file():
        with journal.open(encoding="utf-8") as handle:
            entries = [json.loads(line) for line in handle]
        result["request_attempts"] = sum(row["event"] == "write_attempt" for row in entries)
    try:
        summarize(directory, result)
    except (OSError, ValueError) as exc:
        result["exit_code"] = 4
        result["error"] = f"{type(exc).__name__}: evidence generation failed"
    result["end_utc"] = utc()
    write_json_new(directory / "capture_manifest.json", result)
    return result
