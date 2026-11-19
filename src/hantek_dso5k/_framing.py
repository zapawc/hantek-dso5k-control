STATUS = bytes.fromhex('53 04 00 92 01 00 EA')


def checksum_valid(packet):
    return len(packet) >= 5 and (sum(packet[:-1]) & 255) == packet[-1]


class ProtocolError(ValueError):
    pass


def frame(opcode, payload=b""):
    header = b"S" + (len(payload) + 2).to_bytes(2, "little") + bytes([opcode])
    body = header + payload
    return body + bytes([sum(body) & 255])


def read_frames(transfers):
    """Strict stream reconstruction for offline evidence. Offsets are into unchanged source bytes."""
    wire = b"".join(transfers)
    offset = 0
    while offset < len(wire):
        if wire[offset] != 0x53:
            raise ProtocolError(f"unexpected marker at {offset}")
        if len(wire) - offset < 3:
            raise ProtocolError(f"partial header at {offset}")
        size = int.from_bytes(wire[offset + 1:offset + 3], "little")
        if size < 2:
            raise ProtocolError(f"invalid length at {offset}")
        end = offset + 3 + size
        if end > len(wire):
            raise ProtocolError(f"partial frame at {offset}")
        block = wire[offset:end]
        if not checksum_valid(block):
            raise ProtocolError(f"bad frame checksum at {offset}")
        yield offset, block[3], block[4:-1]
        offset = end
