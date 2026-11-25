"""Plot preserved native scope records against the fixture's nominal pattern."""

import argparse
import csv
import json
from pathlib import Path

from fixture import expected_pulses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("record")
    parser.add_argument("event_id", type=int)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    import matplotlib.pyplot as plt

    manifest = json.loads((args.evidence / "native_manifest.json").read_bytes())
    if args.record == "diagnostic_pattern":
        decoded = manifest.get("pattern_decoded", {})
    else:
        trial = next(row for row in manifest["trials"] if row["requested_event_id"] == args.event_id)
        decoded = trial.get("decoded", {})
    fig, axes = plt.subplots(2, 1, figsize=(10, 5.4), sharex=True, layout="constrained")
    for channel, axis in enumerate(axes, 1):
        name = f"CH{channel}"
        path = args.evidence / f"{args.record}_{name}.csv"
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        times_ms = [float(row["relative_time_s"]) * 1000 for row in rows]
        values = [float(row["voltage_v"]) for row in rows]
        axis.plot(times_ms, values, linewidth=0.9, label=f"{name} scope samples")
        candidates = decoded.get(name, {}).get("decoded_candidates", [])
        candidate = next((row for row in candidates
                          if row["event_id"] == args.event_id and row["channel"] == channel), None)
        if candidate:
            # Align the nominal reference to the observed channel signature edge.
            shift = candidate["pulses_us"][0][0] - 1000
            x, y = [], []
            for start, end in expected_pulses(args.event_id, channel):
                x.extend(((start + shift) / 1000, (start + shift) / 1000,
                          (end + shift) / 1000, (end + shift) / 1000))
                y.extend((0, 3.3, 3.3, 0))
            axis.plot(x, y, "--", linewidth=0.8, label="Nominal fixture pattern, aligned to signature")
        axis.set_ylabel(f"{name} (V)")
        axis.grid(alpha=0.2)
        axis.legend(loc="upper right", fontsize="small")

    axes[-1].set_xlabel("Display-derived time from record start (ms)")
    fig.suptitle(f"ACQ-12 event {args.event_id} · {args.record.replace('_', ' ')}")
    fig.savefig(args.output, dpi=160)


if __name__ == "__main__":
    main()
