from datetime import datetime, timezone
import math

from ._framing import STATUS, checksum_valid
from ._graphics import GraphicsStream, READ_BYTES

class Reply:
    def __init__(self, opcode, output, event):
        self.opcode = opcode
        self.output = output
        self.event = event
        self.screen = GraphicsStream(output, self.graphics_event) if opcode == 0xA0 else None
        self.data = bytearray()
        self.base = 0
        self.start_poll = None
        self.primary = False
        self.bad = False
        self.status = 0
        self.content = {}

    def graphics_event(self, kind, **fields):
        label = fields.get("classification")
        if kind == "frame":
            if label == "graphics_terminal":
                self.primary = True
            elif label == "transfer_status_origin_unresolved":
                self.status_event(fields)
            elif label != "graphics_data":
                self.bad = True
        elif kind in ("frame_alignment_fault", "frame_window_fault", "partial_frame"):
            self.bad = True
        self.event(kind, **fields)

    def status_event(self, fields):
        self.status += 1
        classified = self.primary and self.status == 1 and not self.bad
        fields["session_classification"] = ("timer_consistent_post_reply_origin_unresolved" if classified
                                            else "uncorrelated_or_duplicate_status")
        if not classified:
            self.bad = True

    def feed(self, data, poll, completed, utc, in_window):
        if self.screen:
            self.screen.feed(data, poll, completed, utc, in_window)
            return
        if not in_window:
            self.bad = True
        if not self.data:
            self.start_poll = poll
        self.data.extend(data)
        while len(self.data) >= 3:
            size = int.from_bytes(self.data[1:3], "little") + 3
            if self.data[0] != 0x53 or not 5 <= size <= 213:
                self.bad = True
                self.event("frame_alignment_fault", offset_in_response=self.base, poll=poll,
                           marker=self.data[0], encoded_wire_length=size)
                break
            if len(self.data) < size:
                break
            block = bytes(self.data[:size])
            del self.data[:size]
            fields = {"offset_in_response": self.base, "wire_bytes": size, "first_poll": self.start_poll,
                      "last_poll": poll, "capture_utc": utc, "completed_monotonic": completed,
                      "wire_hex": block.hex(" ").upper(), "opcode": f"{block[3]:02X}",
                      "checksum_valid": checksum_valid(block)}
            self.base += size
            self.start_poll = poll
            if not fields["checksum_valid"]:
                self.bad = True
                fields["classification"] = "bad_checksum"
            elif block == STATUS:
                fields["classification"] = "transfer_status_origin_unresolved"
                self.status_event(fields)
            elif not self.bad and not self.primary and block[3] == self.opcode:
                payload = block[4:-1]
                if self.opcode == 0x81 and len(payload) == 208:
                    self.output.write(payload)
                    self.primary = True
                else:
                    self.bad = True
                fields["classification"] = "expected_reply" if self.primary else "wrong_expected_payload_length"
            else:
                self.bad = True
                fields["classification"] = "unexpected_or_duplicate"
            self.event("frame", **fields)

    def finish(self):
        if self.screen:
            self.content = self.screen.finish()
            self.primary = self.content["graphics_framing"] == "PASS"
        elif self.data:
            self.bad = True
            self.event("partial_frame", offset_in_response=self.base, bytes=len(self.data), first_poll=self.start_poll)
        return {**self.content, "primary_complete": self.primary, "status_frames": self.status,
                "malformed_or_ambiguous": self.bad}


def observe(io, clock, elapsed_from, duration, rx, log, *, limit, reply=None):
    deadline = elapsed_from + duration
    poll = received = 0
    error = None
    log("window_started", start=elapsed_from, deadline=deadline)
    while (started := clock()) < deadline:
        poll += 1
        timeout = max(1, min(250, math.ceil((deadline - started) * 1000)))
        try:
            data = io.read(READ_BYTES, timeout_ms=timeout)
        except TimeoutError:
            log("poll_timeout", poll=poll, started=started, completed=clock(), timeout_ms=timeout)
            continue
        except Exception as exc:
            error = str(exc)
            log("read_error", poll=poll, started=started, completed=clock(), error=error)
            break
        completed = clock()
        utc = datetime.now(timezone.utc).isoformat()
        offset = rx.tell()
        rx.write(data)
        rx.flush()
        received += len(data)
        log("receive", poll=poll, offset=offset, length=len(data), started=started,
            completed=completed, capture_utc=utc, in_window=completed <= deadline)
        if reply is None or not data or len(data) > READ_BYTES:
            error = "unexpected transfer during silence gate" if reply is None else "RX size or window byte budget"
            break
        reply.feed(data, poll, completed, utc, completed <= deadline)
        if received > limit:
            error = "RX window byte budget"
            log("byte_budget_exceeded", limit=limit, total=received)
            break
    result = {"duration": clock() - elapsed_from, "received": received, "polls": poll,
              "complete": clock() >= deadline and error is None, "error": error}
    log("window_completed", **result)
    return result
