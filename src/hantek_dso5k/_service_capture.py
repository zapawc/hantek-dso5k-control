"""Serialized capture procedures for the service's approved instrument profiles."""

from hashlib import sha256

from ._acq11 import RUN_STOP, SINGLE, _running, analyze, describe, restore_settings
from ._native_finalization import create_record
from ._retrieve import file_record
from .acquisition import build_native_result
from .native_waveform import decode_native_waveform


class Unsupported(ValueError):
    """A capture precondition failed without leaving unsettled state."""


FIXTURE_EDITS = {1: 7, 5: 1, 11: 9, 15: 0}


def instrument():
    return {"model": "DSO5102P", "firmware": "3.40.0 (151009.0)",
            "provenance": "operator-declared retained Workbench scope and verified WinUSB interface"}


def snapshot(session, raw, job_id):
    wire = session.native_wire("snapshot")
    decoded = decode_native_waveform([wire], selector=4)
    cfg = describe(raw)
    cfg["s81_sha256"] = sha256(raw).hexdigest()
    cfg["channel_scaling"] = {}
    derived = {}
    divisions = 16
    for channel in decoded.channels:
        scale = cfg["channels"].get(channel.channel)
        if (not channel.samples or not scale or scale["bnc_v_per_div"] is None
                or raw[160] != 15 or raw[162] != 0 or raw[179] != 0 or raw[185] != 0
                or raw[188] != 0 or not cfg["menu_visible"]):
            continue
        dt = cfg["seconds_per_division"] * divisions / len(channel.samples)
        cfg["channel_scaling"][channel.channel] = {
            "volts_per_division": scale["bnc_v_per_div"], "probe_attenuation": scale["software_probe"],
            "vertical_offset_counts": scale["vertical_position_counts"], "display_divisions": divisions,
            "seconds_per_division": cfg["seconds_per_division"], "sample_count": len(channel.samples),
            "sample_interval_s": dt, "time_origin_s": 0, "time_reference": "relative first display sample",
            "verified": True, "provenance": "ACQ-11 fixture S81 mapping with configured software probe factor",
        }
        _, metrics = analyze(channel.samples, bncvdiv=scale["bnc_v_per_div"], probe=scale["software_probe"],
                             offset=scale["vertical_position_counts"], interval=dt)
        derived[channel.channel] = {
            "source": "native_waveform", "uses_configured_probe_factor": True,
            **{key: metrics[key] for key in ("min_v", "max_v", "vpp_v", "mean_v",
                                             "frequency_hz", "period_s", "duty_cycle", "clipped")},
        }
    source = session.evidence / f"{session.count:03d}-snapshot.rx"
    timing = session.last_timing if isinstance(session.last_timing, dict) else None
    result = build_native_result(
        [wire], selector=4, run_id=job_id, instrument=instrument(), configuration_snapshot=cfg,
        timing=timing,
        artifacts=[{"role": "raw_s82", "locator": source.name, **file_record(source)}],
    )
    create_record(session.evidence / "waveform.json", result)
    return {"method": "native_s02", "status_offsets": list(decoded.status_offsets),
            "availability": {slot.channel: slot.samples is not None for slot in decoded.channels},
            "derived_measurements": derived,
            "same_shot": "UNPROVEN"}


def verify_profile(raw, path):
    profile = {0: 1, 10: 1, 2: 0, 12: 0, 21: 0, 22: 0, 23: 0, 24: 0, 59: 0,
               160: 15, 162: 0, 171: 0, 179: 0, 185: 0, 188: 0}
    if any(raw[field] != expected for field, expected in profile.items()):
        raise Unsupported("Settings differ from the verified dual-channel 4K probe-compensation profile")
    if path == "frozen" and raw[206:208] != b"\x30\x01":
        raise Unsupported("Frozen capture requires the visible CSV menu and mounted media")


def triggered(session, baseline, job_id, request, result, restoration_required):
    path = request["path"]
    verify_profile(baseline, path)
    if not _running(baseline) and not request["allow_overwrite"]:
        raise Unsupported("SINGLE can overwrite the existing stopped acquisition. Supply allow_overwrite=true")
    restoration_required(True)
    try:
        state = session.set_fields("fixture_scale", baseline, FIXTURE_EDITS)
        if not _running(state):
            session.key("start_running", RUN_STOP)
            if not _running(session.settings("running")):
                raise RuntimeError("Run/Stop did not start acquisition")
        session.key("arm_single", SINGLE)
        for attempt in range(1, 5):
            frozen = session.settings(f"single_{attempt}")
            if not _running(frozen):
                break
        else:
            raise Unsupported("SINGLE did not reach Stop within the readback budget")
        result["stop_observed"] = True
        if path != "frozen":
            native = snapshot(session, frozen, job_id)
            result["native"] = native
            if all(native["availability"].get(ch) for ch in ("CH1", "CH2")):
                result["method"] = "native_s02"
                return
            if path == "native":
                raise Unsupported("Stopped native returned unavailable analog slots")
        if frozen[206:208] != b"\x30\x01":
            raise Unsupported("Stopped native data unavailable and the frozen CSV menu is not selected")
        result["frozen"] = session.frozen(frozen)
        result["method"] = "frozen_csv"
    finally:
        if session.settled:
            result["restored_raw"] = restore_settings(session, baseline, result)
            restoration_required(False)


def set_run_state(session, baseline, request, result, restoration_required):
    target = request["running"]
    previous = _running(baseline)
    if target and not previous and not request["allow_overwrite"]:
        raise Unsupported("Starting Run can overwrite the stopped record. Supply allow_overwrite=true")
    if target != previous:
        restoration_required(True)
        session.key("requested_run_state", RUN_STOP)
        raw = session.settings("run_state_readback")
        if _running(raw) != target:
            raise RuntimeError("Run-state readback did not match the explicit request")
        restoration_required(False)
    else:
        raw = baseline
    result["restored_raw"] = raw
    result["settings"] = describe(raw)


def s22_probe(session, baseline, request, result, restoration_required):
    configure = request.get("configure_fixture", False)
    if configure:
        verify_profile(baseline, "auto")
        if not _running(baseline):
            raise Unsupported("The configured S22 comparison requires an explicitly running scope")
        restoration_required(True)
    try:
        if configure:
            configured = session.set_fields("probe_fixture_scale", baseline, FIXTURE_EDITS)
            if not _running(configured):
                raise Unsupported("Scope did not stay running after the fixture edits")
            result["configured"] = describe(configured)
        result["reads"] = [
            {"source": source, "mode": mode, "item": item,
             "display": session.measurement(item, source, mode=mode)}
            for mode in (0, 1) for source in (0, 1) for item in (1, 4)
        ]
    finally:
        if configure and session.settled:
            result["restored_raw"] = restore_settings(session, baseline, result)
            restoration_required(False)
