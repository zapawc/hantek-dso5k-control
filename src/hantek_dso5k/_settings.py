from hashlib import sha256

SETTINGS_SCHEMA_VERSION = "2.0"

# Only observed wire widths are retained. Keys identify decimal byte offsets.
_FIELD_WIDTHS = (
    1, 1, 1, 1, 1, 1, 1, 1, 2, 1, 1, 1, 1, 1, 1, 1, 1, 2, 1, 1,
    1, 1, 1, 2, 8, 8, 8, 8, 1, 1, 1, 1, 2, 1, 1, 8, 1, 1, 1, 2,
    2, 8, 1, 1, 1, 1, 1, 1, 1, 2, 1, 1, 1, 1, 1, 1, 2, 2, 8, 1,
    1, 1, 1, 1, 1, 1, 2, 1, 1, 8, 1, 1, 1, 2, 2, 8, 1, 8, 1, 1,
    1, 8, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
)
VOLATILE = {"field_020": (20, 21), "field_027": (27, 35)}


def fields_layout():
    offset = 0
    for count in _FIELD_WIDTHS:
        end = offset + count
        yield f"field_{offset:03d}", offset, end
        offset = end


def compare_settings(before, after):
    layout = list(fields_layout())
    if len(before) != 208 or len(after) != 208 or layout[-1][-1] != 208:
        raise ValueError("Expected the 208-byte settings layout")
    offsets = {name: (start, end) for name, start, end in layout}
    if any(offsets.get(name) != span for name, span in VOLATILE.items()):
        raise ValueError("Volatile ranges do not match validated offsets")
    result = {"volatile_changes": [], "configuration_or_unclassified_changes": []}
    for name, start, end in layout:
        a, b = before[start:end], after[start:end]
        if a != b:
            key = "volatile_changes" if name in VOLATILE else "configuration_or_unclassified_changes"
            result[key].append({"field": name, "range_start_inclusive_end_exclusive": [start, end],
                                "initial_hex": a.hex(), "final_hex": b.hex()})
    return result


def settings(payload):
    if len(payload) != 208:
        raise ValueError("Expected 208 settings bytes")
    fields = {}
    for name, start, end in fields_layout():
        data = payload[start:end]
        fields[name] = {"offset": start, "hex": data.hex(),
                        "raw_integer": int.from_bytes(data, "little", signed=end-start > 1)}
    labels = {"field_020": {0: "Stop", 1: "Ready", 2: "Auto", 3: "Triggered"},
              "field_022": {0: "CH1", 1: "CH2", 2: "EXT", 3: "EXT/5", 4: "AC line"},
              "field_021": {0: "Edge", 1: "Video", 2: "Pulse", 3: "Slope", 4: "Overtime", 5: "Alternate"},
              "field_023": {0: "Auto", 1: "Normal"}, "field_059": {0: "Rising", 1: "Falling"},
              **{key: {0: "DC", 1: "AC", 2: "GND"} for key in ("field_002", "field_012")}}
    for name, values in labels.items():
        fields[name]["candidate_label"] = values.get(fields[name]["raw_integer"])
    return {"schema_version": SETTINGS_SCHEMA_VERSION, "schema": "2015 S81 / 208 bytes", "sha256": sha256(payload).hexdigest(),
            "label_evidence": "archived community mapping with partial local corroboration",
            "armed_state": None, "completed_event": None, "fields": fields}
