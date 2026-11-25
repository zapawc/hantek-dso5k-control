"""Strict offline decoding of one complete native S82 observation window."""

from collections.abc import Iterable
from dataclasses import dataclass

from ._framing import STATUS, ProtocolError, read_frames


_CHANNELS = ("CH1", "CH2", "MATH")
_ORDER = {0: (0,), 1: (1,), 4: (0, 1, 2)}
MAX_CAPTURE_BYTES = 256 * 1024
MAX_CHANNEL_SAMPLES = 40_000
MAX_CHUNK_SAMPLES = 10_000


@dataclass(frozen=True)
class ChannelWaveform:
    channel: str
    samples: tuple[int, ...] | None


@dataclass(frozen=True)
class NativeWaveform:
    selector: int
    channels: tuple[ChannelWaveform, ...]
    terminal_seen: bool
    status_offsets: tuple[int, ...]


def decode_native_waveform(transfers: Iterable[bytes], *, selector: int) -> NativeWaveform:
    """Decode selectors 0, 1 or 4. Offsets include all bytes in the input.

    Pass the whole captured window, including traffic after the terminal.
    Only exact S92 status may follow it. For selectors 0 and 1 an explicit
    unavailable record ends the primary reply without a terminal.
    """
    if type(selector) is not int or selector not in _ORDER:
        raise ValueError("Expected native waveform selector 0, 1 or 4")
    wire = bytearray()
    for transfer in transfers:
        if not isinstance(transfer, bytes):
            raise TypeError("Pass an iterable of byte transfers")
        if len(wire) + len(transfer) > MAX_CAPTURE_BYTES:
            raise ProtocolError("native waveform capture exceeds the offline byte bound")
        wire.extend(transfer)

    order = _ORDER[selector]
    channels = []
    status_offsets = []
    count = None
    samples = bytearray()
    terminal = False
    for offset, opcode, payload in read_frames([wire]):
        if opcode == 0x92:
            if payload != STATUS[4:-1]:
                raise ProtocolError(f"unrecognized S92 frame at {offset}")
            status_offsets.append(offset)
            continue
        if opcode != 0x82 or not payload:
            raise ProtocolError(f"unexpected opcode or empty S82 at {offset}")
        if terminal:
            raise ProtocolError(f"extra primary data after terminal at {offset}")

        subtype, data = payload[0], payload[1:]
        if subtype == 2:
            if data != bytes([selector]) or count is not None or len(channels) != len(order):
                raise ProtocolError(f"invalid terminal selector or incomplete channel coverage at {offset}")
            if selector != 4 and channels[0].samples is None:
                raise ProtocolError(f"unexpected terminal for single-channel unavailable at {offset}")
            terminal = True
            continue
        if len(channels) == len(order):
            raise ProtocolError(f"extra channel data at {offset}")
        channel = order[len(channels)]
        if subtype == 0 and count is None and len(data) == 3:
            count = int.from_bytes(data, "little")
            if not 1 <= count <= MAX_CHANNEL_SAMPLES:
                raise ProtocolError(f"invalid declared sample count at {offset}")
        elif subtype == 1 and count is not None:
            if not 2 <= len(data) <= MAX_CHUNK_SAMPLES + 1 or data[0] != selector:
                raise ProtocolError(f"invalid data selector or chunk length at {offset}")
            samples.extend(data[1:])
            if len(samples) > count:
                raise ProtocolError(f"sample count exceeded at {offset}")
            if len(samples) == count:
                signed = tuple(value - 256 if value >= 128 else value for value in samples)
                channels.append(ChannelWaveform(_CHANNELS[channel], signed))
                count = None
                samples.clear()
        elif subtype == 3 and count is None and data == bytes([channel]):
            channels.append(ChannelWaveform(_CHANNELS[channel], None))
        else:
            raise ProtocolError(f"invalid S82 subtype, channel order or block length at {offset}")

    if not terminal and not (selector != 4 and channels and channels[0].samples is None):
        raise ProtocolError("missing terminal or incomplete channel coverage")
    return NativeWaveform(selector, tuple(channels), terminal, tuple(status_offsets))
