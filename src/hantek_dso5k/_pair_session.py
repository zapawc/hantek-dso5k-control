"""Bounded sequential CH1/CH2 S10 controller with explicit dependencies."""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from typing import NamedTuple

from ._file_replay import CHUNK_BYTES, MAX_RX_BYTES, SHA256_HEX, csv_request, decode_file_reply, publish_file
from ._receive import ReceiveError, observe
from ._retrieve import CONFIRMATIONS, file_record, require_absent, window_result, record_cleanup
from ._s92_guard import observe_qualification_guard, qualification_guard_result
from ._screens import write_json_new
from ._session import utc

WINDOW = 12
PROFILES = (
    ("CH1", "WaveData163.csv", 89566),
    ("CH2", "WaveData164.csv", 89193),
)


class PairBlocked(RuntimeError):
    pass


class PairSpec(NamedTuple):
    reference: Path
    expected_sha256: str
    output: Path


class PairS10IO:
    """Only permit CH1 and then CH2 once each in strict order."""
    def __init__(self, link):
        self.link = link
        self.next_index = 0
        self.requests = tuple(csv_request(filename) for _, filename, _ in PROFILES)

    def read(self, size, *, timeout_ms):
        return self.link.read(size, timeout_ms=timeout_ms)

    def write(self, request):
        if self.next_index >= len(self.requests) or request != self.requests[self.next_index]:
            raise ReceiveError("request not permitted by sequential S10 guard")
        self.next_index += 1   # burn allowance BEFORE underlying write, including on ambiguity
        return self.link.write(request)


def _record_reference(spec, count):
    if SHA256_HEX.fullmatch(spec.expected_sha256 or "") is None:
        raise PairBlocked("Independent SHA-256 must contain exactly 64 hex digits")
    with Path(spec.reference).open("rb") as handle:
        data = handle.read(count + 1)
    if len(data) != count:
        raise PairBlocked("Independent reference has incorrect byte count")
    digest = sha256(data).hexdigest()
    if digest != spec.expected_sha256.lower():
        raise PairBlocked("Independent reference digest mismatch")
    return {"bytes": count, "sha256": digest}


def simulate_pair(spec_ch1, spec_ch2, evidence_dir, *, confirmations,
                  hold=None, census=None, claim=None, assess=None, clock=None, marker=None,
                  experimental_s92_guard=False):
    """Run a simulation with caller-supplied dependencies and no live defaults."""
    if any(x is None for x in (hold, census, claim, assess, clock, marker)):
        raise PairBlocked("ACQ-06 has no live backend; offline injected dependencies required")
    return _run_pair(spec_ch1, spec_ch2, evidence_dir, confirmations=confirmations,
                     hold=hold, census=census, claim=claim, assess=assess, clock=clock, marker=marker,
                     experimental_s92_guard=experimental_s92_guard)


def pair_paths(specs, evidence, marker):
    """Check names before admission and resolve parent aliases for publication."""
    require_absent(evidence)
    evidence = evidence.resolve()
    marker = marker.resolve()
    if (marker == evidence or marker in evidence.parents or not evidence.parent.is_dir()
            or evidence.is_reserved() or
            (os.name == "nt" and (":" in evidence.name or evidence.name.endswith((".", " "))))):
        raise PairBlocked("Evidence needs an existing parent separate from the marker")
    paths = []
    for spec in specs:
        out = Path(spec.output).absolute()
        require_absent(out)
        if not out.parent.is_dir() or out.is_reserved() or (
                os.name == "nt" and (":" in out.name or out.name.endswith((".", " ")))):
            raise PairBlocked("Each output requires an existing parent and nonreserved filename")
        out = out.parent.resolve() / out.name
        require_absent(out)
        if out == marker or out == evidence or evidence in out.parents:
            raise PairBlocked("Outputs must be separate from marker and evidence")
        paths.append(out)
    if len(set(paths)) != 2:
        raise PairBlocked("Outputs must be distinct")
    references = [Path(spec.reference).resolve() for spec in specs]
    if len(set(references)) != 2 or any(ref in paths or ref == marker for ref in references):
        raise PairBlocked("Reference paths must be separate from outputs and marker")
    return evidence, paths


def pair_references(specs):
    return [_record_reference(spec, count) for (_, _, count), spec in zip(PROFILES, specs)]


def _run_pair(spec_ch1, spec_ch2, evidence_dir, *, confirmations, hold, census, claim,
              assess, clock, marker, source_commit=None, experimental_s92_guard=False):
    specs = (spec_ch1, spec_ch2)
    evidence = Path(evidence_dir).absolute()
    marker = Path(marker).absolute()
    result = {
        "schema_version": "0.1-offline", "command": "ACQ-06 simulated pair S10",
        "status": "BLOCKED", "exit_code": 3, "start_utc": utc(), "end_utc": None,
        "source": "offline injected transport ONLY", "same_shot": None,
        "acquisition_coherence": "UNPROVEN", "hardware_qualified": False,
        "request_attempts": 0, "retries": 0, "marker": "NOT CHECKED",
        "host": None, "error": None,
        "cleanup": {"interface_claimed": False, "interface_released": False,
                    "lease_acquired": False, "lease_released": False},
        "preflight": window_result(), "inter_request_guard": window_result(),
        "channels": {key: {
            "instrument_path": "/mnt/udisk/" + filename,
            "reference": None, "request_attempts": 0, "response": window_result(),
            "integrity": "NOT CHECKED", "transfer_complete": False,
            "output_published": False, "output_path": str(Path(spec.output).absolute()),
            "file_bytes": None, "sha256": None, "data_frames": None, "terminal_sum8": None,
        } for (key, filename, size), spec in zip(PROFILES, specs)},
        "artifacts": {},
    }
    if source_commit is not None:
        result.update(schema_version="0.1-supervised", command="files retrieve-pair",
                      source="supervised Windows adapter", source_commit=source_commit)
    guard = result["inter_request_guard"]
    if experimental_s92_guard:
        result["schema_version"] = "0.2-s92-qualification"
        guard.update(qualification_guard_result())
    journal = None
    created = False
    stage = "evidence_setup"
    completed_bytes = {}

    def log(event, **fields):
        journal.write(json.dumps({"utc": utc(), "monotonic": clock(), "stage": stage,
                                  "event": event, **fields}) + "\n")
        journal.flush()

    try:
        if set(confirmations) != set(CONFIRMATIONS):
            raise PairBlocked("All six operator confirmations required")
        evidence, paths = pair_paths(specs, evidence, marker)
        for (key, _, _), ref, path in zip(PROFILES, pair_references(specs), paths):
            result["channels"][key].update(reference=ref, output_path=str(path))
        evidence.mkdir(exist_ok=False)
        created = True
        with (evidence / "journal.jsonl").open("x", encoding="utf-8") as journal:
            stage = "host_preflight"
            with record_cleanup(hold(), result["cleanup"], "lease_released"):
                result["cleanup"]["lease_acquired"] = True
                if os.path.lexists(marker):
                    result["marker"] = "EXISTING"
                    raise PairBlocked("Prior interrupted operation requires external review")
                result["marker"] = "ABSENT"
                result["host"] = assess(census(), os.getpid(), (sys.executable, sys._base_executable))
                if result["host"].get("blocked"):
                    raise PairBlocked("Host ownership check did not pass")
                marker.parent.mkdir(parents=True, exist_ok=True)
                with marker.open("x", encoding="utf-8") as stream:
                    result["marker"] = "RETAINED"
                    result["status"], result["exit_code"] = "ERROR", 4
                    json.dump({"utc": utc(), "operation": result["command"],
                               "source_commit": source_commit}, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                stage = "claim"
                with record_cleanup(claim(), result["cleanup"], "interface_released") as raw_link:
                    result["cleanup"]["interface_claimed"] = True
                    link = PairS10IO(raw_link)
                    stage = "preflight"
                    with (evidence / "preflight_rx.bin").open("xb") as rx:
                        observe(link, clock, clock(), rx, log, result["preflight"],
                                duration=WINDOW, rx_limit=MAX_RX_BYTES, require_silence=True)
                    result["preflight"]["complete"] = True
                    for index, ((key, filename, size), spec) in enumerate(zip(PROFILES, specs)):
                        if index:
                            stage = "inter_request_guard"
                            with (evidence / "inter_request_guard_rx.bin").open("xb") as rx:
                                if experimental_s92_guard:
                                    if not observe_qualification_guard(link, clock, rx, log, guard):
                                        break
                                else:
                                    observe(link, clock, clock(), rx, log, guard,
                                            duration=WINDOW, rx_limit=MAX_RX_BYTES, require_silence=True)
                                    guard["complete"] = True
                        # Fail closed on destinations being introduced during USB activity.
                        stage = key + "_destination"
                        for out in paths:
                            require_absent(out)
                        stage = key + "_write"
                        request = csv_request(filename)
                        with (evidence / (key + "_request_attempt.bin")).open("xb") as tx:
                            tx.write(request)
                            tx.flush()
                            os.fsync(tx.fileno())
                        log("write_attempt", channel=key, wire_hex=request.hex(" ").upper())
                        result["request_attempts"] += 1
                        result["channels"][key]["request_attempts"] = 1
                        written = link.write(request)
                        completed = clock()
                        log("write_result", channel=key, bytes_returned=written)
                        if written != len(request):
                            raise ReceiveError("ambiguous short S10 write")
                        stage = key + "_response"
                        with (evidence / (key + "_response_rx.bin")).open("xb") as rx:
                            wire = observe(link, clock, completed, rx, log,
                                           result["channels"][key]["response"],
                                           duration=WINDOW, rx_limit=MAX_RX_BYTES,
                                           primary_opcode=0x90, max_frame_bytes=CHUNK_BYTES + 6)
                        result["channels"][key]["response"]["complete"] = True
                        stage = key + "_validate"
                        data, reply = decode_file_reply(wire, size, result["channels"][key]["reference"]["sha256"])
                        result["channels"][key].update(
                            transfer_complete=True, integrity="PASS",
                            file_bytes=reply["received_bytes"], sha256=reply["sha256"],
                            data_frames=reply["data_frames"], terminal_sum8=reply["terminal_sum8"])
                        completed_bytes[key] = data
                        log("file_verified", channel=key, sha256=reply["sha256"], bytes=len(data))
                    stage = "interface_cleanup"
                stage = "lease_cleanup"
            stage = "marker_cleanup"
            marker.unlink()
            result["marker"] = "CLEARED"
            if experimental_s92_guard and guard["outcome"] == "INCONCLUSIVE":
                outcome = "INCONCLUSIVE", 7
            else:
                stage = "publish"
                for key, path in zip(("CH1", "CH2"), paths):
                    publish_file(path, completed_bytes[key],
                                 result["channels"][key]["reference"]["sha256"])
                    result["channels"][key]["output_published"] = True
                    log("output_published", channel=key)
                outcome = "PASS", 0
            stage = "journal_close"
        result["status"], result["exit_code"] = outcome
    except (Exception, KeyboardInterrupt) as exc:
        if result["cleanup"]["lease_acquired"] and not result["cleanup"]["lease_released"]:
            result["status"], result["exit_code"] = "ERROR", 4
        result["error"] = {"stage": stage, "type": type(exc).__name__, "message": str(exc)}
    result["end_utc"] = utc()
    if created:
        try:
            result["artifacts"] = {
                item.name: file_record(item) for item in evidence.iterdir() if item.is_file()
                and item.name != "pair_manifest.json"
            }
            write_json_new(evidence / "pair_manifest.json", result)
        except (Exception, KeyboardInterrupt) as exc:
            result["status"], result["exit_code"] = "ERROR", 4
            result["evidence_error"] = {"stage": "evidence_finalize", "type": type(exc).__name__,
                                        "message": str(exc)}
            if result["error"] is None:
                result["error"] = result["evidence_error"]
    return result
