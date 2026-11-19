"""Incremental S20 packet decoding for an already captured or permitted byte stream."""
import hashlib
from ._framing import checksum_valid, STATUS

CHUNKS = (10208,) * 75 + (2400,)
PIXEL_BYTES = sum(CHUNKS)
REQUEST = bytes.fromhex("53 02 00 20 75")
READ_BYTES = 16384


class GraphicsStream:
    def __init__(self, output, event):
        self.output = output
        self.event = event
        self.pending = bytearray()
        self.offset = 0
        self.first_poll = None
        self.frame_errors = 0
        self.extra_frames = 0
        self.status_frames = 0
        self.chunks = 0
        self.data_bytes = 0
        self.checksum = 0
        self.terminal = None
        self.digest = hashlib.sha256()
        self.aligned = True
        self.correlatable = True
        self.outside_window_transfers = 0

    def feed(self, data, poll, completed, utc, in_window=True):
        if not in_window:
            self.outside_window_transfers += 1
            self.event("frame_window_fault", poll=poll, completed_monotonic=completed, capture_utc=utc,
                       length=len(data), pending_length=len(self.pending))
            if self.terminal is None:
                self.correlatable = False
        if not self.aligned:
            return
        if not self.pending:
            self.first_poll = poll
        self.pending.extend(data)
        pos = 0
        while pos < len(self.pending):
            marker = self.pending[pos]
            if marker not in (0x53, 0x43):
                self.alignment_fault("unexpected marker", self.offset + pos, poll)
                return
            if len(self.pending) - pos < 3:
                break
            length = self.pending[pos + 1] | self.pending[pos + 2] << 8
            if length < 2:
                self.alignment_fault("invalid encoded length", self.offset + pos, poll)
                return
            end = pos + length + 3
            if end > len(self.pending):
                break
            packet = bytes(self.pending[pos:end])
            self.classify(packet, self.offset + pos, poll, completed, utc)
            self.first_poll = poll
            pos = end
        if pos:
            del self.pending[:pos]
            self.offset += pos

    def alignment_fault(self, reason, offset, poll):
        self.event("frame_alignment_fault", offset_in_response=offset, last_poll=poll,
                   first_poll=self.first_poll, reason=reason)
        self.frame_errors += 1
        self.aligned = self.correlatable = False
        self.pending.clear()

    def classify(self, packet, offset, poll, completed, utc):
        good = checksum_valid(packet)
        opcode = packet[3]
        body = packet[4:-1]
        classification = "unexpected_frame"
        if not good:
            self.frame_errors += 1
            classification = "bad_checksum"
        elif packet == STATUS:
            self.status_frames += 1
            classification = "transfer_status_origin_unresolved"
        elif packet[0] == 0x53 and opcode == 0xA0 and self.terminal is None and self.correlatable:
            if self.chunks < len(CHUNKS) and body[:1] == b"\x01" and len(body) == CHUNKS[self.chunks] + 1:
                data = body[1:]
                self.output.write(data)
                self.output.flush()
                self.digest.update(data)
                self.checksum = (self.checksum + sum(data)) & 255
                self.data_bytes += len(data)
                self.chunks += 1
                classification = "graphics_data"
            elif self.chunks == len(CHUNKS) and body[:1] == b"\x02" and len(body) == 2:
                self.terminal = body[1]
                classification = "graphics_terminal"
            else:
                self.frame_errors += 1
                classification = "invalid_graphics_order_or_length"
        if classification not in ("graphics_data", "graphics_terminal"):
            self.extra_frames += 1
            if self.terminal is None:
                self.correlatable = False
        self.event("frame", offset_in_response=offset, wire_bytes=len(packet), marker=chr(packet[0]),
                   opcode=f"{opcode:02X}", payload_bytes=len(body), subtype=body[0] if body else None,
                   received_checksum=packet[-1], checksum_valid=good, classification=classification,
                   first_poll=self.first_poll, last_poll=poll, completed_monotonic=completed, capture_utc=utc,
                   small_frame_hex=packet.hex(" ").upper() if len(packet) <= 32 else None)

    def finish(self):
        if self.pending:
            self.event("partial_frame", offset_in_response=self.offset, bytes=len(self.pending),
                       first_poll=self.first_poll)
            self.frame_errors += 1
        complete_data = self.chunks == len(CHUNKS) and self.data_bytes == PIXEL_BYTES
        framed = complete_data and self.terminal is not None
        return {
            "graphics_frames": self.chunks, "graphics_bytes": self.data_bytes,
            "complete_graphics_bytes": complete_data, "graphics_framing": "PASS" if framed else "FAIL",
            "frame_errors": self.frame_errors, "unexpected_frames": self.extra_frames,
            "transfer_status_frames": self.status_frames,
            "outside_window_transfers": self.outside_window_transfers,
            "computed_aggregate": self.checksum, "reported_aggregate": self.terminal,
            "aggregate": ("PASS" if self.checksum == self.terminal else "FAIL") if framed else "UNVERIFIED",
            "graphics_sha256": self.digest.hexdigest() if self.data_bytes else None,
        }
