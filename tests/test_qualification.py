from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from hantek_dso5k._framing import frame, ProtocolError, STATUS
from hantek_dso5k._graphics import CHUNKS
from hantek_dso5k._host import Blocked
from hantek_dso5k._session import qualify
from hantek_dso5k._waveform import ch1_waveform, convert_samples

S81 = frame(0x81, bytes(208))
GRAPHICS = b"".join(frame(0xA0, b"\1" + bytes(n)) for n in CHUNKS) + frame(0xA0, b"\2\0")
SCREEN = [GRAPHICS[i:i+7000] for i in range(0, len(GRAPHICS), 7000)]


class Replay:
    def __init__(self, replies, initial=()):
        self.queue = list(initial)
        self.now = 0.
        self.writes = []
        self.replies = iter(replies)
        self.claims = self.releases = 0

    def clock(self):
        return self.now

    def write(self, endpoint, data, timeout):
        self.writes.append((endpoint, data, self.now))
        self.queue = list(next(self.replies))
        return len(data)

    def read(self, endpoint, size, timeout):
        if self.queue:
            self.now += .00001
            return self.queue.pop(0)
        self.now += timeout / 1000
        raise TimeoutError

    @contextmanager
    def claim(self):
        self.claims += 1
        try:
            yield SimpleNamespace(device=self, timeout_error=TimeoutError)
        finally:
            self.releases += 1


@contextmanager
def lease():
    yield


@contextmanager
def held_by_other():
    raise Blocked("Cooperative USB lease unavailable")
    yield


def snapshot():
    return {"processes": [{"pid": os.getpid(), "ppid": 0, "exe": sys.executable,
                           "created": "2026-11-19T17:00:00Z", "argv": []}],
            "devices": [{"instance": "USB\\VID_049F&PID_505A\\FIXTURE", "status": "OK", "service": "WinUSB"}],
            "assignment": "not_shared"}


class QualificationTests(unittest.TestCase):
    def run_fixture(self, replay, *, held=lease, census=snapshot, prior=False, confirmed=True):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        marker = root / "unsettled.json"
        if prior:
            marker.write_text("{}", encoding="utf-8")
        output = root / "session"
        result = qualify(output, confirmed=confirmed, history_confirmed=True, hold=held, census=census,
                         claim=replay.claim, clock=replay.clock, marker=marker)
        saved = json.loads((output / "capture_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(result, saved)
        return result, output, marker.exists()

    def test_full_four_steps_and_previews(self):
        replay = Replay([[S81[:3], S81[3:]], SCREEN, SCREEN, [S81]])
        report, path, unsettled = self.run_fixture(replay)
        self.assertEqual(report["exit_code"], 0)
        self.assertFalse(unsettled)
        self.assertEqual((replay.claims, replay.releases), (1, 1))
        self.assertEqual([data.hex() for _, data, _ in replay.writes],
                         ["5302000156", "5302002075", "5302002075", "5302000156"])
        self.assertTrue(all(b[2]-a[2] >= 12.55 for a,b in zip(replay.writes,replay.writes[1:])))
        self.assertEqual((path/"screen_first_preview.png").read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(report["request_attempts"], 4)
        self.assertIsNone(report["shared_acquisition"])

    def test_classified_status_keeps_strict_failure(self):
        report, _, unsettled = self.run_fixture(Replay([[S81+STATUS], SCREEN, SCREEN, [S81]]))
        self.assertEqual(report["exit_code"], 5)
        self.assertTrue(report["sequence_complete"])
        self.assertFalse(unsettled)
        self.assertEqual(report["steps"][0]["reply"]["status_frames"], 1)

    def test_duplicate_early_unknown_bad_partial_and_timeout_stop(self):
        for first in ([S81+STATUS+STATUS], [STATUS+S81], [S81+frame(0xA1)], [S81[:-1]],
                      [S81[:-1]+bytes([S81[-1]^0xff])], []):
            with self.subTest(first=[len(x) for x in first]):
                report, _, unsettled = self.run_fixture(Replay([first]))
                self.assertEqual(report["exit_code"], 4)
                self.assertEqual(report["request_attempts"], 1)
                self.assertTrue(unsettled)

    def test_aggregate_discrepancy_separate_from_framing(self):
        aggregate_bad = SCREEN[:-1] + [SCREEN[-1][:-7] + frame(0xA0, b"\2\1")]
        report, _, _ = self.run_fixture(Replay([[S81], aggregate_bad, SCREEN, [S81]]))
        self.assertEqual(report["exit_code"], 5)
        self.assertEqual(report["steps"][1]["reply"]["graphics_framing"], "PASS")
        self.assertEqual(report["steps"][1]["reply"]["aggregate"], "FAIL")

    def test_receive_preflight_blocks_tx(self):
        replay = Replay([], initial=[STATUS])
        report, path, unsettled = self.run_fixture(replay)
        self.assertEqual(report["request_attempts"], 0)
        self.assertEqual(replay.releases, 1)
        self.assertEqual((path/"preflight_rx.bin").read_bytes(), STATUS)
        self.assertTrue(unsettled)

    def test_short_write_retains_interruption_and_never_retries(self):
        replay = Replay([])
        attempts = []
        def short_write(endpoint, data, timeout):
            attempts.append((endpoint, data))
            return len(data) - 1
        replay.write = short_write
        report, _, unsettled = self.run_fixture(replay)
        self.assertEqual((report["exit_code"], report["request_attempts"], replay.releases), (4, 1, 1))
        self.assertEqual(len(attempts), 1)
        self.assertTrue(unsettled)

    def test_status_in_guard_prevents_next_write(self):
        replay = Replay([[S81]])
        read = replay.read
        def late_status(endpoint, size, timeout):
            if replay.writes and replay.now - replay.writes[-1][2] >= 12:
                return STATUS
            return read(endpoint, size, timeout)
        replay.read = late_status
        report, path, unsettled = self.run_fixture(replay)
        self.assertEqual(report["request_attempts"], 1)
        self.assertEqual((path/"screen_first_guard_rx.bin").read_bytes(), STATUS)
        self.assertTrue(unsettled)

    def test_lease_confirmations_history_and_assignment_block_claim(self):
        def shared():
            return {**snapshot(), "assignment": "shared"}
        for kwargs in ({"held": held_by_other}, {"prior": True}, {"confirmed": False}, {"census": shared}):
            replay = Replay([])
            report, _, _ = self.run_fixture(replay, **kwargs)
            self.assertEqual(report["exit_code"], 3)
            self.assertEqual((replay.claims, len(replay.writes)), (0, 0))

    def test_settings_diff_volatile_vs_conflicting(self):
        for index, code in ((20, 0), (27, 0), (22, 4)):
            value = bytearray(208)
            value[index] = 1
            report, _, _ = self.run_fixture(Replay([[S81], SCREEN, SCREEN, [frame(0x81, value)]]))
            self.assertEqual(report["exit_code"], code)

    def test_waveform_count_source_and_signed_conversion(self):
        wire = frame(0x82,b"\0\2\0\0") + frame(0x82,b"\1\0\x80\x7f") + frame(0x82,b"\2\0")
        status, values = ch1_waveform([wire[:4],wire[4:]])
        self.assertEqual((status,values),("complete",(-128,127)))
        self.assertEqual(convert_samples((-25,25),.01,2,10,-25)[1][-1],40)
        with self.assertRaises(ProtocolError):
            ch1_waveform([wire[:-1]])
        with self.assertRaises(ProtocolError):
            ch1_waveform([frame(0x82,b"\0\1\0\0"),wire[9:]])
