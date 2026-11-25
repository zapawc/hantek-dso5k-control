"""Explicit Windows adapter for the separately approved paired S10 candidate."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import subprocess
import sys
from time import perf_counter

from ._host import Blocked, assess_host, host_snapshot, lease
from ._pair_session import PROFILES, PairBlocked, _run_pair, pair_paths, pair_references
from ._retrieve import CONFIRMATIONS
from ._session import state_path
from ._winusb import ClaimedBulkIO, open_link


def _source_commit(expected):
    if not re.fullmatch(r"[0-9a-fA-F]{40}", expected):
        raise PairBlocked("A full reviewed Git commit SHA is required")
    root = Path(__file__).resolve().parents[2]
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}

    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                encoding="utf-8", timeout=10, env=env)
        if result.returncode:
            raise PairBlocked("Could not verify candidate Git worktree")
        return result.stdout.strip()

    if Path(git("rev-parse", "--show-toplevel")).resolve() != root:
        raise PairBlocked("Run the candidate directly from its reviewed source worktree")
    actual = git("rev-parse", "HEAD")
    if actual != expected.lower() or git("--no-optional-locks", "status", "--porcelain",
                                         "--untracked-files=all"):
        raise PairBlocked("Candidate worktree must be clean and at the approved commit")
    return actual


def _host_assessment():
    return assess_host(host_snapshot(), os.getpid(), (sys.executable, sys._base_executable))


def _screen(specs, evidence, expected):
    if sys.platform != "win32":
        raise PairBlocked("Supervised paired retrieval requires Windows")
    commit = _source_commit(expected)
    marker = state_path()
    if os.path.lexists(marker):
        raise PairBlocked("Existing interruption marker requires external review")
    pair_paths(specs, evidence, marker)
    references = pair_references(specs)
    host = _host_assessment()
    if host["blocked"]:
        raise PairBlocked(host["blocked"])
    return commit, marker, references, host


def _blocked(command, exc):
    return {"command": command, "status": "BLOCKED", "exit_code": 3, "request_attempts": 0,
            "error": {"type": type(exc).__name__, "message": str(exc)}}


def passive_preflight(specs, evidence, expected):
    """Inspect only Git, local files, Windows PnP metadata and process ownership."""
    command = "files pair-preflight"
    try:
        commit, _, references, host = _screen(specs, evidence, expected)
    except (Blocked, PairBlocked, OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        return _blocked(command, exc)
    return {"command": command, "status": "PASS", "exit_code": 0, "source_commit": commit,
            "references": dict(zip((key for key, _, _ in PROFILES), references)),
            "available_instances": host["available_instances"], "driver": host["driver"],
            "assignment": host["assignment"], "marker": "ABSENT", "known_conflicts": 0,
            "unavailable_command_lines": host["unavailable_command_lines"],
            "request_attempts": 0, "lease_acquired": False, "interface_claimed": False}


@contextmanager
def _claim_pair():
    with open_link() as claimed:
        yield ClaimedBulkIO(claimed)


def retrieve_pair(specs, evidence, expected, confirmations, *, experimental_s92_guard=False):
    """Explicit interactive entrypoint. External architecture approval is required."""
    command = "files retrieve-pair"
    try:
        if set(confirmations) != set(CONFIRMATIONS):
            raise PairBlocked("All six contemporaneous operator confirmations are required")
        if not (sys.stdin.isatty() and sys.stderr.isatty()):
            raise PairBlocked("Use a supervised interactive console")
        if sys.platform != "win32":
            raise PairBlocked("Supervised paired retrieval requires Windows")
        _source_commit(expected)
        print("Proceed only with run-specific architecture approval and all six confirmations. "
              "Type YES to allow one paired session:", file=sys.stderr, flush=True)
        if experimental_s92_guard:
            print("Experimental S92 qualification selected. A clean guard without S92 is inconclusive.",
                  file=sys.stderr, flush=True)
        if input().strip() != "YES":
            raise PairBlocked("Operator declined paired session")
        commit, marker = _source_commit(expected), state_path()
    except (Blocked, PairBlocked, OSError, ValueError, subprocess.SubprocessError,
            EOFError, KeyboardInterrupt) as exc:
        return _blocked(command, exc)
    return _run_pair(*specs, evidence, confirmations=confirmations,
                     hold=lease, census=host_snapshot, assess=assess_host, claim=_claim_pair,
                     clock=perf_counter, marker=marker, source_commit=commit,
                     experimental_s92_guard=experimental_s92_guard)
