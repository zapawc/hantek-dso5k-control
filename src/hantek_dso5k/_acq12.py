"""Supervised DSO5102P external event profiles for the retained 208-byte layout."""

from ._acq11 import RUN_STOP, _running, describe
from ._framing import frame
from ._qualification import SCREEN, WINDOW
from ._replies import Reply, observe
from ._rgb565 import render
from ._screens import REGIONS, region
from ._settings import compare_settings

# Qualified ACQ-11 BNC scales 7 (0.5 V/div) and 9 (2 V/div) also let the
# stopped CSV fallback distinguish the two export slots from their headers.
ANALOG = {
    0: b"\x01", 1: b"\x07", 2: b"\x00", 5: b"\x00", 8: (-65).to_bytes(2, "little", signed=True),
    10: b"\x01", 11: b"\x09", 12: b"\x00", 15: b"\x00", 18: (-35).to_bytes(2, "little", signed=True),
}
DIAGNOSTIC = {**ANALOG, 21: b"\x00", 22: b"\x00", 23: b"\x00", 24: b"\x00", 59: b"\x00"}
# Retained firmware normalized a Normal-mode S11 write to mode 0 while
# stopped. Arm behavior in this mode must be demonstrated by the display.
EXTERNAL = {**DIAGNOSTIC, 22: b"\x02", 25: b"\x0c\x00"}


def set_timebase(session, current, target):
    """Use the SEC/DIV keys because S11 only updates the timebase readback."""
    if current[188] != 0 or current[162] != 0 or current[160] not in range(15, 21) or target not in range(15, 21):
        raise ValueError("ACQ-12 timebase keys require ordinary retained 4K mode and a reviewed timebase")
    while current[160] != target:
        direction = 1 if current[160] < target else -1
        expected = current[160] + direction
        key = 41 if direction == 1 else 40
        ack = session.exchange("acq12_timebase_key", frame(0x13, bytes((key, 1))), 0x93, duration=1.4)
        observed = session.settings("acq12_timebase_readback")
        if len(ack) != 1 or observed[160] != expected:
            raise RuntimeError("SEC/DIV key did not advance the reviewed timebase by one step")
        current = observed
    return current


def patch_profile(raw, edits):
    if len(raw) != 208:
        raise ValueError("The ACQ-12 profile requires the retained 208-byte layout")
    result = bytearray(raw)
    for offset, value in edits.items():
        if offset not in EXTERNAL or len(value) != len(EXTERNAL[offset]):
            raise ValueError(f"Unsupported ACQ-12 settings span at {offset}")
        result[offset:offset + len(value)] = value
    return bytes(result)


def apply_profile(session, name, current, fields):
    payload = patch_profile(current, fields)
    if payload == current:
        return current
    ack = session.exchange(name, frame(0x11, payload), 0x91, duration=1.8)
    observed = session.settings(name + "_readback")
    differences = compare_settings(payload, observed)["configuration_or_unclassified_changes"]
    if ack != b"\x00" or differences:
        fields = ", ".join(change["field"] for change in differences)
        raise RuntimeError(f"{name} acknowledgment {ack.hex()}, readback differences: {fields or 'none'}")
    return observed


def restore(session, baseline, outcome, fields):
    with session.recovery():
        current = session.settings("before_acq12_restore")
        if _running(current) and not _running(baseline):
            session.key("acq12_disarm", RUN_STOP)
            current = session.settings("acq12_disarmed")
            if _running(current):
                raise RuntimeError("Scope did not stop before settings restoration")
        previous_fields = {offset: baseline[offset:offset + len(value)] for offset, value in fields.items()}
        current = apply_profile(session, "acq12_restore_settings", current, previous_fields)
        current = set_timebase(session, current, baseline[160])
        if _running(current) != _running(baseline):
            session.key("acq12_restore_run_state", RUN_STOP)
        final = session.settings("acq12_restored")
    differences = compare_settings(baseline, final)
    outcome.update(restored=describe(final), restoration_comparison=differences,
                   restoration_verified=(not differences["configuration_or_unclassified_changes"]
                                         and _running(baseline) == _running(final)))
    if not outcome["restoration_verified"]:
        raise RuntimeError("ACQ-12 scope restoration did not match its baseline")


def screen_status(session, name):
    """Preserve one RGB565 display reply and extract the reviewed trigger-status region."""
    if not session.settled:
        raise RuntimeError("Previous command has unresolved history")
    if session.job_check is not None:
        session.job_check()
    folder = session.evidence
    session.count += 1
    stem = f"{session.count:03d}-{name}"
    (folder / (stem + ".tx")).write_bytes(SCREEN)
    session.settled = False
    with (folder / (stem + ".rx")).open("xb") as raw, (folder / (stem + ".rgb565")).open("xb") as pixels:
        reply = Reply(0xA0, pixels, session.log)
        session.log("write_attempt", command=name, bytes=SCREEN.hex(" "))
        written = session.link.write(SCREEN)
        started = session.clock()
        session.log("write_completed", command=name, bytes=written, completed=started)
        if written != len(SCREEN):
            raise RuntimeError("Short screen request")
        transport = observe(session.link, session.clock, started, WINDOW, raw, session.log,
                            limit=768470, reply=reply)
        result = reply.finish()
    if (not transport["complete"] or not result["primary_complete"] or result["frame_errors"]
            or result["malformed_or_ambiguous"]):
        raise RuntimeError("Incomplete or ambiguous scope display exchange")
    session.settled = True
    source = folder / (stem + ".rgb565")
    render(source, folder / (stem + ".png"))
    result["status_pixels"] = region(source.read_bytes(), REGIONS["trigger_status"][1])
    return result
