import unittest

from fixture import expected_pulses, inspect_capture
from logic_reference import analyze_reference, compare_reference


def record_event(event_id):
    data = bytearray(20000)
    trigger_start = 75
    for index in range(trigger_start, trigger_start + 500):
        data[index] = 4
    for channel in (1, 2):
        for rise, fall in expected_pulses(event_id, channel):
            for index in range(trigger_start + rise, trigger_start + fall):
                data[index] |= 1 << (channel - 1)
    return data


class LogicReferenceTests(unittest.TestCase):
    def test_decodes_trigger_and_both_channels(self):
        report = analyze_reference(bytes(record_event(135)), 135)
        self.assertEqual(report["trigger_sample_index"], 75)
        self.assertEqual(report["trigger_width_us"], 500)
        self.assertEqual(report["channels"]["CH2"]["pulses_from_trigger_us"][0], (1000, 1800))

    def test_detects_stale_or_incomplete_observation(self):
        record = record_event(135)
        with self.assertRaisesRegex(ValueError, "does not match"):
            analyze_reference(bytes(record), 136)
        record[1700] |= 4
        with self.assertRaisesRegex(ValueError, "one reference trigger"):
            analyze_reference(bytes(record), 135)

    def test_compares_scope_samples_with_the_independent_event(self):
        channels = {}
        for number in (1, 2):
            pulses = expected_pulses(135, number)
            channels[f"CH{number}"] = {"interval_s": 20e-6, "clipped": False,
                "rising_edges_s": [(start + 5000) / 1e6 for start, _ in pulses],
                "falling_edges_s": [(end + 5000) / 1e6 for _, end in pulses]}
        scope = inspect_capture({"channels": channels, "availability": dict.fromkeys(channels, True)}, 135)
        result = compare_reference(analyze_reference(bytes(record_event(135)), 135), scope, 135)
        self.assertEqual(result["combined_width_sample_bound_us"], {"CH1": 21, "CH2": 21})
        self.assertAlmostEqual(result["channels"]["CH1"]["scope_minus_logic_pulse_widths_us"][0], 0)
        self.assertEqual(result["reference_median_skew_us"], 0)
