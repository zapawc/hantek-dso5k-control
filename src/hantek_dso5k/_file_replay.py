"""Offline replay for the original firmware's bounded S10 CSV file reply."""

from hashlib import sha256
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from ._framing import ProtocolError, STATUS, frame, read_frames

MAX_FILE_BYTES = 96 * 1024
MAX_RX_BYTES = 100 * 1024
CHUNK_BYTES = 10_208
CSV_NAME = re.compile(r"WaveData[0-9]{1,8}\.csv", re.ASCII)
SHA256_HEX = re.compile(r"[0-9a-fA-F]{64}", re.ASCII)


class FileReplayError(ValueError):
    """Stable reason and diagnostic for a rejected offline transfer."""

    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason = reason


def csv_request(filename):
    if CSV_NAME.fullmatch(filename) is None:
        raise FileReplayError("invalid_filename", "expected one exact WaveData<number>.csv basename")
    return frame(0x10, b"\x00" + ("/mnt/udisk/" + filename).encode("ascii"))


def decode_file_reply(wire, expected_bytes, expected_sha256):
    if not 1 <= expected_bytes <= MAX_FILE_BYTES:
        raise FileReplayError("invalid_expected_size", "expected bytes must be within 1 to 98,304")
    if SHA256_HEX.fullmatch(expected_sha256) is None:
        raise FileReplayError("invalid_expected_sha256", "expected 64 hexadecimal digits")
    if len(wire) > MAX_RX_BYTES:
        raise FileReplayError("rx_budget_exceeded", "response exceeds 102,400 bytes")
    full, tail = divmod(expected_bytes, CHUNK_BYTES)
    sizes = (CHUNK_BYTES,) * full + (tail,)
    content = bytearray()
    chunks = 0
    terminal = None
    try:
        for offset, opcode, body in read_frames((wire,)):
            if wire[offset:offset + len(STATUS)] == STATUS:
                raise FileReplayError("unsolicited_status", f"uncorrelated S92 at offset {offset}")
            if terminal is not None:
                raise FileReplayError("trailing_frame", f"unexpected frame at offset {offset}")
            if opcode != 0x90 or not body:
                raise FileReplayError("unexpected_response", f"expected S90 at offset {offset}")
            if body[0] == 1:
                if chunks >= len(sizes) or len(body) - 1 != sizes[chunks]:
                    raise FileReplayError("unexpected_chunk_size", f"unexpected data size at offset {offset}")
                content.extend(body[1:])
                chunks += 1
            elif body[0] == 2 and len(body) == 2:
                if chunks != len(sizes) or len(content) != expected_bytes:
                    raise FileReplayError("incomplete_file", "terminal arrived before the expected chunks")
                terminal = body[1]
            else:
                raise FileReplayError("unexpected_file_subtype", f"unexpected file body at offset {offset}")
    except ProtocolError as exc:
        raise FileReplayError("invalid_framing", str(exc)) from exc

    if terminal is None:
        raise FileReplayError("missing_terminal", "response ended before the S90 terminal")
    if terminal != (sum(content) & 255):
        raise FileReplayError("aggregate_mismatch", "terminal sum differs from file data")
    digest = sha256(content).hexdigest()
    if digest != expected_sha256.lower():
        raise FileReplayError("sha256_mismatch", "file differs from the independent reference")
    return bytes(content), {"received_bytes": len(content), "data_frames": chunks,
                            "terminal_sum8": f"{terminal:02X}", "sha256": digest}


def replay_file(source, destination, filename, expected_bytes, expected_sha256):
    """Validate one saved response window and publish the exact bytes without replacement."""
    csv_request(filename)
    with source.open("rb") as handle:
        wire = handle.read(MAX_RX_BYTES + 1)
    data, metadata = decode_file_reply(wire, expected_bytes, expected_sha256)
    publish_file(destination, data, metadata["sha256"])
    return {"status": "PASS", "filename_reference": filename, "output_path": str(destination),
            "rx_bytes": len(wire), "expected_bytes": expected_bytes, **metadata}


def publish_file(destination, data, expected_sha256):
    """Verify a staged local file before publishing it without replacement."""
    if destination.exists() or destination.is_symlink():
        raise FileReplayError("destination_exists", "destination must be absent")
    with TemporaryDirectory(prefix=".hantek-replay-", dir=destination.parent) as scratch:
        staged = Path(scratch) / "payload"
        with staged.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        staged_data = staged.read_bytes()
        if len(staged_data) != len(data) or sha256(staged_data).hexdigest() != expected_sha256:
            raise FileReplayError("staging_mismatch", "staged bytes failed verification")
        try:
            if os.name == "nt":
                os.rename(staged, destination)
            else:
                os.link(staged, destination)
        except FileExistsError as exc:
            raise FileReplayError("destination_exists", "destination must be absent") from exc
