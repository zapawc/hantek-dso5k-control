import math
import unittest

from fixture import decode_pulses, expected_pulses, inspect_capture


class PulseCodecTests(unittest.TestCase):
    def test_event_and_channel_identity(self):
        for channel in (1, 2):
            for event_id in (1, 2, 0xA531, 0xFFFF):
                with self.subTest(channel=channel, event_id=event_id):
                    expected = expected_pulses(event_id, channel)
                    measured = [(rise + 73, fall + 86) for rise, fall in expected]
                    self.assertEqual(decode_pulses(measured), (channel, event_id))

    def test_missing_or_misplaced_pulse(self):
        pulses = expected_pulses(123, 1)
        with self.assertRaisesRegex(ValueError, "pulse count"):
            decode_pulses(pulses[:-1])
        rise, fall = pulses[-1]
        pulses[-1] = (rise + 95, fall + 95)
        with self.assertRaisesRegex(ValueError, "spacing"):
            decode_pulses(pulses)

    def test_small_repeatable_timer_overhead_does_not_accumulate_as_a_missing_slot(self):
        pulses = expected_pulses(1126, 1)
        measured = [(rise + 7 * slot, fall + 7 * slot) for slot, (rise, fall) in enumerate(pulses)]
        self.assertEqual(decode_pulses(measured, tolerance_us=40), (1, 1126))

    def test_rejects_ambiguous_bit_width(self):
        pulses = expected_pulses(1, 2)
        rise, _ = pulses[-1]
        for width in (300, math.nan):
            with self.subTest(width=width):
                pulses[-1] = (rise, rise + width)
                with self.assertRaisesRegex(ValueError, "bit width"):
                    decode_pulses(pulses)

    def test_reserved_zero_event(self):
        pulses = expected_pulses(1, 1)
        rise, _ = pulses[-1]
        pulses[-1] = (rise, rise + 200)
        with self.assertRaisesRegex(ValueError, "reserved"):
            decode_pulses(pulses)

    def test_capture_rejects_stale_event_and_swapped_channels(self):
        def observed(channel):
            pulses = expected_pulses(177, channel)
            return {"interval_s": 2e-5, "clipped": False,
                    "rising_edges_s": [start * 1e-6 for start, _ in pulses],
                    "falling_edges_s": [end * 1e-6 for _, end in pulses]}

        capture = {"availability": {"CH1": True, "CH2": True},
                   "channels": {"CH1": observed(1), "CH2": observed(2)}}
        self.assertTrue(all(ch["accepted"] for ch in inspect_capture(capture, 177).values()))
        self.assertFalse(any(ch["accepted"] for ch in inspect_capture(capture, 178).values()))
        capture["channels"] = {"CH1": observed(2), "CH2": observed(1)}
        self.assertFalse(any(ch["accepted"] for ch in inspect_capture(capture, 177).values()))
