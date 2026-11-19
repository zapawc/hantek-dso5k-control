"""Generated fixtures for S90 replay, with no hardware or historical capture dependency."""

from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hantek_dso5k import _file_replay as transfer
from hantek_dso5k._framing import STATUS, frame
from hantek_dso5k.cli import main

NAME = "WaveData1.csv"
PAYLOAD = b"#size=2\r\n1.0,0.0\r\n2.0,1.0\r\n"
DIGEST = sha256(PAYLOAD).hexdigest()


def reply(data):
    chunk_size = transfer.CHUNK_BYTES
    frames = [frame(0x90, b"\x01" + data[i:i + chunk_size])
              for i in range(0, len(data) + 1, chunk_size)]
    frames.append(frame(0x90, bytes([2, sum(data) & 255])))
    return b"".join(frames)


class FileReplayTests(unittest.TestCase):
    def rejects(self, reason, wire, size=len(PAYLOAD), digest=DIGEST):
        with self.assertRaises(transfer.FileReplayError) as caught:
            transfer.decode_file_reply(wire, size, digest)
        self.assertEqual(caught.exception.reason, reason)

    def test_exact_normalized_request(self):
        self.assertEqual(transfer.csv_request(NAME), bytes.fromhex(
            "53 1B 00 10 00 2F 6D 6E 74 2F 75 64 69 73 6B 2F 57 61 76 65 44 61 74 61 31 2E 63 73 76 32"))
        self.assertEqual(len(transfer.csv_request("WaveData12345678.csv")), 37)

    def test_unsafe_or_ambiguous_names(self):
        for name in ("../WaveData1.csv", "/mnt/udisk/WaveData1.csv", "WaveData*.csv",
                     "WaveData1.csv\x00", "WaveData123456789.csv", "WaveData1.csv:other"):
            with self.subTest(name=name), self.assertRaises(transfer.FileReplayError):
                transfer.csv_request(name)

    def test_full_and_tail_chunks(self):
        data = bytes(range(255)) * 342
        result, meta = transfer.decode_file_reply(reply(data), len(data), sha256(data).hexdigest())
        self.assertEqual(result, data)
        self.assertEqual(meta["data_frames"], 9)

    def test_exact_multiple_requires_empty_tail(self):
        data = bytes(transfer.CHUNK_BYTES)
        decoded, meta = transfer.decode_file_reply(reply(data), len(data), sha256(data).hexdigest())
        self.assertEqual(decoded, data)
        self.assertEqual(meta["data_frames"], 2)
        missing_tail = frame(0x90, b"\x01" + data) + frame(0x90, b"\x02\x00")
        self.rejects("incomplete_file", missing_tail, len(data), sha256(data).hexdigest())

    def test_short_or_fragment_only_and_timeout_transcripts(self):
        self.rejects("missing_terminal", b"")
        self.rejects("missing_terminal", reply(PAYLOAD)[:-7])
        self.rejects("invalid_framing", reply(PAYLOAD)[:-1])
        self.rejects("incomplete_file", frame(0x90, b"\x02\x00"))
        self.rejects("unexpected_chunk_size", reply(PAYLOAD), len(PAYLOAD) + 1)

    def test_checksum_and_integrity_failures(self):
        valid = reply(PAYLOAD)
        self.rejects("invalid_framing", valid[:-1] + bytes([valid[-1] ^ 1]))
        self.rejects("aggregate_mismatch",
                     valid[:-7] + frame(0x90, bytes([2, (sum(PAYLOAD) + 1) & 255])))
        self.rejects("sha256_mismatch", reply(PAYLOAD[::-1]))

    def test_unsolicited_unexpected_and_trailing_data(self):
        valid = reply(PAYLOAD)
        self.rejects("unsolicited_status", STATUS + valid)
        self.rejects("unsolicited_status", valid + STATUS)
        self.rejects("unsolicited_status", valid[:-7] + STATUS + valid[-7:])
        self.rejects("unexpected_response", frame(0xA2, b"?") + valid)
        self.rejects("unexpected_file_subtype", frame(0x90, b"\x00") + valid)
        self.rejects("trailing_frame", valid + valid[-7:])
        self.rejects("invalid_framing", valid + b"\x53")

    def test_rejects_out_of_budget_inputs(self):
        self.rejects("invalid_expected_size", b"", 0)
        self.rejects("invalid_expected_size", b"", transfer.MAX_FILE_BYTES + 1)
        self.rejects("invalid_expected_sha256", b"", digest="missing")
        self.rejects("rx_budget_exceeded", bytes(transfer.MAX_RX_BYTES + 1))

    def test_cli_replay_and_duplicate_destination(self):
        with TemporaryDirectory() as root:
            src = Path(root) / "rx.bin"
            src.write_bytes(reply(PAYLOAD))
            dst = Path(root) / NAME
            args = ["offline", "file-replay", str(src), "--filename", NAME, "--expected-bytes", str(len(PAYLOAD)),
                    "--expected-sha256", DIGEST.upper(), "--output", str(dst)]
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(main(args), 0)
            meta = json.loads(output.getvalue())
            self.assertEqual((meta["schema_version"], meta["sha256"]), ("1.0", DIGEST))
            self.assertEqual(dst.read_bytes(), PAYLOAD)
            with redirect_stdout(StringIO()) as output, redirect_stderr(StringIO()):
                self.assertEqual(main(args), 4)
            self.assertEqual(json.loads(output.getvalue())["error"], "destination_exists")
            self.assertEqual(dst.read_bytes(), PAYLOAD)

    def test_duplicate_filename_reference_is_never_autoselected(self):
        with TemporaryDirectory() as root:
            work = Path(root)
            src = work / "rx.bin"
            src.write_bytes(reply(PAYLOAD[::-1]))
            dst = work / NAME
            with self.assertRaises(transfer.FileReplayError):
                transfer.replay_file(src, dst, NAME, len(PAYLOAD), DIGEST)
            self.assertFalse(dst.exists())
