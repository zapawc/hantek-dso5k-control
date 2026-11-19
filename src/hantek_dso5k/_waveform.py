from hashlib import sha256
import csv
import math

from ._framing import ProtocolError, read_frames

def ch1_waveform(transfers):
    count = None
    chunks = []
    end = False
    unavailable = False
    for offset, opcode, payload in read_frames(transfers):
        if end or unavailable or opcode != 0x82 or not payload:
            raise ProtocolError(f"unexpected waveform frame at {offset}")
        subtype, data = payload[0], payload[1:]
        if subtype == 0 and count is None and len(data) == 3:
            count = int.from_bytes(data, "little")
            if not 1 <= count <= 40000:
                raise ProtocolError("sample count outside 1 through 40000")
        elif subtype == 3 and count is None and data == b"\0":
            unavailable = True
        elif subtype == 1 and count is not None and 2 <= len(data) <= 10001 and data[0] == 0:
            chunks.append(data[1:])
        elif subtype == 2 and count is not None and data == b"\0":
            end = True
        else:
            raise ProtocolError(f"invalid waveform subtype, source or length at {offset}")
    if unavailable:
        return "unavailable", ()
    if not end or sum(map(len, chunks)) != count:
        raise ProtocolError("missing terminal or inconsistent sample count")
    return "complete", tuple(value - 256 if value >= 128 else value for value in b"".join(chunks))

def convert_samples(samples, dt, volts_per_division, probe, position):
    if not all(math.isfinite(value) and value > 0 for value in (dt, volts_per_division, probe)):
        raise ValueError("Timing, scale and probe factor must be finite and positive")
    return [(i, i * dt, sample, (sample - position) / 25 * volts_per_division * probe)
            for i, sample in enumerate(samples)]


def analyze(source, out, dt, vdiv, probe, position):
    with source.open("rb") as handle:
        wire = handle.read(131073)
    if len(wire) > 131072:
        raise ValueError("Offline waveform input exceeds 128 KiB bound")
    for _, op, data in read_frames([wire]):
        if op == 0x82 and data[:1] == b"\0" and int.from_bytes(data[1:], "little") > 40000:
            raise ProtocolError("Display record exceeds 40000 sample bound")
    status, samples = ch1_waveform([wire])
    if status != "complete":
        raise ProtocolError("Waveform unavailable")
    rows = convert_samples(samples, dt, vdiv, probe, position)
    with out.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("sample_index", "record_relative_seconds", "signed_screen_count", "candidate_volts"))
        writer.writerows(rows)
    return {"status": status, "sample_count": len(samples), "sha256": sha256(wire).hexdigest(),
            "scale_evidence": "caller-supplied and unverified", "trigger_reference": None,
            "shared_acquisition": None}
