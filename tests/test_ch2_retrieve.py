from contextlib import contextmanager, redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hantek_dso5k._file_replay import MAX_FILE_BYTES, MAX_RX_BYTES, csv_request
from hantek_dso5k._framing import STATUS, frame
from hantek_dso5k._retrieve import CONFIRMATIONS, FILE_BYTES, INSTRUMENT_PATH, SingleFileIO, retrieve
from hantek_dso5k.cli import main
from test_retrieve import FakeLink, file_frames


CH2_PATH = "/mnt/udisk/WaveData164.csv"
CH2_SIZE = 89_193
CH1_REQUEST = bytes.fromhex(
    "53 1D 00 10 00 2F 6D 6E 74 2F 75 64 69 73 6B 2F 57 61 76 65 44 61 74 61 31 36 33 2E 63 73 76 9D"
)
CH2_REQUEST = bytes.fromhex(
    "53 1D 00 10 00 2F 6D 6E 74 2F 75 64 69 73 6B 2F 57 61 76 65 44 61 74 61 31 36 34 2E 63 73 76 9E"
)


class ShortWriteLink(FakeLink):
    def write(self, endpoint, data, *, timeout):
        return super().write(endpoint, data, timeout=timeout) - 1


class CH2RetrieveTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        prefix = (bytes(range(251)) * (CH2_SIZE // 251 + 1))[:CH2_SIZE - 1]
        self.data = prefix + bytes([(0x2F - sum(prefix)) & 255])
        self.reference = self.root / "synthetic.csv"
        self.reference.write_bytes(self.data)
        self.digest = sha256(self.reference.read_bytes()).hexdigest()
        self.frames = file_frames(self.data)

    def retrieve_with(self, link, label="run", *, release_fault=None, **overrides):
        work = self.root / label
        work.mkdir()
        output = work / "result.csv"
        marker = work / "unsettled.json"
        if overrides.pop("preexisting_output", False):
            output.write_bytes(b"keep")
        if overrides.pop("preexisting_marker", False):
            marker.write_bytes(b"prior")
        history = []

        @contextmanager
        def hold():
            history.append("lease")
            yield
            if release_fault == "lease":
                raise OSError("lease release failed")

        def census():
            history.append("census")
            return {}

        @contextmanager
        def claim():
            history.append("claim")
            yield link
            if release_fault == "interface":
                raise OSError("interface release failed")

        args = dict(instrument_path=CH2_PATH, output=output, reference=self.reference, directory=work / "evidence",
                    max_bytes=CH2_SIZE, timeout=12, confirmations=CONFIRMATIONS, experimental_ch2=True,
                    expected_sha256=self.digest, hold=hold, census=census, claim=claim, marker=marker,
                    clock=lambda: link.clock)
        args.update(overrides)
        with patch("hantek_dso5k._retrieve.assess_host", return_value={"blocked": None}):
            result = retrieve(**args)
        self.assertEqual(json.loads((args["directory"] / "transfer_manifest.json").read_text()), result)
        self.assertEqual(result["request_attempts"], len(link.writes))
        self.assertLessEqual(len(link.writes), 1)
        self.assertEqual(result["retries"], 0)
        return result, work, output, marker, history

    def test_ch2_exact_frame_tail_terminal_and_fragmented_status(self):
        self.assertEqual(csv_request("WaveData164.csv"), CH2_REQUEST)
        self.assertEqual(len(CH2_REQUEST), 32)
        self.assertEqual([len(packet) - 6 for packet in self.frames[:-1]], [10_208] * 8 + [7_529])
        self.assertEqual(self.frames[-1], bytes.fromhex("53 04 00 90 02 2F 18"))
        self.assertEqual(sum(map(len, self.frames)), 89_254)
        wire = b"".join(self.frames)
        response = [wire[:2], wire[2:10_214] + STATUS[:2], STATUS[2:] + wire[10_214:]]
        link = FakeLink(response)
        result, work, output, marker, history = self.retrieve_with(link, expected_sha256=self.digest.upper())
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(link.writes[0][:2], (0x02, CH2_REQUEST))
        self.assertGreaterEqual(link.writes[0][2], 12)
        self.assertGreaterEqual(link.clock - link.writes[0][2], 12)
        self.assertEqual(history, ["lease", "census", "claim"])
        self.assertEqual(result["response"]["primary_frames_seen"], 10)
        self.assertEqual(result["response"]["raw_bytes"], 89_261)
        self.assertEqual(result["async_status_count"], 1)
        self.assertEqual(result["data_frames"], 9)
        self.assertEqual(result["file_bytes"], CH2_SIZE)
        self.assertEqual(result["terminal_sum8"], "2F")
        self.assertEqual(result["integrity"], "PASS")
        self.assertEqual(sha256(output.read_bytes()).hexdigest(), self.digest)
        self.assertEqual(output.read_bytes(), self.data)
        self.assertEqual((work / "evidence/response_rx.bin").read_bytes(), b"".join(response))
        self.assertFalse(marker.exists())
        schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/file-transfer-v1.schema.json").read_text())
        self.assertEqual(set(schema["required"]), set(result))
        self.assertEqual(schema["properties"]["schema_version"]["const"], result["schema_version"])

    def test_profile_digest_reference_and_limit_gates_before_lease(self):
        short = self.root / "short.csv"
        short.write_bytes(self.data[:-1])
        wrong = self.root / "wrong.csv"
        wrong.write_bytes(self.data[:-1] + bytes([self.data[-1] ^ 1]))
        cases = {
            "no-opt-in": {"experimental_ch2": False},
            "no-hash": {"expected_sha256": None},
            "malformed-hash": {"expected_sha256": "g" * 64},
            "wrong-hash": {"expected_sha256": "0" * 64},
            "same-length-wrong-reference": {"reference": wrong},
            "missing-reference": {"reference": self.root / "missing.csv"},
            "wrong-length": {"reference": short},
            "bare-name": {"instrument_path": "WaveData164.csv"},
            "case-alias": {"instrument_path": "/mnt/udisk/wavedata164.csv"},
            "traversal": {"instrument_path": "/mnt/udisk/x/../WaveData164.csv"},
            "another-file": {"instrument_path": "/mnt/udisk/WaveData165.csv"},
            "low-cap": {"max_bytes": CH2_SIZE - 1},
            "high-cap": {"max_bytes": MAX_FILE_BYTES + 1},
            "short-window": {"timeout": 11},
            "confirmation-missing": {"confirmations": CONFIRMATIONS[:-1]},
            "existing-output": {"preexisting_output": True},
        }
        for label, options in cases.items():
            with self.subTest(label=label):
                link = FakeLink()
                result, _, output, marker, history = self.retrieve_with(link, label, **options)
                self.assertEqual(result["exit_code"], 3, result)
                self.assertEqual(result["error"]["stage"], "host_preflight")
                self.assertEqual(history, [])
                self.assertFalse(marker.exists())
                if label == "existing-output":
                    self.assertEqual(output.read_bytes(), b"keep")
                else:
                    self.assertFalse(output.exists())

    def test_existing_marker_blocks_before_census_or_claim(self):
        result, _, output, marker, history = self.retrieve_with(FakeLink(), preexisting_marker=True)
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(result["marker"], "EXISTING")
        self.assertEqual(marker.read_bytes(), b"prior")
        self.assertEqual(history, ["lease"])
        self.assertFalse(output.exists())

    def test_any_ch2_preflight_bytes_block_even_exact_s92(self):
        for label, data in (("exact-status", STATUS), ("fragment", STATUS[:2]), ("early-s90", self.frames[0])):
            with self.subTest(label=label):
                result, work, output, marker, _ = self.retrieve_with(FakeLink(preflight=[data]), label)
                self.assertEqual(result["error"]["stage"], "receive_preflight")
                self.assertEqual(result["request_attempts"], 0)
                self.assertEqual(result["preflight"]["recognized_status_frames"], 0)
                self.assertEqual((work / "evidence/preflight_rx.bin").read_bytes(), data)
                self.assertFalse(output.exists())
                self.assertTrue(marker.exists())

    def test_response_matrix_preserves_failure_and_marker(self):
        wire = b"".join(self.frames)
        altered = self.data[:-1] + bytes([self.data[-1] ^ 1])
        cases = {
            "read-timeout": ([], "validate"),
            "checksum": ([wire[:-1] + bytes([wire[-1] ^ 1])], "response"),
            "unknown-opcode": ([frame(0x99)], "response"),
            "unknown-status": ([frame(0x92, b"\x01\x01")], "response"),
            "missing-terminal": ([wire[:-7]], "validate"),
            "duplicate-terminal": ([wire + self.frames[-1]], "validate"),
            "trailing-frame": ([wire + self.frames[0]], "validate"),
            "partial": ([wire[:-1]], "response"),
            "disconnect": ([self.frames[0], OSError("disconnected")], "response"),
            "wrong-aggregate": ([wire[:-7] + frame(0x90, b"\x02\x30")], "validate"),
            "wrong-hash": (file_frames(altered), "validate"),
            "only-async": ([STATUS], "validate"),
            "over-budget": ([STATUS * (MAX_RX_BYTES // len(STATUS) + 100)], "response"),
        }
        for label, (response, stage) in cases.items():
            with self.subTest(label=label):
                result, _, output, marker, _ = self.retrieve_with(FakeLink(response), label)
                self.assertEqual(result["exit_code"], 4)
                self.assertEqual(result["error"]["stage"], stage)
                self.assertEqual(result["marker"], "RETAINED")
                self.assertTrue(marker.exists())
                self.assertFalse(output.exists())

    def test_ambiguous_write_never_retries_or_claims_completion(self):
        for label, link in (("short", ShortWriteLink()), ("fault", FakeLink(write_error=OSError("write failed")))):
            with self.subTest(label=label):
                result, _, output, marker, _ = self.retrieve_with(link, label)
                self.assertEqual(result["exit_code"], 4)
                self.assertEqual(result["error"]["stage"], "write")
                self.assertEqual([entry[1] for entry in link.writes], [CH2_REQUEST])
                self.assertFalse(result["transfer_complete"])
                self.assertFalse(output.exists())
                self.assertTrue(marker.exists())

    def test_cleanup_failure_blocks_publication_even_after_integrity_pass(self):
        for fault in ("interface", "lease"):
            with self.subTest(fault=fault):
                result, _, output, marker, _ = self.retrieve_with(FakeLink(self.frames), fault, release_fault=fault)
                self.assertEqual(result["exit_code"], 4)
                self.assertEqual(result["integrity"], "PASS")
                self.assertFalse(result["cleanup"][fault + "_released"])
                self.assertFalse(output.exists())
                self.assertTrue(marker.exists())

    def test_selected_guard_rejects_ch1_and_second_ch2_write_after_error(self):
        link = FakeLink(write_error=OSError("write failed"))
        io = SingleFileIO(link, CH2_PATH)
        with self.assertRaises(RuntimeError):
            io.write(CH1_REQUEST)
        with self.assertRaises(OSError):
            io.write(CH2_REQUEST)
        with self.assertRaises(RuntimeError):
            io.write(CH2_REQUEST)
        self.assertEqual([entry[1] for entry in link.writes], [CH2_REQUEST])

    def test_cli_keeps_ch1_default_and_forwards_only_explicit_ch2_options(self):
        for path, options in ((INSTRUMENT_PATH, []),
                              (CH2_PATH, ["--experimental-ch2", "--expected-sha256", self.digest,
                                          "--max-bytes", str(CH2_SIZE)])):
            with self.subTest(path=path):
                args = ["files", "retrieve", "--instrument-path", path, "--reference", "sample.csv",
                        "--output", "result.csv", "--evidence-dir", "diagnostics",
                        *["--confirm-" + name for name in CONFIRMATIONS], *options]
                with patch("hantek_dso5k.cli.retrieve", return_value={"exit_code": 3}) as runner, \
                     redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    self.assertEqual(main(args), 3)
                values = runner.call_args.kwargs
                self.assertEqual(values["timeout"], 12)
                self.assertEqual(values["confirmations"], list(CONFIRMATIONS))
                self.assertEqual(values["max_bytes"], CH2_SIZE if options else FILE_BYTES)
                self.assertEqual(values["experimental_ch2"], bool(options))
                self.assertEqual(values["expected_sha256"], self.digest if options else None)
        self.assertEqual(SingleFileIO(FakeLink()).request, CH1_REQUEST)
