from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import unittest

from hantek_dso5k.acquisition import build_native_result
from test_native_waveform import PAIR, S92, chunk, start, terminal, unavailable


SCHEMA = Path(__file__).resolve().parents[1] / "schemas/acquisition-result-v1.schema.json"
SCALING = {
    "verified": True,
    "provenance": "synthetic independent fixture",
    "volts_per_division": 0.5,
    "probe_attenuation": 10,
    "vertical_offset_counts": 2,
    "seconds_per_division": 0.001,
    "viewport": "menu shown",
    "display_divisions": 16,
    "sample_count": 3,
    "sample_interval_s": 0.016 / 3,
    "time_origin_s": -0.008,
    "time_reference": "synthetic display center",
}
TIMING = {
    "clock_context": "fixture host boot A, perf_counter seconds and UTC wall clock",
    "request_write": {"utc": "2026-01-01T00:00:00Z", "monotonic_s": 50},
    "receive_first": {"utc": "2026-01-01T00:00:00.010+00:00", "monotonic_s": 50.01},
    "receive_end": {"utc": "2026-01-01T00:00:00.020+00:00", "monotonic_s": 50.02},
}


def result(parts=PAIR, *, selector=4, **metadata):
    return build_native_result(parts, selector=selector, run_id="fixture-1", **metadata)


class AcquisitionResultTests(unittest.TestCase):
    def test_conversion_inputs_are_explicit_and_raw_is_preserved(self):
        config = {"channel_scaling": {
            "CH1": SCALING,
            "CH2": dict(SCALING, sample_count=2, sample_interval_s=0.008, probe_attenuation=1),
        }}
        observation = result(configuration_snapshot=config)
        one, two, math_channel = observation["channels"]
        self.assertEqual(one["raw"], {"unit": "display_code", "values": [-127, 0, 127]})
        self.assertEqual(one["sample_count"], 3)
        self.assertEqual(one["conversion_status"], "converted")
        self.assertIsNone(one["conversion_reason"])
        values = one["engineering"]
        self.assertEqual((values["voltage_unit"], values["time_unit"]), ("V", "s"))
        for value, expected in zip(values["values"], [-25.8, -0.4, 25.0]):
            self.assertAlmostEqual(value, expected)
        self.assertEqual(values["sample_interval_s"], 0.016 / 3)
        self.assertEqual(values["time_origin_s"], -0.008)
        self.assertEqual(values["time_reference"], "synthetic display center")
        self.assertEqual(two["engineering"]["values"], [0.3, -2.44])
        self.assertEqual(math_channel["availability"], "unavailable")
        self.assertIsNone(math_channel["raw"])
        self.assertIsNone(math_channel["sample_count"])

    def test_explicit_span_and_unknown_math_conversion(self):
        scaling = dict(SCALING, display_span_s=0.016)
        del scaling["display_divisions"]
        parts = (*PAIR[:-2], start(1), chunk(4, b"\xff"), terminal(4))
        observation = result(parts, configuration_snapshot={"channel_scaling": {"CH1": scaling, "MATH": scaling}})
        self.assertEqual(observation["channels"][0]["conversion_status"], "converted")
        math_channel = observation["channels"][2]
        self.assertEqual(math_channel["raw"]["values"], [-1])
        self.assertEqual(math_channel["conversion_reason"], "math_scaling_unsupported")

    def test_no_conversion_with_missing_unverified_invalid_or_inconsistent_metadata(self):
        cases = [
            ("time_origin_s", None, "missing"), ("display_divisions", None, "missing"),
            ("verified", False, "unverified"), ("provenance", " ", "unverified"),
            ("probe_attenuation", 0, "invalid"), ("volts_per_division", True, "invalid"),
            ("vertical_offset_counts", float("nan"), "invalid"),
            ("volts_per_division", 1e308, "invalid"),
            ("time_origin_s", float("inf"), "invalid"),
            ("sample_count", 2, "invalid"), ("time_reference", "", "invalid"),
            ("sample_interval_s", 0.01, "inconsistent"), ("display_span_s", 0.014, "inconsistent"),
        ]
        for field, value, reason in cases:
            with self.subTest(field=field, reason=reason):
                config = {"channel_scaling": {"CH1": dict(SCALING, **{field: value})}}
                observation = result(configuration_snapshot=config)
                channel = observation["channels"][0]
                self.assertEqual(channel["raw"]["values"], [-127, 0, 127])
                self.assertEqual(channel["conversion_status"], "raw_only")
                self.assertEqual(channel["conversion_reason"], f"scaling_metadata_{reason}")
                self.assertIsNone(channel["engineering"])
                json.dumps(observation, allow_nan=False)

    def test_wire_integrity_leaves_measurement_freshness_and_coherence_unverified(self):
        observation = result()
        self.assertEqual(observation["transport_integrity"], "verified_wire")
        self.assertEqual(observation["channel_association"], "s82_slot_order")
        self.assertEqual(observation["measurement_validity"], "unverified")
        self.assertEqual(observation["data_freshness"], "unknown")
        self.assertEqual(observation["acquisition_coherence"], "unproven")
        self.assertIsNone(observation["device_generation_id"])
        self.assertEqual(observation["host_run_id"], "fixture-1")
        self.assertEqual(observation["channels"][0]["conversion_reason"], "scaling_metadata_missing")

    def test_incomplete_wire_results_do_not_publish_partial_samples(self):
        observation = result(PAIR[:-1])
        self.assertEqual(observation["transport_integrity"], "failed")
        self.assertEqual(observation["channels"], [])
        self.assertIsNone(observation["terminal_seen"])
        self.assertEqual(observation["channel_association"], "unverified")
        self.assertEqual(observation["errors"][0]["code"], "invalid_s82_observation")

    def test_metadata_and_schema_serialization(self):
        wire = b"".join((S92, *PAIR))
        instrument = {"model": "DSO5102P", "firmware": "3.40.0 (151009.0)", "provenance": "fixture label"}
        artifact = {"role": "raw_rx", "locator": "fixture.bin", "bytes": len(wire), "sha256": sha256(wire).hexdigest()}
        snapshot = {"source": "synthetic", "channel_scaling": {"CH1": SCALING}}
        observation = result([wire], instrument=instrument, timing=TIMING, artifacts=[artifact],
                             configuration_snapshot=snapshot)
        self.assertEqual(observation["schema_version"], "1.0")
        self.assertEqual(observation["source_method"], "native_s02_s82")
        self.assertEqual(observation["timing"], TIMING)
        self.assertEqual(observation["instrument"], instrument)
        self.assertEqual(observation["artifacts"], [artifact])
        self.assertEqual(observation["configuration_snapshot"], snapshot)
        self.assertEqual(observation["s92_status_offsets"], [0])
        roundtrip = json.loads(json.dumps(observation, allow_nan=False))
        self.assertEqual(roundtrip, observation)
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(set(schema["required"]), set(roundtrip))
        self.assertEqual(set(schema["$defs"]["channel"]["required"]), set(roundtrip["channels"][0]))
        self.assertEqual(schema["properties"]["schema_version"]["const"], observation["schema_version"])

        # A detached development validator checks the full schema when available.
        # Hosted regression tests remain dependency-free.
        if importlib.util.find_spec("jsonschema") is not None:
            from jsonschema import Draft202012Validator

            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
            for record in (roundtrip, result(), result(PAIR[:-1]), result([unavailable(1)], selector=1),
                           result([start(1), chunk(0, b"\x01"), terminal(0)], selector=0)):
                validator.validate(record)
            wrong_order = dict(roundtrip, channels=list(reversed(roundtrip["channels"])))
            impossible_terminal = dict(roundtrip, terminal_seen=False)
            false_coherence = dict(roundtrip, acquisition_coherence="verified")
            for invalid in (wrong_order, impossible_terminal, false_coherence):
                self.assertFalse(validator.is_valid(invalid))

    def test_metadata_rejects_invalid_timestamps_and_artifact_claims(self):
        bad_utc = deepcopy(TIMING)
        bad_utc["receive_first"]["utc"] = "2026-01-01T00:00:00-05:00"
        backwards = deepcopy(TIMING)
        backwards["receive_end"]["monotonic_s"] = 49
        invalid_stamp = deepcopy(TIMING)
        invalid_stamp["receive_end"] = {"utc": None, "monotonic_s": None}
        for timing in (bad_utc, backwards, invalid_stamp, dict(TIMING, clock_context=None)):
            with self.subTest(timing=timing), self.assertRaises(ValueError):
                result(timing=timing)
        with self.assertRaises(ValueError):
            result(instrument={"model": "DSO5102P", "firmware": None, "provenance": None})
        with self.assertRaises(ValueError):
            result(artifacts=[{"role": "raw_rx", "locator": "fixture.bin", "bytes": 0, "sha256": "?"}])
