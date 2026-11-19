from datetime import datetime, timezone
import json
from pathlib import Path

from ._framing import frame
from ._replies import Reply, observe
from ._graphics import READ_BYTES
from ._settings import compare_settings
from ._winusb import ClaimedBulkIO

WINDOW = 12.0
GUARD = 0.550
SETTINGS = frame(0x01)
SCREEN = frame(0x20)


class Stop(RuntimeError):
    pass


STEPS = (
    ("baseline", SETTINGS, 0x81, 220),
    ("screen_first", SCREEN, 0xA0, 768470),
    ("screen_second", SCREEN, 0xA0, 768470),
    ("final", SETTINGS, 0x81, 220),
)

class ClaimedLinkIO(ClaimedBulkIO):
    """Bind a previously validated and externally claimed transport link."""

    def __init__(self, link):
        super().__init__(link)
        self.next_step = 0

    def read(self, size, *, timeout_ms):
        if size != READ_BYTES or not 1 <= timeout_ms <= 250:
            raise Stop("read outside the reviewed bounds")
        return super().read(size, timeout_ms=timeout_ms)

    def write(self, request):
        if self.next_step >= len(STEPS) or request != STEPS[self.next_step][1]:
            raise Stop("write outside the reviewed sequence")
        slot = self.next_step
        self.next_step = len(STEPS)
        written = super().write(request)
        if written == len(request):
            self.next_step = slot + 1
        return written


def run(io, clock, directory, gate, *, allow_timer_consistent_status=False):
    """Write evidence through a caller-provided adapter. Caller owns setup/cleanup."""
    if not all((gate.exclusive_ownership, gate.lease_held, gate.target_verified,
                gate.prior_history_settled, gate.no_unresolved_s12)):
        raise Stop("ownership, target or prior-history gate failed")
    directory = Path(directory)
    result = {"steps": [], "write_attempts": 0, "retry_count": 0,
              "status_continuation_policy": allow_timer_consistent_status}
    with (directory / "journal.jsonl").open("x", encoding="utf-8") as journal:
        def log(event, **fields):
            journal.write(json.dumps({"event": event, "stage": stage, "monotonic": clock(),
                                      "utc": datetime.now(timezone.utc).isoformat(), **fields}) + "\n")
            journal.flush()

        stage = "preflight"
        log("state", state="PREFLIGHT")
        try:
            with (directory / "preflight_rx.bin").open("xb") as rx:
                if not observe(io, clock, clock(), WINDOW, rx, log, limit=0)["complete"]:
                    raise Stop("receive-only preflight blocked")
            for index, (stage, request, opcode, limit) in enumerate(STEPS):
                if index:
                    with (directory / f"{stage}_guard_rx.bin").open("xb") as rx:
                        if not observe(io, clock, clock(), GUARD, rx, log, limit=0)["complete"]:
                            raise Stop("inter-command quiet guard blocked")
                log("state", state=("BASELINE" if stage in ("baseline", "final") else "TRANSFER"))
                item = {"stage": stage, "request_hex": request.hex(" ").upper()}
                result["steps"].append(item)
                with (directory / f"{stage}_rx.bin").open("xb") as rx, \
                     (directory / f"{stage}_payload.bin").open("xb") as payload:
                    with (directory / f"{stage}_tx.bin").open("xb") as tx:
                        tx.write(request)
                    result["write_attempts"] += 1
                    log("write_attempt", wire_hex=item["request_hex"])
                    try:
                        written = io.write(request)
                    except Exception as exc:
                        log("write_error", error=str(exc))
                        raise Stop("write fault") from exc
                    completed = clock()
                    log("write_result", bytes_returned=written, completed=completed)
                    if written != len(request):
                        raise Stop("short write")
                    reply = Reply(opcode, payload, log)
                    item["transport"] = observe(io, clock, completed, WINDOW, rx, log, limit=limit, reply=reply)
                    item["reply"] = reply.finish()
                detail = item["reply"]
                item["strict"] = (item["transport"]["complete"] and detail["primary_complete"]
                                  and not detail["malformed_or_ambiguous"] and detail["status_frames"] == 0
                                  and detail.get("aggregate") != "FAIL")
                if not item["transport"]["complete"] or detail["malformed_or_ambiguous"] or not detail["primary_complete"]:
                    raise Stop("incomplete or ambiguous response")
                if detail["status_frames"] and not allow_timer_consistent_status:
                    raise Stop("unattributed status requires separately reviewed continuation policy")
                if stage == "baseline":
                    baseline = (directory / "baseline_payload.bin").read_bytes()
                    item["settings_context"] = {"ch1_display": baseline[0], "ch2_display": baseline[10],
                                                "trigger_state": baseline[20], "display_format": baseline[179],
                                                "measurement_slots_hex": baseline[189:205].hex()}
            final = (directory / "final_payload.bin").read_bytes()
            result["settings_comparison"] = compare_settings(baseline, final)
            if result["settings_comparison"]["configuration_or_unclassified_changes"]:
                raise Stop("unexplained settings differences")
            result["sequence_complete"] = True
            log("state", state="DONE")
        except (Stop, OSError, ValueError, KeyboardInterrupt) as e:
            log("state", state="ABORT")
            result["sequence_complete"] = False
            result["stop_reason"] = str(e)
        result["strict_session"] = (result["sequence_complete"] and all(item["strict"] for item in result["steps"]))
        log("session_result", **result)
    with (directory / "session.json").open("x", encoding="utf-8") as final:
        json.dump(result, final, indent=2)
    return result
