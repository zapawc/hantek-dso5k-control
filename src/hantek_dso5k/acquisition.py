"""Version 1 host acquisition results for offline native waveform observations."""

from datetime import datetime
import json
import math
import re

from ._framing import ProtocolError
from ._waveform import convert_samples
from .native_waveform import decode_native_waveform


_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|\+00:00)")


def _json_object(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError("Expected a JSON object")
    return json.loads(json.dumps(value, allow_nan=False))


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _positive(value):
    return _finite(value) and value > 0


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_instrument(instrument):
    fields = {"model", "firmware", "provenance"}
    if set(instrument) != fields or not all(value is None or _text(value) for value in instrument.values()):
        raise ValueError("Instrument must identify model, firmware and provenance, or use null for unknown values")
    if (instrument["model"] or instrument["firmware"]) and not instrument["provenance"]:
        raise ValueError("Identified instrument attributes require provenance")


def _validate_timing(timing):
    events = ("request_write", "receive_first", "receive_end")
    if set(timing) != {"clock_context", *events}:
        raise ValueError("Timing requires clock_context and an explicit value or null for each event")
    context = timing["clock_context"]
    if context is not None and not _text(context):
        raise ValueError("Expected a descriptive clock context or null")
    if any(timing[event] is not None for event in events) and context is None:
        raise ValueError("Host timestamps require an identified clock context")

    prior = None
    for event in events:
        stamp = timing[event]
        if stamp is None:
            continue
        if not isinstance(stamp, dict) or set(stamp) != {"utc", "monotonic_s"}:
            raise ValueError(f"Expected UTC and monotonic seconds for {event}")
        utc, monotonic = stamp["utc"], stamp["monotonic_s"]
        if utc is not None:
            if not isinstance(utc, str) or not _UTC.fullmatch(utc):
                raise ValueError(f"Expected an ISO 8601 UTC timestamp for {event}")
            datetime.fromisoformat(utc.replace("Z", "+00:00"))
        if monotonic is not None:
            if not _finite(monotonic) or monotonic < 0 or (prior is not None and monotonic < prior):
                raise ValueError(f"Invalid or out-of-order monotonic seconds for {event}")
            prior = monotonic
        if utc is None and monotonic is None:
            raise ValueError(f"Use null for an unrecorded {event}")


def _artifact_descriptors(artifacts):
    result = []
    roles = set()
    for descriptor in artifacts:
        item = _json_object(descriptor)
        if item is None or set(item) != {"role", "locator", "bytes", "sha256"}:
            raise ValueError("Each artifact requires role, locator, bytes and sha256")
        role, locator, size, digest = (item[key] for key in ("role", "locator", "bytes", "sha256"))
        if not _text(role) or not _text(locator) or (role, locator) in roles:
            raise ValueError("Expected a unique artifact role and locator pair")
        if type(size) is not int or size < 0:
            raise ValueError("Expected nonnegative artifact bytes")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Expected a lowercase SHA-256 digest")
        roles.add((role, locator))
        result.append(item)
    return result


def _engineering(samples, scaling):
    if not isinstance(scaling, dict):
        return None, "scaling_metadata_missing"
    vertical = ("volts_per_division", "probe_attenuation", "vertical_offset_counts")
    horizontal = ("seconds_per_division", "sample_count", "sample_interval_s",
                  "time_origin_s", "time_reference")
    if any(scaling.get(key) is None for key in (*vertical, *horizontal)):
        return None, "scaling_metadata_missing"
    if scaling.get("verified") is not True or not _text(scaling.get("provenance")):
        return None, "scaling_metadata_unverified"

    vdiv, probe, offset = (scaling[key] for key in vertical)
    timebase, count, dt, origin, reference = (scaling[key] for key in horizontal)
    divisions = scaling.get("display_divisions")
    span = scaling.get("display_span_s")
    if divisions is None and span is None:
        return None, "scaling_metadata_missing"
    if (not all(_positive(value) for value in (vdiv, probe, timebase, dt))
            or not _finite(offset) or not _finite(origin) or not _text(reference)
            or type(count) is not int or count != len(samples)
            or (divisions is not None and not _positive(divisions))
            or (span is not None and not _positive(span))):
        return None, "scaling_metadata_invalid"

    if divisions is not None:
        viewport = timebase * divisions
        if not _positive(viewport) or (span is not None and not math.isclose(span, viewport, rel_tol=1e-9)):
            return None, "scaling_metadata_inconsistent"
        span = viewport
    if not math.isclose(dt, span / count, rel_tol=1e-9):
        return None, "scaling_metadata_inconsistent"

    points = convert_samples(samples, dt, vdiv, probe, offset)
    values = [point[3] for point in points]
    last_time = origin + points[-1][1]
    if not _finite(last_time) or not all(_finite(value) for value in values):
        return None, "scaling_metadata_invalid"
    return {
        "voltage_unit": "V", "values": values, "time_unit": "s",
        "sample_interval_s": dt, "time_origin_s": origin, "time_reference": reference,
    }, None


def _channel_result(channel, config):
    samples = channel.samples
    if samples is None:
        engineering, reason = None, "instrument_reported_no_data"
        conversion_status = "unavailable"
    elif channel.channel == "MATH":
        engineering, reason = None, "math_scaling_unsupported"
        conversion_status = "raw_only"
    else:
        scaling = config.get("channel_scaling")
        inputs = scaling.get(channel.channel) if isinstance(scaling, dict) else None
        engineering, reason = _engineering(samples, inputs)
        conversion_status = "converted" if engineering is not None else "raw_only"
    return {
        "channel": channel.channel,
        "availability": "unavailable" if samples is None else "available",
        "sample_count": len(samples) if samples is not None else None,
        "raw": {"unit": "display_code", "values": list(samples)} if samples is not None else None,
        "engineering": engineering,
        "conversion_status": conversion_status,
        "conversion_reason": reason,
    }


def build_native_result(transfers, *, selector, run_id, instrument=None, timing=None,
                        configuration_snapshot=None, artifacts=()):
    """Produce a JSON-compatible host observation from captured transfers.

    Supply independently verified scaling as configuration_snapshot["channel_scaling"].
    Malformed wire data yields a failed result without partially attributed samples.
    Invalid caller identifiers, timestamps or artifact descriptors raise an exception.
    """
    if not _text(run_id):
        raise ValueError("Expected a nonempty host run ID")
    if instrument is None:
        instrument = {"model": None, "firmware": None, "provenance": None}
    instrument = _json_object(instrument)
    _validate_instrument(instrument)
    if timing is None:
        timing = {"clock_context": None, "request_write": None, "receive_first": None, "receive_end": None}
    timing = _json_object(timing)
    _validate_timing(timing)
    if configuration_snapshot is not None and not isinstance(configuration_snapshot, dict):
        raise TypeError("Expected a JSON object for the configuration snapshot")
    # Preserve invalid numeric input as null in JSON while classifying it against the original.
    config = json.loads(json.dumps(configuration_snapshot), parse_constant=lambda _: None)
    descriptors = _artifact_descriptors(artifacts)
    try:
        decoded = decode_native_waveform(transfers, selector=selector)
    except ProtocolError as exc:
        decoded = None
        channels = []
        errors = [{"code": "invalid_s82_observation", "detail": str(exc)}]
    else:
        channels = [_channel_result(channel, configuration_snapshot or {}) for channel in decoded.channels]
        errors = []
    return {
        "schema_version": "1.0",
        "host_run_id": run_id,
        "source_method": "native_s02_s82",
        "selector": selector,
        "instrument": instrument,
        "timing": timing,
        "configuration_snapshot": config,
        "channels": channels,
        "transport_integrity": "verified_wire" if decoded else "failed",
        "terminal_seen": decoded.terminal_seen if decoded else None,
        "s92_status_offsets": list(decoded.status_offsets) if decoded else [],
        "measurement_validity": "unverified",
        "data_freshness": "unknown",
        "channel_association": "s82_slot_order" if decoded else "unverified",
        "acquisition_coherence": "unproven",
        "device_generation_id": None,
        "artifacts": descriptors,
        "errors": errors,
    }
