"""Offline ACQ-05 tests; synthetic transfers only, no USB library or hardware."""
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hantek_dso5k._file_replay import CHUNK_BYTES, csv_request
from hantek_dso5k._framing import frame, STATUS
from hantek_dso5k._pair_review import PairReviewError, review_pair


class PairReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.args = []
        for channel, filename, size in (
            ("CH1", "WaveData163.csv", 89566),
            ("CH2", "WaveData164.csv", 89193),
        ):
            root = self.root / channel
            root.mkdir()
            evidence = root / "evidence"
            evidence.mkdir()
            payload = (b"sample,time,value\r\n" * (size // 19 + 2))[:size]
            csv = root / filename
            csv.write_bytes(payload)
            digest = sha256(payload).hexdigest()
            frames = [frame(0x90, b"\x01" + payload[i:i+CHUNK_BYTES])
                      for i in range(0, size, CHUNK_BYTES)]
            frames.append(frame(0x90, bytes([2, sum(payload) & 255])))
            response = b"".join(frames) + (STATUS if channel == "CH2" else b"")
            artifacts = {}
            for name, data in (
                ("request_attempt.bin", csv_request(filename)),
                ("preflight_rx.bin", b""),
                ("response_rx.bin", response),
            ):
                (evidence / name).write_bytes(data)
                artifacts[name] = {"bytes": len(data), "sha256": sha256(data).hexdigest()}
            manifest = {
                "status": "PASS", "exit_code": 0,
                "instrument_path": "/mnt/udisk/" + filename,
                "request_attempts": 1, "retries": 0,
                "transfer_complete": True, "integrity": "PASS",
                "output_published": True, "file_bytes": size,
                "sha256": digest, "reference": {"bytes": size, "sha256": digest},
                "marker": "CLEARED", "cleanup": {"interface_released": True, "lease_released": True},
                "transfer_timeout_seconds": 12,
                "preflight": {"complete": True, "raw_bytes": 0, "recognized_status_frames": 0},
                "response": {"complete": True, "raw_bytes": len(response),
                             "recognized_status_frames": 1 if channel == "CH2" else 0,
                             "primary_frames_seen": len(frames)},
                "data_frames": len(frames) - 1,
                "terminal_sum8": f"{sum(payload) & 255:02X}",
                "start_utc": "2026-11-20T22:00:00Z",
                "end_utc": "2026-11-20T22:00:25Z",
                "artifacts": artifacts,
            }
            path = root / "transfer_manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            self.args.extend((path, csv, evidence, digest))
        self.output = self.root / "pair-review.json"

    def run_review(self):
        return review_pair(*self.args, self.output)

    def test_complete_offline_review_keeps_same_shot_unknown(self):
        result = self.run_review()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["acquisition_coherence"], "UNPROVEN")
        self.assertIsNone(result["same_shot"])
        self.assertFalse(result["automated_paired_retrieval_qualified"])
        self.assertEqual(result["ch2"]["s92_status_frames"], 1)
        self.assertEqual(json.loads(self.output.read_text()), result)

    def test_tampered_csv_blocks_output(self):
        csv = self.args[1]
        data = bytearray(csv.read_bytes())
        data[20] ^= 1
        csv.write_bytes(data)
        with self.assertRaises(PairReviewError):
            self.run_review()
        self.assertFalse(self.output.exists())

    def test_tampered_raw_response_blocks_output(self):
        raw = self.args[6] / "response_rx.bin"
        data = bytearray(raw.read_bytes())
        data[20] ^= 1
        raw.write_bytes(data)
        with self.assertRaises(PairReviewError):
            self.run_review()
        self.assertFalse(self.output.exists())

    def test_not_cleared_marker_blocks_output(self):
        manifest_path = self.args[0]
        manifest = json.loads(manifest_path.read_text())
        manifest["marker"] = "RETAINED"
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(PairReviewError):
            self.run_review()
        self.assertFalse(self.output.exists())

    def test_wrong_independent_digest_blocks_output(self):
        self.args[7] = "0" * 64
        with self.assertRaises(PairReviewError):
            self.run_review()
        self.assertFalse(self.output.exists())

    def test_existing_destination_is_never_overwritten(self):
        self.output.write_text("preserve")
        with self.assertRaises(PairReviewError):
            self.run_review()
        self.assertEqual(self.output.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
