"""Bounded sigrok FX2LP reference capture for the exposed three fixture signals."""

from contextlib import contextmanager
from hashlib import sha256
from statistics import median
from subprocess import Popen, TimeoutExpired
from time import sleep

from fixture import decode_pulses

SAMPLE_HZ = 1_000_000
RECORD_SAMPLES = 4 * SAMPLE_HZ


def analyze_reference(data: bytes, event_id: int) -> dict:
    """Decode active-high FX2LP D0/D1/D2 bits from a 1 MS/s packed record."""
    if not data or len(data) > RECORD_SAMPLES or data[0] & 7 or data[-1] & 7:
        raise ValueError("Independent timing capture lacks complete idle boundaries")
    edges = [[], [], []]
    previous = 0
    for index, sample in enumerate(data):
        levels = sample & 7
        if levels != previous:
            for channel, channel_edges in enumerate(edges):
                if (levels ^ previous) & (1 << channel):
                    channel_edges.append(index)
            previous = levels
    if len(edges[2]) != 2:
        raise ValueError("Expected exactly one reference trigger pulse")
    trigger_start, trigger_end = edges[2]
    if not 400 <= trigger_end - trigger_start <= 600:
        raise ValueError("Reference trigger width differs from the fixture envelope")

    channels = {}
    for channel in (1, 2):
        if len(edges[channel - 1]) % 2:
            raise ValueError("Reference waveform ended during a pulse")
        stream = iter(edges[channel - 1])
        pulses = [(start - trigger_start, end - trigger_start) for start, end in zip(stream, stream)]
        number, decoded = decode_pulses(pulses, tolerance_us=25)
        if number != channel or decoded != event_id or not 900 <= pulses[0][0] <= 1100:
            raise ValueError("Independent timing reference does not match the requested channels and event")
        channels[f"CH{channel}"] = {"event_id": decoded, "pulses_from_trigger_us": pulses}
    return {"backend": "sigrok fx2lafw", "sample_count": len(data), "sample_rate_hz": SAMPLE_HZ,
            "trigger_sample_index": trigger_start, "trigger_width_us": trigger_end - trigger_start,
            "channels": channels, "sha256": sha256(data).hexdigest(),
            "timebase_calibrated": False}


def compare_reference(reference, scope, event_id):
    comparisons = {}
    for channel, independent in reference["channels"].items():
        logic = independent["pulses_from_trigger_us"]
        measured = next(result["pulses_us"] for result in scope[channel]["decoded_candidates"]
                        if result["event_id"] == event_id and result["channel"] == int(channel[-1]))
        comparisons[channel] = {
            "trigger_to_first_rise_us": logic[0][0],
            "scope_minus_logic_pulse_widths_us": [
                (scope_end - scope_start) - (logic_end - logic_start)
                for (scope_start, scope_end), (logic_start, logic_end) in zip(measured, logic)
            ],
            "scope_minus_logic_relative_rises_us": [
                (scope_rise - measured[0][0]) - (logic_rise - logic[0][0])
                for (scope_rise, _), (logic_rise, _) in zip(measured, logic)
            ],
        }
    first, second = (channel["pulses_from_trigger_us"] for channel in reference["channels"].values())
    differences = [right[0] - left[0] for left, right in zip(first, second)]
    return {"channels": comparisons, "reference_ch2_minus_ch1_rising_us": differences,
            "reference_median_skew_us": median(differences),
            "combined_width_sample_bound_us": {
                channel: scope[channel]["interval_s"] * 1e6 + 1 for channel in comparisons},
            "independent_clock_accuracy": "uncharacterized"}


@contextmanager
def record_reference(executable, folder, event_id):
    """Start a finite capture before requesting the event and retain raw bytes."""
    with (folder / f"logic_{event_id}.bin").open("xb") as output, (
            folder / f"logic_{event_id}.stderr.txt").open("xb") as errors:
        process = Popen([str(executable), "-d", "fx2lafw", "-c", f"samplerate={SAMPLE_HZ}",
                         "-C", "D0,D1,D2", "--samples", str(RECORD_SAMPLES), "-O", "binary"],
                        stdout=output, stderr=errors)
        try:
            # Allow USB startup before the event, with two seconds of capture headroom.
            sleep(1.5)
            if process.poll() is not None:
                raise RuntimeError("sigrok exited before the event")
            result = {}
            yield result
            try:
                if process.wait(timeout=8) != 0:
                    result["error"] = "sigrok did not complete the independent reference capture"
            except TimeoutExpired:
                result["error"] = "sigrok exceeded the independent capture window"
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
