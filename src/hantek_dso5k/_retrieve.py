"""Single-request retrieval of the qualified native CSV profile."""

from contextlib import contextmanager, ExitStack
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from time import perf_counter

from . import __version__
from ._file_replay import CHUNK_BYTES, MAX_FILE_BYTES, MAX_RX_BYTES, csv_request, decode_file_reply, publish_file
from ._host import Blocked, assess_host, host_snapshot, lease
from ._qualification import WINDOW
from ._receive import observe, ReceiveError
from ._screens import write_json_new
from ._session import installed_commit, state_path, utc
from ._winusb import ClaimedBulkIO, open_link


FILENAME = "WaveData163.csv"
INSTRUMENT_PATH = "/mnt/udisk/" + FILENAME
FILE_BYTES = 89_566
CONFIRMATIONS = ("device-identity", "single-client", "settled-history", "completed-file-present",
                 "no-active-save-recall", "independent-reference")


class SingleFileIO(ClaimedBulkIO):
    def __init__(self, link):
        super().__init__(link)
        self.request = csv_request(FILENAME)
        self.attempted = False

    def write(self, request):
        if self.attempted or request != self.request:
            raise ReceiveError("write outside the single S10 allowance")
        self.attempted = True
        return super().write(request)


@contextmanager
def record_cleanup(context, flags, key):
    stack = ExitStack()
    resource = stack.enter_context(context)
    try:
        yield resource
    finally:
        stack.close()
        flags[key] = True


def require_absent(path):
    if os.path.lexists(path):
        raise Blocked("Destination already exists")


def file_record(path):
    with path.open("rb") as handle:
        digest = sha256()
        size = 0
        while block := handle.read(16384):
            digest.update(block)
            size += len(block)
    return {"bytes": size, "sha256": digest.hexdigest()}


def window_result():
    return {"complete": False, "raw_bytes": 0, "recognized_status_frames": 0, "primary_frames_seen": 0}


def retrieve(instrument_path, output, reference, directory, *, max_bytes, timeout, confirmations,
             hold=lease, census=host_snapshot, claim=open_link, clock=perf_counter, marker=None):
    """Run one bounded retrieval, using injected transport only in offline tests."""
    output, reference, directory = (Path(path).absolute() for path in (output, reference, directory))
    result = {
        "schema_version": "1.0", "command_version": "1.0", "command": "files retrieve",
        "software_version": __version__, "source_commit": installed_commit(),
        "instrument_path": instrument_path, "output_path": str(output), "evidence_dir": str(directory),
        "start_utc": utc(), "end_utc": None, "status": "BLOCKED", "exit_code": 3, "error": None,
        "device": {"vid_pid": "049F:505A", "model": "DSO5102P", "firmware_baseline": "3.40.0 (151009.0)",
                   "firmware_verified_live": False, "interface": 0, "bulk_in": "0x81", "bulk_out": "0x02"},
        "host": None, "operator_confirmations": sorted(confirmations), "max_bytes": max_bytes,
        "transfer_timeout_seconds": timeout, "reference": None,
        "preflight": window_result(), "response": window_result(), "async_status_count": 0,
        "request_attempts": 0, "retries": 0, "transfer_complete": False,
        "integrity": "NOT CHECKED", "file_bytes": None, "sha256": None, "data_frames": None,
        "terminal_sum8": None, "output_published": False,
        "cleanup": {"interface_claimed": False, "interface_released": False, "lease_released": False},
        "marker": "NOT CHECKED", "artifacts": {},
    }
    stage = "evidence_setup"
    journal = None
    created_directory = False

    def log(event, **fields):
        journal.write(json.dumps({"event": event, "stage": stage, "utc": utc(),
                                  "monotonic": clock(), **fields}) + "\n")
        journal.flush()

    try:
        resolved_output, resolved_directory = output.resolve(), directory.resolve()
        if resolved_output == resolved_directory or resolved_directory in resolved_output.parents:
            raise Blocked("Output must be outside the new evidence directory")
        marker = Path(marker) if marker is not None else state_path() if os.name == "nt" else None
        if marker is not None and marker.resolve() in (resolved_output, resolved_directory):
            raise Blocked("Output and evidence must not use the interruption marker path")
        directory.mkdir(exist_ok=False)
        created_directory = True
        with (directory / "journal.jsonl").open("x", encoding="utf-8") as journal:
            stage = "host_preflight"
            if set(confirmations) != set(CONFIRMATIONS):
                raise Blocked("All six operator confirmations are required")
            if instrument_path != INSTRUMENT_PATH:
                raise Blocked("Only the exact qualified instrument path is supported")
            if not FILE_BYTES <= max_bytes <= MAX_FILE_BYTES or timeout != WINDOW:
                raise Blocked("Limits must accommodate the 89,566-byte profile and the full 12-second window")
            require_absent(output)
            if not output.parent.is_dir() or output.is_reserved():
                raise Blocked("Destination must have an existing local parent and a regular filename")
            with reference.open("rb") as handle:
                reference_data = handle.read(FILE_BYTES + 1)
            if len(reference_data) != FILE_BYTES:
                raise Blocked("The independent reference must be exactly 89,566 bytes")
            digest = sha256(reference_data).hexdigest()
            result["reference"] = {"bytes": FILE_BYTES, "sha256": digest}
            cleanup = result["cleanup"]
            with record_cleanup(hold(), cleanup, "lease_released"):
                if marker is None:
                    raise Blocked("The Windows interruption marker is required")
                if os.path.lexists(marker):
                    result["marker"] = "EXISTING"
                    raise Blocked("Prior command history requires external review")
                result["marker"] = "ABSENT"
                result["host"] = assess_host(census(), os.getpid(), (sys.executable, sys._base_executable))
                if result["host"]["blocked"]:
                    raise Blocked(result["host"]["blocked"])
                marker.parent.mkdir(parents=True, exist_ok=True)
                with marker.open("x", encoding="utf-8") as handle:
                    result["marker"] = "RETAINED"
                    json.dump({"run_id": directory.name, "utc": utc(), "operation": "files retrieve"}, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                stage = "claim"
                result["status"], result["exit_code"] = "ERROR", 4
                with record_cleanup(claim(), cleanup, "interface_released") as claimed:
                    cleanup["interface_claimed"] = True
                    link = SingleFileIO(claimed)
                    stage = "receive_preflight"
                    with (directory / "preflight_rx.bin").open("xb") as rx:
                        observe(link, clock, clock(), rx, log, result["preflight"], duration=WINDOW,
                                rx_limit=MAX_RX_BYTES)
                    result["preflight"]["complete"] = True
                    require_absent(output)
                    stage = "write"
                    with (directory / "response_rx.bin").open("xb") as rx:
                        with (directory / "request_attempt.bin").open("xb") as tx:
                            tx.write(link.request)
                        log("write_attempt", wire_hex=link.request.hex(" ").upper())
                        result["request_attempts"] = 1
                        written = link.write(link.request)
                        completed = clock()
                        log("write_result", bytes_returned=written, completed=completed)
                        if written != len(link.request):
                            raise ReceiveError("short USB write")
                        stage = "response"
                        wire = observe(link, clock, completed, rx, log, result["response"], duration=timeout,
                                       rx_limit=MAX_RX_BYTES, primary_opcode=0x90, max_frame_bytes=CHUNK_BYTES + 6)
                    result["response"]["complete"] = True
                    stage = "validate"
                    result["integrity"] = "FAIL"
                    data, reply = decode_file_reply(wire, FILE_BYTES, digest)
                    result.update(transfer_complete=True, integrity="PASS", file_bytes=reply["received_bytes"],
                                  sha256=reply["sha256"], data_frames=reply["data_frames"],
                                  terminal_sum8=reply["terminal_sum8"])
                    log("reply_verified", **reply)
                    stage = "interface_cleanup"
                stage = "lease_cleanup"
            stage = "marker_cleanup"
            marker.unlink()
            result["marker"] = "CLEARED"
            stage = "publish"
            publish_file(output, data, digest)
            result["output_published"] = True
            log("output_published")
            result["status"], result["exit_code"] = "PASS", 0
    except (Exception, KeyboardInterrupt) as exc:
        result["error"] = {"stage": stage, "type": type(exc).__name__, "message": str(exc)}
        if hasattr(exc, "reason"):
            result["error"]["reason"] = exc.reason
    result["async_status_count"] = sum(result[name]["recognized_status_frames"] for name in ("preflight", "response"))
    result["end_utc"] = utc()
    if created_directory:
        try:
            if journal is not None:
                result["artifacts"] = {name: file_record(directory / name) for name in
                                       ("journal.jsonl", "preflight_rx.bin", "response_rx.bin", "request_attempt.bin")
                                       if (directory / name).is_file()}
            write_json_new(directory / "transfer_manifest.json", result)
        except (OSError, ValueError) as exc:
            result["status"], result["exit_code"] = "ERROR", 4
            result["error"] = {"stage": "evidence_finalize", "type": type(exc).__name__, "message": str(exc)}
    return result
