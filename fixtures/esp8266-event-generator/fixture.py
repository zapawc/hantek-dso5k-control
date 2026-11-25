"""Serial client and pulse-width reference decoder for the ACQ-12 fixture."""

import argparse
import json
import time
from collections.abc import Sequence
from contextlib import contextmanager

MAX_EVENT_ID = 65535
EVENT_BITS = 16
SLOT_US = 1000
CHANNEL_SIGNATURE_US = {1: 600, 2: 800}
EVENT_DURATION_US = (2 + EVENT_BITS) * SLOT_US


def expected_pulses(event_id: int, channel: int) -> list[tuple[int, int]]:
    """Return (rising edge, falling edge) pairs in µs from the EXT rising edge."""
    if not 1 <= event_id <= MAX_EVENT_ID:
        raise ValueError("event ID must be 1–65535")
    widths = [CHANNEL_SIGNATURE_US[channel]]
    widths.extend(400 if event_id & (1 << bit) else 200 for bit in reversed(range(EVENT_BITS)))
    return [
        (slot * SLOT_US, slot * SLOT_US + width)
        for slot, width in enumerate(widths, start=1)
    ]


def decode_pulses(pulses: Sequence[tuple[float, float]], *, tolerance_us: float = 60) -> tuple[int, int]:
    """Decode (channel, event ID) from measured edge pairs on one analog channel."""
    if not 0 <= tolerance_us < 100:
        raise ValueError("tolerance must be below 100 µs")
    if len(pulses) != EVENT_BITS + 1:
        raise ValueError("incorrect pulse count")
    channel = 0
    event_id = 0
    for slot, (rise, fall) in enumerate(pulses):
        if slot and not abs(rise - pulses[slot - 1][0] - SLOT_US) <= tolerance_us:
            raise ValueError(f"incorrect spacing at slot {slot}")
        width = fall - rise
        if slot == 0:
            for candidate, signature in CHANNEL_SIGNATURE_US.items():
                if abs(width - signature) <= tolerance_us:
                    channel = candidate
                    break
            if not channel:
                raise ValueError("unknown channel signature")
        elif abs(width - 200) <= tolerance_us:
            event_id <<= 1
        elif abs(width - 400) <= tolerance_us:
            event_id = (event_id << 1) | 1
        else:
            raise ValueError(f"invalid event bit width at slot {slot}")
    if not event_id:
        raise ValueError("reserved event ID 0")
    return channel, event_id


def inspect_capture(capture: dict, expected: int) -> dict:
    """Compare decoded physical pulses with the requested event and wire assignments."""
    channels = {}
    for channel in ("CH1", "CH2"):
        if not capture["availability"].get(channel):
            continue
        report = capture["channels"][channel]
        interval = report["interval_s"]
        edges = sorted([(rise, True) for rise in report["rising_edges_s"]]
                       + [(fall, False) for fall in report["falling_edges_s"]])
        pulses = [(a * 1e6, b * 1e6) for (a, a_high), (b, b_high) in zip(edges, edges[1:])
                  if a_high and not b_high]
        candidates = []
        tolerance = max(25, 2 * interval * 1e6)
        if tolerance < 100:
            for start in range(len(pulses) - EVENT_BITS):
                packet = pulses[start:start + EVENT_BITS + 1]
                try:
                    number, decoded = decode_pulses(packet, tolerance_us=tolerance)
                except ValueError:
                    continue
                candidates.append({"channel": number, "event_id": decoded, "pulses_us": packet})
        channels[channel] = {
            **report,
            "decoded_candidates": candidates,
            "decode_tolerance_us": tolerance,
            "accepted": any(row["event_id"] == expected and row["channel"] == int(channel[-1])
                            for row in candidates) and not report["clipped"],
        }
    return channels


@contextmanager
def open_fixture(port: str):
    """Open the fixture at 115200 baud with reset control lines deasserted."""
    import serial

    device = serial.Serial(baudrate=115200, timeout=0.2, write_timeout=1)
    device.port = port
    device.dtr = False
    device.rts = False
    device.open()
    try:
        yield device
    finally:
        device.close()


def command(device, request: str) -> dict:
    """Send one request. Surface ambiguous timeouts without retransmission."""
    device.write(request.encode("ascii") + b"\n")
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        line = device.read_until(size=256)
        if not line.endswith(b"\n"):
            continue
        try:
            response = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(response, dict):
            if response.get("ok") is False:
                return response
            if response.get("ok") is True and response.get("command") == request.split()[0]:
                return response
    raise TimeoutError(f"no matching reply for {request}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("operation", choices=["info", "status", "idle", "stop", "event", "pattern"])
    parser.add_argument("event_id", type=int, nargs="?")
    args = parser.parse_args()
    if args.operation in ("event", "pattern"):
        if args.event_id is None or not 1 <= args.event_id <= MAX_EVENT_ID:
            parser.error("event and pattern require an ID from 1 to 65535")
        request = f"{args.operation.upper()} {args.event_id}"
    else:
        if args.event_id is not None:
            parser.error("only event and pattern take an ID")
        request = args.operation.upper()

    with open_fixture(args.port) as device:
        response = command(device, request)
    print(json.dumps(response))
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
