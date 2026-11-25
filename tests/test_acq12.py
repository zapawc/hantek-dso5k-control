import unittest
from unittest.mock import Mock

from hantek_dso5k._acq12 import DIAGNOSTIC, EXTERNAL, apply_profile, patch_profile


class ExternalProfileTests(unittest.TestCase):
    def test_patch_spans_and_keeps_unrelated_bytes(self):
        baseline = bytes(range(208))
        edited = patch_profile(baseline, EXTERNAL)
        changed = {index for index, pair in enumerate(zip(baseline, edited)) if pair[0] != pair[1]}
        allowed = {i for offset, value in EXTERNAL.items() for i in range(offset, offset + len(value))}
        self.assertLessEqual(changed, allowed)
        self.assertEqual(edited[22], 2)
        self.assertEqual(edited[25:27], b"\x0c\x00")
        self.assertEqual(edited[27:35], baseline[27:35])
        self.assertEqual(edited[160], baseline[160])
        self.assertEqual(edited[188], baseline[188])
        self.assertEqual(edited[206:], baseline[206:])

    def test_rejects_unknown_and_partial_fields(self):
        for edits in ({20: b"\x01"}, {8: b"\x01"}, {189: b"\x00"}):
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                patch_profile(bytes(208), edits)

    def test_readback_requires_ack_and_complete_configuration_match(self):
        baseline = bytes(208)
        desired = patch_profile(baseline, DIAGNOSTIC)
        mock = Mock()
        mock.exchange.return_value = b"\x00"
        mock.settings.return_value = desired[:20] + b"\x01" + desired[21:]
        self.assertEqual(apply_profile(mock, "diagnostic", baseline, DIAGNOSTIC), mock.settings.return_value)
        mock.settings.return_value = desired[:23] + b"\x01" + desired[24:]
        with self.assertRaisesRegex(RuntimeError, "field_023"):
            apply_profile(mock, "diagnostic", baseline, DIAGNOSTIC)
        mock.settings.return_value = desired
        mock.exchange.return_value = b"\x02"
        with self.assertRaisesRegex(RuntimeError, "acknowledgment 02"):
            apply_profile(mock, "diagnostic", baseline, DIAGNOSTIC)
