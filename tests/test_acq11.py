import unittest
from unittest.mock import Mock

from hantek_dso5k._acq11 import (
    RUN_STOP, SINGLE, _fixture_evidence, _scope_volts, analyze, describe, parse_listing, patch_settings,
    read_frozen_csv, workflow,
)


def settings(state):
    result = bytearray(208)
    for offset, value in {0: 1, 1: 7, 5: 1, 10: 1, 11: 7, 15: 1,
                          160: 15, 206: 48, 207: 1, 20: state}.items():
        result[offset] = value
    return bytes(result)


def fixture_waveform():
    return {
        "channels": {"CH1": {"vpp_v": 2.64, "bnc_vpp_v": .264, "frequency_hz": 1000, "clipped": False},
                     "CH2": {"vpp_v": 2.56, "bnc_vpp_v": 2.56, "frequency_hz": 1000, "clipped": False}},
        "availability": {"CH1": True, "CH2": True, "MATH": False},
    }


class WorkbenchAcquisitionTests(unittest.TestCase):
    def test_allowlisted_patch_changes_only_requested_settings(self):
        original = settings(0)
        updated = patch_settings(original, {11: 9, 15: 0})
        self.assertEqual([i for i, (a, b) in enumerate(zip(original, updated)) if a != b], [11, 15])
        self.assertEqual(describe(updated)["channels"]["CH2"]["bnc_v_per_div"], 1.)
        self.assertEqual(describe(updated)["channels"]["CH2"]["software_probe"], 1)
        self.assertEqual(describe(updated)["seconds_per_division"], .0002)
        for edits in ({188: 1}, {20: 3}, {11: 13}):
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                patch_settings(original, edits)

    def test_analysis_keeps_probe_compensation_and_timing_separate(self):
        values, metrics = analyze([0, 0, 25, 25] * 3, bncvdiv=.5, probe=10, offset=0, interval=.00025)
        self.assertEqual(max(values), 5.)
        self.assertEqual(metrics["bnc_vpp_v"], .5)
        self.assertAlmostEqual(metrics["frequency_hz"], 1000.)
        self.assertAlmostEqual(metrics["duty_cycle"], .5)
        self.assertEqual(len(metrics["rising_edges_s"]), 3)
        self.assertEqual(len(metrics["falling_edges_s"]), 2)
        self.assertFalse(metrics["clipped"])

    def test_relative_probe_attribution_is_independent_of_nominal_voltage(self):
        evidence = _fixture_evidence(fixture_waveform())
        self.assertTrue(evidence["probe_assignment_plausible"])
        self.assertEqual(evidence["voltage"], "OUTSIDE_NOMINAL_RANGE")
        self.assertEqual(_scope_volts("250mV"), .25)
        self.assertEqual(_scope_volts("2.64V"), 2.64)
        self.assertIsNone(_scope_volts("?"))

    def test_full_workflow_preserves_single_before_restore(self):
        configured = patch_settings(settings(0), {11: 9, 15: 0})
        running = patch_settings(settings(3), {11: 9, 15: 0})
        frozen = patch_settings(settings(5), {11: 9, 15: 0})
        link = Mock(settled=True)
        phases = {"initial": settings(0), "running": running, "single_state_1": frozen,
                  "before_restore": frozen, "restored": settings(5)}
        link.settings.side_effect = phases.__getitem__
        link.set_fields.side_effect = [configured, settings(5)]
        link.capture.return_value = fixture_waveform()
        link.measurement.return_value = "?"
        result = {}
        workflow(link, "full", result)
        self.assertTrue(result["single_stop_observed"])
        self.assertTrue(result["restoration_verified"])
        self.assertFalse(result["ch1_vpp_crosscheck"])
        self.assertEqual([a.args for a in link.key.call_args_list],
                         [("start_running", RUN_STOP), ("arm_single", SINGLE)])
        actions = [a.args[0] for a in link.method_calls if a[0] in ("capture", "set_fields")]
        self.assertLess(actions.index("single_waveform"), actions.index("restore_scale"))

    def test_unsettled_command_prevents_followup_writes(self):
        link = Mock(settled=True)
        link.settings.side_effect = [settings(0), settings(3)]
        link.set_fields.return_value = settings(0)

        def malformed(*_):
            link.settled = False
            raise RuntimeError("unsettled capture")

        link.capture.side_effect = malformed
        with self.assertRaisesRegex(RuntimeError, "unsettled"):
            workflow(link, "running", {})
        self.assertEqual(link.set_fields.call_count, 1)
        self.assertEqual(link.key.call_count, 1)

    def test_frozen_csv_preserves_count_and_analyzes_only_nominal_samples(self):
        lines = ["#timebase=200000000(ps)", ",#voltbase=1000000(uV)", "#size=4164"]
        lines += [f"{(i + 1) * 1e-6:.10f},{5000 if i % 1000 >= 500 else 0}" for i in range(4000)]
        lines += [f"{(i + 1) * 1e-6:.10f},-9000" for i in range(4000, 4164)]
        raw = "\n".join(lines).encode()
        result = read_frozen_csv(raw)
        self.assertEqual(result["exported_samples"], 4164)
        self.assertEqual(result["samples"], 4000)
        self.assertEqual(result["vpp_v"], 5)
        self.assertAlmostEqual(result["frequency_hz"], 1000)
        with self.assertRaises(RuntimeError):
            read_frozen_csv(raw.replace(b"4164", b"4301", 1))

    def test_frozen_listing_refuses_unrelated_and_duplicate_entries(self):
        line = b"-rw-r--r-- 1 root root 89000 Nov 24 12:00 /mnt/udisk/WaveData163.csv"
        self.assertEqual(parse_listing(line), {"WaveData163.csv": 89000})
        for listing in (line + b"\n" + line, line.replace(b"WaveData163", b"other")):
            with self.subTest(listing=listing), self.assertRaises(RuntimeError):
                parse_listing(listing)
