import math
import unittest

from fixture import decode_pulses, expected_pulses


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
