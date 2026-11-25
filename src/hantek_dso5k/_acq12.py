"""Supervised DSO5102P external event profiles for the retained 208-byte layout."""

from ._acq11 import RUN_STOP, _running, describe
from ._framing import frame
from ._settings import compare_settings

# Qualified ACQ-11 BNC scales 7 (0.5 V/div) and 9 (2 V/div) also let the
# stopped CSV fallback distinguish the two export slots from their headers.
ANALOG = {
    0: b"\x01", 1: b"\x07", 2: b"\x00", 5: b"\x00", 8: (-65).to_bytes(2, "little", signed=True),
    10: b"\x01", 11: b"\x09", 12: b"\x00", 15: b"\x00", 18: (-35).to_bytes(2, "little", signed=True),
    160: b"\x12", 188: b"\x00",
}
DIAGNOSTIC = {**ANALOG, 21: b"\x00", 22: b"\x00", 23: b"\x00", 24: b"\x00", 59: b"\x00"}
EXTERNAL = {**ANALOG, 21: b"\x00", 22: b"\x03", 23: b"\x01", 24: b"\x00", 59: b"\x00"}


def patch_profile(raw, edits):
    if len(raw) != 208:
        raise ValueError("The ACQ-12 profile requires the retained 208-byte layout")
    result = bytearray(raw)
    for offset, value in edits.items():
        if offset not in DIAGNOSTIC or len(value) != len(DIAGNOSTIC[offset]):
            raise ValueError(f"Unsupported ACQ-12 settings span at {offset}")
        result[offset:offset + len(value)] = value
    return bytes(result)


def apply_profile(session, name, current, fields):
    payload = patch_profile(current, fields)
    if payload == current:
        return current
    ack = session.exchange(name, frame(0x11, payload), 0x91, duration=1.8)
    observed = session.settings(name + "_readback")
    if ack != b"\x00" or compare_settings(payload, observed)["configuration_or_unclassified_changes"]:
        raise RuntimeError(f"{name} did not produce the expected complete settings readback")
    return observed


def restore(session, baseline, outcome, fields):
    with session.recovery():
        current = session.settings("before_acq12_restore")
        previous_fields = {offset: baseline[offset:offset + len(value)] for offset, value in fields.items()}
        current = apply_profile(session, "acq12_restore_settings", current, previous_fields)
        if _running(current) != _running(baseline):
            session.key("acq12_restore_run_state", RUN_STOP)
        final = session.settings("acq12_restored")
    differences = compare_settings(baseline, final)
    outcome.update(restored=describe(final), restoration_comparison=differences,
                   restoration_verified=(not differences["configuration_or_unclassified_changes"]
                                         and _running(baseline) == _running(final)))
    if not outcome["restoration_verified"]:
        raise RuntimeError("ACQ-12 scope restoration did not match its baseline")
