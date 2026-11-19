"""Synthetic compatibility checks for the neutral public settings vocabulary."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hantek_dso5k._settings import compare_settings, fields_layout, settings
from hantek_dso5k.cli import main


class SettingsV2Tests(unittest.TestCase):
    def test_layout_reconstructs_every_byte(self):
        layout = list(fields_layout())
        self.assertEqual(len(layout), 119)
        self.assertEqual(layout[-1][2], 208)
        self.assertEqual([n for _, start, end in layout for n in range(start, end)], list(range(208)))
        for data in (bytes(range(208)), bytes([0xff])*208, bytes(208)):
            result = settings(data)
            self.assertEqual(result["schema_version"], "2.0")
            self.assertEqual(list(result["fields"]), [name for name, _, _ in layout])
            self.assertEqual(bytes.fromhex("".join(item["hex"] for item in result["fields"].values())), data)
            for name, start, end in layout:
                self.assertEqual(name, f"field_{start:03d}")
                self.assertEqual(result["fields"][name]["raw_integer"],
                                 int.from_bytes(data[start:end], "little", signed=end-start>1))

    def test_independently_observed_fields_and_signed_decoding(self):
        data = bytearray(208)
        for offset, value in ((0, 1), (2, 1), (10, 1), (12, 2), (20, 3), (22, 1), (179, 0)):
            data[offset] = value
        data[27:35] = bytes.fromhex("01020304050607ff")
        fields = settings(bytes(data))["fields"]
        self.assertEqual(fields["field_000"]["raw_integer"], 1)
        self.assertEqual(fields["field_010"]["raw_integer"], 1)
        self.assertEqual(fields["field_002"]["candidate_label"], "AC")
        self.assertEqual(fields["field_012"]["candidate_label"], "GND")
        self.assertEqual(fields["field_020"]["candidate_label"], "Triggered")
        self.assertEqual(fields["field_022"]["candidate_label"], "CH2")
        self.assertEqual(fields["field_027"]["raw_integer"], -70080650589044223)
        self.assertEqual(fields["field_179"]["raw_integer"], 0)

    def test_volatile_disposition_and_public_identifiers(self):
        original = bytes(208)
        final = bytearray(original)
        for offset in (20, 34, 179):
            final[offset] = 1
        diff = compare_settings(original, final)
        self.assertEqual([x["field"] for x in diff["volatile_changes"]], ["field_020", "field_027"])
        self.assertEqual(diff["volatile_changes"][1]["range_start_inclusive_end_exclusive"], [27, 35])
        self.assertEqual([x["field"] for x in diff["configuration_or_unclassified_changes"]], ["field_179"])

    def test_cli_and_checked_schema_agree(self):
        with TemporaryDirectory() as temp:
            fixture = Path(temp)/"fixture.bin"
            fixture.write_bytes(bytes(208))
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(main(["offline", "settings", str(fixture)]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["schema_version"], "2.0")
        schema_path = Path(__file__).resolve().parents[1]/"schemas/settings-v2.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(set(result), set(schema["required"]))
        self.assertEqual(set(result["fields"]), set(schema["properties"]["fields"]["propertyNames"]["enum"]))
