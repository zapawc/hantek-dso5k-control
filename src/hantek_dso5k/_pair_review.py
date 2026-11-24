"""Offline integrity review of two independently completed Hantek file transfers.

No device access, retry, acquisition-control or same-shot inference occurs here.
"""
from hashlib import sha256
from pathlib import Path
import json
import re

from ._file_replay import CHUNK_BYTES, MAX_RX_BYTES, csv_request, decode_file_reply
from ._receive import ReceiveStream
from ._screens import write_json_new

_PROFILES = {
    "CH1": ("/mnt/udisk/WaveData163.csv", "WaveData163.csv", 89566),
    "CH2": ("/mnt/udisk/WaveData164.csv", "WaveData164.csv", 89193),
}
_HEX = re.compile(r"[0-9a-fA-F]{64}\Z")


class PairReviewError(ValueError):
    pass


def _read(path, limit):
    with Path(path).open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise PairReviewError("Evidence file exceeds its size limit")
    return payload


def _transfer(channel, manifest_path, csv_path, evidence_dir, expected_sha256):
    if not isinstance(expected_sha256, str) or not _HEX.fullmatch(expected_sha256):
        raise PairReviewError(f"{channel}: independent SHA-256 is required")
    expected_sha256 = expected_sha256.lower()
    path, basename, size = _PROFILES[channel]
    report = json.loads(_read(manifest_path, 128 * 1024))
    if not isinstance(report, dict):
        raise PairReviewError(f"{channel}: malformed manifest")
    if not isinstance(report.get("reference"), dict) or not isinstance(report.get("cleanup"), dict) or not isinstance(report.get("preflight"), dict) or not isinstance(report.get("response"), dict) or not isinstance(report.get("artifacts"), dict):
        raise PairReviewError(f"{channel}: invalid manifest subrecords")
    expected_flags = (
        report.get("status") == "PASS", report.get("exit_code") == 0,
        report.get("instrument_path") == path,
        report.get("request_attempts") == 1, report.get("retries") == 0,
        report.get("transfer_complete") is True,
        report.get("integrity") == "PASS", report.get("output_published") is True,
        report.get("file_bytes") == size,
        report.get("sha256") == expected_sha256,
        report.get("reference", {}).get("sha256") == expected_sha256,
        report.get("reference", {}).get("bytes") == size,
        report.get("marker") == "CLEARED",
        report.get("cleanup", {}).get("interface_released") is True,
        report.get("cleanup", {}).get("lease_released") is True,
        report.get("transfer_timeout_seconds") == 12,
        report.get("preflight", {}).get("complete") is True,
        report.get("response", {}).get("complete") is True,
    )
    if not all(expected_flags):
        raise PairReviewError(f"{channel}: manifest does not meet strict completion conditions")
    if channel == "CH2" and (report["preflight"].get("raw_bytes") != 0 or
                             report["preflight"].get("recognized_status_frames") != 0):
        raise PairReviewError("CH2: silent preflight requirement not met")
    evidence = Path(evidence_dir)
    records = report.get("artifacts", {})
    raw_files = {}
    for filename, cap in (("request_attempt.bin", 256), ("response_rx.bin", MAX_RX_BYTES),
                          ("preflight_rx.bin", MAX_RX_BYTES)):
        payload = _read(evidence / filename, cap)
        stamp = records.get(filename)
        if (not isinstance(stamp, dict) or stamp.get("bytes") != len(payload) or
                stamp.get("sha256") != sha256(payload).hexdigest()):
            raise PairReviewError(f"{channel}: {filename} disagrees with manifest")
        raw_files[filename] = payload
    if raw_files["request_attempt.bin"] != csv_request(basename):
        raise PairReviewError(f"{channel}: request did not target exact file")
    if len(raw_files["preflight_rx.bin"]) != report["preflight"].get("raw_bytes"):
        raise PairReviewError(f"{channel}: preflight size differs")
    preflight_report = {"recognized_status_frames": 0, "primary_frames_seen": 0}
    preflight_stream = ReceiveStream(lambda *args, **kwargs: None, preflight_report,
                                     primary_opcode=None, max_frame_bytes=0)
    preflight_stream.feed(raw_files["preflight_rx.bin"], 1, 0.0, "offline-replay")
    preflight_stream.finish()
    if preflight_report["recognized_status_frames"] != report["preflight"].get("recognized_status_frames"):
        raise PairReviewError(f"{channel}: preflight status accounting differs")

    if channel == "CH2" and raw_files["preflight_rx.bin"]:
        raise PairReviewError("CH2: preflight was not silent")
    wire = raw_files["response_rx.bin"]
    if len(wire) != report["response"].get("raw_bytes"):
        raise PairReviewError(f"{channel}: response size differs")
    parsed = {"recognized_status_frames": 0, "primary_frames_seen": 0}
    stream = ReceiveStream(lambda *args, **kwargs: None, parsed,
                           primary_opcode=0x90, max_frame_bytes=CHUNK_BYTES + 6)
    stream.feed(wire, 1, 0.0, "offline-replay")
    stream.finish()
    reconstructed, details = decode_file_reply(bytes(stream.file_wire), size, expected_sha256)
    csv = _read(csv_path, size)
    if len(csv) != size or csv != reconstructed:
        raise PairReviewError(f"{channel}: CSV is not identical to reconstructed response")
    if (parsed["recognized_status_frames"] != report["response"].get("recognized_status_frames") or
            parsed["primary_frames_seen"] != report["response"].get("primary_frames_seen") or
            details["data_frames"] != report.get("data_frames") or
            details["terminal_sum8"] != report.get("terminal_sum8")):
        raise PairReviewError(f"{channel}: frame accounting differs")
    return {
        "path": path, "bytes": size, "sha256": expected_sha256,
        "s90_data_frames": details["data_frames"],
        "s92_status_frames": parsed["recognized_status_frames"],
        "start_utc": report.get("start_utc"), "end_utc": report.get("end_utc"),
        "software_version": report.get("software_version"),
        "source_commit": report.get("source_commit"),
        "qualification_scope": "single existing exported CSV",
    }


def review_pair(ch1_manifest, ch1_csv, ch1_evidence, ch1_sha256,
                ch2_manifest, ch2_csv, ch2_evidence, ch2_sha256, output):
    """Review independent completed transfers without opening any USB device."""
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise PairReviewError("Output already exists; overwrite prohibited")
    if not output.parent.is_dir():
        raise PairReviewError("Output parent directory does not exist")
    ch1 = _transfer("CH1", ch1_manifest, ch1_csv, ch1_evidence, ch1_sha256)
    ch2 = _transfer("CH2", ch2_manifest, ch2_csv, ch2_evidence, ch2_sha256)
    result = {
        "schema_version": "1.0", "command": "offline pair-review",
        "status": "PASS", "individual_file_integrity": "PASS",
        "acquisition_coherence": "UNPROVEN",
        "same_shot": None, "automated_paired_retrieval_qualified": False,
        "ch1": ch1, "ch2": ch2,
        "limits": [
            "Historical files and independent transfers; no shared trigger identity established",
            "No hardware access, transfer sequencing, or acquisition controls were exercised",
            "Manifest source_commit may be null; externally verified provenance is required",
            "A matching time axis does not prove simultaneous samples",
        ],
    }
    write_json_new(output, result)
    return result
