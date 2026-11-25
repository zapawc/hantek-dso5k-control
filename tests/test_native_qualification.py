from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hantek_dso5k._framing import frame, STATUS
from hantek_dso5k._native_qualification import REQUEST, _run


SCALE = dict(volts_per_division=0.5, probe_attenuation=1, vertical_offset_counts=0,
             seconds_per_division=0.001, display_divisions=12, sample_count=12, sample_interval_s=0.001,
             time_origin_s=0, time_reference="synthetic sample zero", viewport="synthetic",
             verified=True, provenance="synthetic fixture")
FIXTURE = dict(setup="synthetic running fixture", running=True, math_enabled=False,
               channel_scaling={"CH1": SCALE, "CH2": SCALE}, expectations={
                   "CH1": dict(vpp_v=[0.45, 0.55], period_s=[0.0039, 0.0041], reference="known CH1"),
                   "CH2": dict(vpp_v=[0.95, 1.05], period_s=[0.0039, 0.0041], reference="known CH2"),
               })


def slot(amplitude, count=12):
    return frame(0x82, b"\x00" + count.to_bytes(3, "little")) + frame(
        0x82, b"\x01\x04" + bytes([0, 0, amplitude, amplitude]) * 3
    )


MATH_OFF = frame(0x82, b"\x03\x02")
END = frame(0x82, b"\x02\x04")
WIRE = slot(25) + slot(50) + MATH_OFF + END


class FakeTransport:
    def __init__(self, response=(WIRE,), *, startup=(), write_outcome=7):
        self.now = 0.0
        self.response = iter(response)
        self.startup = iter(startup)
        self.write_outcome = write_outcome
        self.writes = []
        self.reads = []

    def clock(self):
        return self.now

    def write(self, request):
        self.writes.append(request)
        if isinstance(self.write_outcome, Exception):
            raise self.write_outcome
        return self.write_outcome

    def read(self, size, *, timeout_ms):
        self.reads.append((self.now, size, timeout_ms, bool(self.writes)))
        entry = next(self.response if self.writes else self.startup, TimeoutError())
        delay = timeout_ms / 1000 if isinstance(entry, TimeoutError) else 0.001
        if isinstance(entry, tuple):
            delay, entry = entry
        self.now += delay
        if isinstance(entry, BaseException):
            raise entry
        return entry


class NativeQualificationTests(unittest.TestCase):
    def session(self, link, *, fixture=FIXTURE, continued=True, claim_error=False, lease_error=False,
                prior_marker=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.marker = root / "marker.json"
        self.evidence = root / "evidence"
        if prior_marker:
            self.marker.write_bytes(b"previous")
        self.actions = []

        @contextmanager
        def hold():
            self.actions.append("lease")
            try:
                yield
            finally:
                self.actions.append("release lease")
                if lease_error:
                    raise OSError("lease release failed")

        @contextmanager
        def claim():
            self.assertTrue(self.marker.is_file())
            self.actions.append("claim")
            try:
                yield link
            finally:
                self.actions.append("release interface")
                if claim_error:
                    raise OSError("interface release failed")

        def observe_panel():
            self.assertEqual(self.actions[-1], "release interface")
            self.actions.append("observe panel")
            return continued

        return _run(self.evidence, json.dumps(fixture).encode(), source="synthetic", authorization="test",
                    identity="synthetic", hold=hold, claim=claim, screen=lambda: {"blocked": None},
                    clock=link.clock, marker=self.marker, continued=observe_panel)

    def check_attempts(self, result, link, attempts):
        self.assertEqual(result["request_attempts"], attempts)
        self.assertEqual(result["retries"], 0)
        self.assertEqual(link.writes, [REQUEST] * attempts)

    def test_success_separates_transport_measurement_and_cleanup(self):
        link = FakeTransport()
        result = self.session(link)
        self.assertEqual(result["status"], "PASS", result["errors"])
        self.assertEqual(result["marker"], "CLEARED")
        self.assertFalse(self.marker.exists())
        self.assertTrue(all(result["cleanup"].values()))
        self.assertEqual(result["instrument_continuation"], "CONFIRMED")
        self.assertEqual(result["channel_attribution"], "slot_order_and_distinct_voltage")
        self.assertEqual(result["acquisition_coherence"], "UNPROVEN")
        self.assertTrue(result["measurement_pass"])
        self.assertEqual(self.actions, ["lease", "claim", "release interface", "observe panel", "release lease"])
        self.assertEqual(json.loads((self.evidence / "native_manifest.json").read_bytes()), result)
        self.check_attempts(result, link, 1)
        self.assertEqual(REQUEST, frame(0x02, b"\x01\x04"))

    def test_exact_status_and_fragmented_coalesced_response(self):
        wire = STATUS + WIRE + STATUS
        link = FakeTransport([wire[:3], wire[3:12], wire[12:]])
        result = self.session(link)
        self.assertEqual(result["status"], "PASS_WITH_STATUS", result["errors"])
        self.assertTrue(result["primary_complete"])
        self.assertEqual([entry["raw_offset"] for entry in result["response"]["statuses"]], [0, len(wire) - 7])
        self.assertEqual((self.evidence / "response_rx.bin").read_bytes(), wire)
        self.assertTrue(all(size % 512 == 0 and size <= 16384 and timeout <= 250
                            for _, size, timeout, _ in link.reads))
        self.check_attempts(result, link, 1)

    def test_strict_startup_including_exact_status(self):
        link = FakeTransport(startup=[STATUS])
        result = self.session(link)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(self.marker.exists())
        self.assertEqual((self.evidence / "startup_rx.bin").read_bytes(), STATUS)
        self.check_attempts(result, link, 0)

    def test_short_or_exceptional_write_is_never_retried(self):
        for outcome in (6, OSError("write failed")):
            with self.subTest(outcome=outcome):
                link = FakeTransport(write_outcome=outcome)
                result = self.session(link)
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["transport"], "FAIL")
                self.assertTrue(self.marker.exists())
                self.assertFalse(any(read[-1] for read in link.reads))
                self.check_attempts(result, link, 1)

    def test_bad_classification_preserves_later_bytes_until_original_deadline(self):
        for bad in (b"bad", frame(0x92, b"\x01\x01"), WIRE[:-1] + bytes([WIRE[-1] ^ 1])):
            with self.subTest(bad=bad):
                link = FakeTransport([bad, WIRE])
                result = self.session(link)
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["transport"], "PASS")
                self.assertIsNotNone(result["response"]["classification_error"])
                self.assertTrue(result["response"]["complete"])
                self.assertEqual((self.evidence / "response_rx.bin").read_bytes(), bad + WIRE)
                self.assertTrue(self.marker.exists())
                self.check_attempts(result, link, 1)

    def test_missing_terminal_or_slot_or_unexpected_math_fails(self):
        for wire in (WIRE[:-len(END)], slot(25) + frame(0x82, b"\x03\x01") + MATH_OFF + END,
                     slot(25) + slot(50) + slot(75) + END, slot(25, count=13) + slot(50) + MATH_OFF + END):
            with self.subTest(wire=wire):
                link = FakeTransport([wire])
                result = self.session(link)
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["transport"], "PASS")
                self.assertFalse(result["primary_complete"])
                self.assertTrue(self.marker.exists())
                self.check_attempts(result, link, 1)

    def test_measurement_mismatch_and_unconfirmed_continuation(self):
        for wire, continued in ((slot(50) + slot(25) + MATH_OFF + END, True), (WIRE, False)):
            with self.subTest(continued=continued):
                link = FakeTransport([wire])
                result = self.session(link, continued=continued)
                self.assertEqual(result["status"], "FAIL")
                self.assertTrue(result["primary_complete"])
                self.assertEqual(result["measurement_pass"], not continued)
                self.assertTrue(self.marker.exists())
                self.check_attempts(result, link, 1)

    def test_receive_frame_and_sample_exhaustion(self):
        for responses, message in (([bytes(16384), bytes(16384)], "RX budget"),
                                   ([STATUS * 257], "frame budget"),
                                   ([slot(25, 10001)], "sample bound")):
            with self.subTest(message=message):
                link = FakeTransport(responses)
                result = self.session(link)
                self.assertEqual(result["transport"], "FAIL")
                self.assertIn(message, result["errors"][0]["detail"])
                self.assertEqual((self.evidence / "response_rx.bin").read_bytes(), b"".join(responses))
                self.assertTrue(self.marker.exists())
                self.check_attempts(result, link, 1)

    def test_deadline_uses_write_completion_without_extensions(self):
        link = FakeTransport([(11.875, STATUS)])
        result = self.session(link)
        self.assertTrue(result["response"]["complete"])
        self.assertEqual(result["response"]["deadline"] - result["write_completed"]["monotonic_s"], 12)
        self.assertLessEqual(result["response"]["end"]["monotonic_s"], result["response"]["deadline"] + 0.001)
        response_reads = [entry for entry in link.reads if entry[-1]]
        self.assertLess(response_reads[-1][2], 250)
        self.assertEqual(result["status"], "FAIL")
        self.check_attempts(result, link, 1)

    def test_late_bytes_and_late_timeout_both_fail(self):
        for entry in (WIRE, TimeoutError()):
            with self.subTest(entry=entry):
                link = FakeTransport([(12.25, entry)])
                result = self.session(link)
                self.assertEqual(result["transport"], "FAIL")
                self.assertFalse(result["response"]["complete"])
                self.assertTrue(self.marker.exists())
                self.check_attempts(result, link, 1)

    def test_cancel_and_transport_error_end_reads(self):
        for error in (KeyboardInterrupt(), OSError("transport failed")):
            with self.subTest(error=error):
                link = FakeTransport([STATUS, error, WIRE])
                result = self.session(link)
                self.assertEqual(result["transport"], "FAIL")
                self.assertEqual((self.evidence / "response_rx.bin").read_bytes(), STATUS)
                self.assertEqual(len([read for read in link.reads if read[-1]]), 2)
                self.assertTrue(self.marker.exists())
                self.check_attempts(result, link, 1)

    def test_interface_and_lease_cleanup_failures_keep_marker(self):
        for flag in ("claim_error", "lease_error"):
            with self.subTest(flag=flag):
                link = FakeTransport()
                result = self.session(link, **{flag: True})
                self.assertEqual(result["status"], "FAIL")
                self.assertTrue(self.marker.exists())
                self.assertFalse(all(result["cleanup"].values()))
                self.check_attempts(result, link, 1)

    def test_prior_marker_and_invalid_fixture_block_claim(self):
        link = FakeTransport()
        result = self.session(link, prior_marker=True)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(self.marker.read_bytes(), b"previous")
        self.assertNotIn("claim", self.actions)
        self.check_attempts(result, link, 0)
        result = self.session(link, fixture={**FIXTURE, "math_enabled": True})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(self.actions)
        self.check_attempts(result, link, 0)

    def test_failed_marker_removal_leaves_no_stale_pass_manifest(self):
        link = FakeTransport()
        with patch.object(Path, "unlink", side_effect=OSError("marker removal blocked")):
            result = self.session(link)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["marker"], "RETAINED")
        self.assertTrue(self.marker.exists())
        self.assertEqual(json.loads((self.evidence / "native_manifest.json").read_bytes()), result)
        pending = json.loads((self.evidence / "native_manifest.pending.json").read_bytes())
        self.assertEqual(pending["status"], "FINALIZING")
        self.check_attempts(result, link, 1)
