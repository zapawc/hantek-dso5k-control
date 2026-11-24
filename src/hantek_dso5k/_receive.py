"""Bounded framed USB receive windows with explicit status classification."""

from datetime import datetime, timezone
import math

from ._framing import STATUS, checksum_valid
from ._graphics import READ_BYTES

PACKET_BYTES = 512


class ReceiveError(RuntimeError):
    pass


class ReceiveStream:
    """Classify exact status notifications at frame boundaries, without resynchronizing."""

    def __init__(self, log, report, *, primary_opcode, max_frame_bytes):
        self.log = log
        self.report = report
        self.primary_opcode = primary_opcode
        self.max_frame_bytes = max_frame_bytes
        self.pending = bytearray()
        self.file_wire = bytearray()
        self.offset = 0
        self.first = None

    def feed(self, data, poll, completed, timestamp):
        current = {"poll": poll, "monotonic": completed, "utc": timestamp}
        if not self.pending:
            self.first = current
        self.pending.extend(data)
        while self.pending:
            if self.pending[0] != 0x53:
                self.fail("unexpected frame marker")
            if len(self.pending) < 3:
                return
            size = int.from_bytes(self.pending[1:3], "little") + 3
            if not 5 <= size <= max(len(STATUS), self.max_frame_bytes):
                self.fail("invalid frame length")
            if len(self.pending) < 4:
                return
            opcode = self.pending[3]
            if opcode == 0x92:
                if size != len(STATUS):
                    self.fail("unrecognized S92 length")
            elif opcode != self.primary_opcode:
                self.fail(f"unexpected opcode 0x{opcode:02X}")
            elif not 6 <= size <= self.max_frame_bytes:
                self.fail("invalid primary frame length")
            if len(self.pending) < size:
                return
            block = bytes(self.pending[:size])
            if not checksum_valid(block):
                self.fail("invalid frame checksum")
            fields = {"raw_offset": self.offset, "wire_bytes": size, "first_transfer": self.first,
                      "last_transfer": current, "opcode": f"0x{opcode:02X}"}
            if opcode == 0x92:
                if block != STATUS:
                    self.fail("unrecognized S92 payload")
                self.report["recognized_status_frames"] += 1
                self.log("recognized_status", classification="transfer_status_origin_unresolved",
                         payload_hex=block[4:-1].hex(" ").upper(),
                         wire_hex=block.hex(" ").upper(), **fields)
            else:
                self.report["primary_frames_seen"] += 1
                self.log("primary_frame", file_stream_offset=len(self.file_wire), subtype=block[4], **fields)
                self.file_wire.extend(block)
            del self.pending[:size]
            self.offset += size
            self.first = current

    def fail(self, reason):
        self.log("frame_rejected", raw_offset=self.offset, first_transfer=self.first, reason=reason)
        raise ReceiveError(f"{reason} at RX offset {self.offset}")

    def finish(self):
        if self.pending:
            self.fail("incomplete frame at observation deadline")


def observe(link, clock, started, rx, log, report, *, duration, rx_limit, primary_opcode=None, max_frame_bytes=0,
            require_silence=False):
    """Record API transfers and validate frame boundaries inside the hard RX budget."""
    deadline = started + duration
    received = poll = 0
    stream = ReceiveStream(log, report, primary_opcode=primary_opcode, max_frame_bytes=max_frame_bytes)
    log("window_started", start=started, deadline=deadline)
    while (poll_start := clock()) < deadline:
        capacity = min(READ_BYTES, ((rx_limit - received) // PACKET_BYTES) * PACKET_BYTES)
        if not capacity:
            raise ReceiveError("RX budget leaves no room for another aligned read")
        timeout = min(250, max(1, math.ceil((deadline - poll_start) * 1000)))
        poll += 1
        try:
            data = link.read(capacity, timeout_ms=timeout)
        except TimeoutError:
            log("poll_timeout", poll=poll, started=poll_start, completed=clock(), timeout_ms=timeout)
            continue
        except Exception as exc:
            log("read_error", poll=poll, started=poll_start, completed=clock(), error=str(exc))
            raise
        completed = clock()
        timestamp = datetime.now(timezone.utc).isoformat()
        rx.write(data)
        rx.flush()
        log("receive", poll=poll, started=poll_start, completed=completed, offset=received,
            length=len(data), requested=capacity, in_window=completed <= deadline, capture_utc=timestamp)
        received += len(data)
        report["raw_bytes"] = received
        if not data or len(data) > capacity or completed > deadline:
            raise ReceiveError("unexpected transfer size or data outside the observation window")
        if require_silence:
            raise ReceiveError("unexpected traffic during the silent CH2 preflight")
        stream.feed(data, poll, completed, timestamp)
    stream.finish()
    log("window_completed", received_bytes=received, polls=poll)
    return bytes(stream.file_wire)
