from collections import deque
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hantek_dso5k.cli import main
from hantek_dso5k._file_replay import CHUNK_BYTES, MAX_RX_BYTES, csv_request, publish_file
from hantek_dso5k._framing import STATUS, frame
from hantek_dso5k._retrieve import CONFIRMATIONS, FILE_BYTES, FILENAME, INSTRUMENT_PATH, SingleFileIO, retrieve


class Timeout(Exception):
    pass


class FakeLink:
    timeout_error = Timeout

    def __init__(self, response=(), preflight=(), write_error=None):
        self.device = self
        self.response = deque(response)
        self.preflight = deque(preflight)
        self.writes = []
        self.clock = 0.0
        self.write_error = write_error

    def read(self, endpoint, size, *, timeout):
        assert endpoint == 0x81
        queue = self.response if self.writes else self.preflight
        data = queue.popleft() if queue else None
        if data is None:
            self.clock += timeout / 1000
            raise Timeout()
        if isinstance(data, Exception):
            raise data
        if len(data) > size:
            queue.appendleft(data[size:])
        self.clock += 0.002
        return data[:size]

    def write(self, endpoint, data, *, timeout):
        self.writes.append((endpoint, data, self.clock))
        if self.write_error:
            raise self.write_error
        return len(data)


def file_frames(data):
    parts = [frame(0x90, b"\x01" + data[i:i + CHUNK_BYTES]) for i in range(0, len(data), CHUNK_BYTES)]
    return [*parts, frame(0x90, bytes([2, sum(data) & 255]))]


class RetrieveTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.reference = self.root / "reference.csv"
        self.output = self.root / "output.csv"
        self.marker = self.root / "marker.json"
        self.directory = self.root / "evidence"
        self.data = (b"t,v\r\n" * FILE_BYTES)[:FILE_BYTES]
        self.reference.write_bytes(self.data)
        self.frames = file_frames(self.data)
        self.wire = b"".join(self.frames)
        self.claims = 0
        self.release_error = None

    def session(self, link, **overrides):
        @contextmanager
        def hold():
            yield

        @contextmanager
        def claim():
            self.claims += 1
            yield link
            if self.release_error:
                raise self.release_error

        args = dict(instrument_path=INSTRUMENT_PATH, output=self.output, reference=self.reference,
                    directory=self.directory, max_bytes=FILE_BYTES, timeout=12, confirmations=CONFIRMATIONS,
                    hold=hold, claim=claim, census=lambda: {}, clock=lambda: link.clock, marker=self.marker)
        args.update(overrides)
        with patch("hantek_dso5k._retrieve.assess_host", return_value={"blocked": None}):
            result = retrieve(**args)
        self.assertEqual(len(link.writes), result["request_attempts"])
        self.assertLessEqual(len(link.writes), 1)
        self.assertEqual(json.loads((args["directory"] / "transfer_manifest.json").read_text()), result)
        return result

    def test_integrated_status_evidence_and_verified_publication(self):
        preflight = [STATUS[:3], None, STATUS[3:] + STATUS]
        response = [self.frames[0] + STATUS[:1],
                    STATUS[1:] + b"".join(self.frames[1:]), *([None] * 42), STATUS]
        link = FakeLink(response, preflight)
        result = self.session(link)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(self.output.read_bytes(), self.data)
        self.assertEqual(result["sha256"], sha256(self.data).hexdigest())
        self.assertEqual(result["data_frames"], 9)
        self.assertEqual(result["async_status_count"], 4)
        self.assertEqual(result["marker"], "CLEARED")
        self.assertFalse(self.marker.exists())
        self.assertEqual(link.writes[0][:2], (0x02, csv_request(FILENAME)))
        self.assertGreaterEqual(link.writes[0][2], 12)
        self.assertGreaterEqual(link.clock - link.writes[0][2], 12)
        self.assertEqual((self.directory / "preflight_rx.bin").read_bytes(), STATUS * 2)
        self.assertEqual((self.directory / "response_rx.bin").read_bytes(),
                         b"".join(data for data in response if data is not None))
        schema_path = Path(__file__).resolve().parents[1] / "schemas/file-transfer-v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(set(schema["required"]), set(result))
        self.assertEqual(result["schema_version"], schema["properties"]["schema_version"]["const"])

    def test_integrity_and_transport_failure_matrix(self):
        changed = self.data[:-1] + b"?"
        cases = {
            "checksum": ([STATUS[:-1] + b"\x00"], "response"),
            "terminal": ([self.wire[:-7]], "validate"),
            "partial": ([self.wire[:-1]], "response"),
            "additional": ([self.wire + self.frames[-1]], "validate"),
            "unknown": ([frame(0x99)], "response"),
            "disconnection": ([self.frames[0], OSError("device disconnected")], "response"),
            "no_data_timeout": ([], "validate"),
            "bad_sha256": (file_frames(changed), "validate"),
            "aggregate": ([self.wire[:-7] + frame(0x90, bytes([2, self.frames[-1][5] ^ 1]))], "validate"),
            "status_only": ([STATUS], "validate"),
            "rx_budget": ([STATUS * (MAX_RX_BYTES // len(STATUS) + 100)], "response"),
        }
        for label, (response, stage) in cases.items():
            with self.subTest(label=label):
                marker = self.root / (label + ".marker")
                result = self.session(FakeLink(response), directory=self.root / label, marker=marker)
                self.assertEqual(result["exit_code"], 4)
                self.assertEqual(result["error"]["stage"], stage)
                self.assertEqual(result["marker"], "RETAINED")
                self.assertTrue(marker.exists())
                self.assertFalse(self.output.exists())
        self.assertLessEqual(result["response"]["raw_bytes"], MAX_RX_BYTES)

    def test_preflight_failures_exclude_writes(self):
        for name, traffic in (("partial", STATUS[:-1]), ("unknown", self.frames[0]),
                              ("wrong_status", frame(0x92, b"\x01\x01"))):
            with self.subTest(name=name):
                result = self.session(FakeLink(preflight=[traffic]), directory=self.root / name,
                                      marker=self.root / (name + ".marker"))
                self.assertEqual(result["error"]["stage"], "receive_preflight")
                self.assertEqual(result["request_attempts"], 0)

    def test_limits_and_path_block_before_claim(self):
        for name, options in (("size", {"max_bytes": FILE_BYTES - 1}), ("time", {"timeout": 1}),
                              ("path", {"instrument_path": "/mnt/udisk/WaveData164.csv"})):
            with self.subTest(name=name):
                result = self.session(FakeLink(), directory=self.root / name, **options)
                self.assertEqual(result["exit_code"], 3)
                self.assertEqual(self.claims, 0)

    def test_persisted_marker_and_existing_output_block_before_claim(self):
        self.marker.write_bytes(b"prior")
        result = self.session(FakeLink())
        self.assertEqual(result["marker"], "EXISTING")
        self.assertEqual(self.marker.read_bytes(), b"prior")
        self.output.write_bytes(b"existing")
        result = self.session(FakeLink(), directory=self.root / "collision")
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(self.output.read_bytes(), b"existing")
        self.assertEqual(self.claims, 0)

    def test_output_collision_after_transfer(self):
        def collide(output, data, digest):
            output.write_bytes(b"existing")
            publish_file(output, data, digest)

        with patch("hantek_dso5k._retrieve.publish_file", side_effect=collide):
            result = self.session(FakeLink([self.wire]))
        self.assertEqual(result["exit_code"], 4)
        self.assertTrue(result["transfer_complete"])
        self.assertFalse(result["output_published"])
        self.assertEqual(self.output.read_bytes(), b"existing")

    def test_cleanup_error_and_ambiguous_write_retain_marker(self):
        self.release_error = OSError("interface release failed")
        result = self.session(FakeLink([self.wire]))
        self.assertFalse(result["cleanup"]["interface_released"])
        self.assertFalse(self.output.exists())
        self.assertTrue(self.marker.exists())
        self.release_error = None
        result = self.session(FakeLink(write_error=OSError("short transmission")),
                              directory=self.root / "short", marker=self.root / "new.marker")
        self.assertEqual(result["request_attempts"], 1)
        self.assertEqual(result["marker"], "RETAINED")

    def test_adapter_blocks_extra_writes(self):
        transport = SingleFileIO(FakeLink())
        with self.assertRaises(RuntimeError):
            transport.write(frame(0x01))
        transport.write(csv_request(FILENAME))
        with self.assertRaises(RuntimeError):
            transport.write(csv_request(FILENAME))

    def test_actual_cli_missing_confirmations_returns_manifest_without_claim(self):
        stdout = StringIO()
        def offline(*args, **kwargs):
            def forbidden():
                self.fail("CLI attempted hardware-session setup without confirmations")
            return retrieve(*args, **kwargs, hold=forbidden, claim=forbidden, census=forbidden,
                            marker=self.marker)

        with redirect_stdout(stdout), redirect_stderr(StringIO()), patch("hantek_dso5k.cli.retrieve", offline):
            exit_code = main(["files", "retrieve", "--instrument-path", INSTRUMENT_PATH,
                              "--reference", os.fspath(self.reference), "--output", os.fspath(self.output),
                              "--evidence-dir", os.fspath(self.directory)])
        result = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 3)
        self.assertEqual(result["request_attempts"], 0)
        self.assertEqual(result["command"], "files retrieve")
