"""Single exact S92 followed by a bounded quiet interval for ACQ-08B."""
import math

from ._framing import STATUS
from ._graphics import READ_BYTES
from ._receive import ReceiveError
from ._retrieve import window_result
from ._session import utc

GUARD_SECONDS = 12
POLICY = "experimental_single_s92_restart"


def qualification_guard_result():
    return {
        **window_result(), "policy": POLICY, "outcome": "NOT_RUN", "stop_reason": None,
        "original_start": None, "original_deadline": None, "absolute_deadline": None,
        "classification": None, "first_byte_observed": None, "final_byte_observed": None,
        "quiet_start": None, "quiet_deadline": None,
    }


def observe_qualification_guard(link, clock, rx, log, report):
    """Return true only for the targeted S92 and a full quiet interval."""
    start = clock()
    original_deadline = start + GUARD_SECONDS
    absolute_deadline = start + 2 * GUARD_SECONDS
    deadline = original_deadline
    report.update(original_start=start, original_deadline=original_deadline,
                  absolute_deadline=absolute_deadline)
    prefix = bytearray()
    poll = 0
    last_timeout = None
    try:
        log("guard_started", policy=POLICY, start=start, deadline=original_deadline,
            absolute_deadline=absolute_deadline)
        while (poll_start := clock()) < deadline:
            timeout = min(250, max(1, math.ceil((deadline - poll_start) * 1000)))
            poll += 1
            try:
                data = link.read(READ_BYTES, timeout_ms=timeout)
            except TimeoutError:
                last_timeout = clock()
                log("poll_timeout", poll=poll, started=poll_start, completed=last_timeout, timeout_ms=timeout)
                continue

            completed = clock()
            timestamp = utc()
            offset = report["raw_bytes"]
            rx.write(data)
            rx.flush()
            report["raw_bytes"] += len(data)
            log("receive", poll=poll, started=poll_start, completed=completed, offset=offset,
                length=len(data), requested=READ_BYTES, in_window=completed <= deadline,
                capture_utc=timestamp)
            if completed > absolute_deadline:
                raise ReceiveError("absolute inter-request guard deadline exceeded")
            if report["quiet_start"] is not None:
                raise ReceiveError("unexpected transfer during restarted quiet interval")
            if completed > original_deadline:
                raise ReceiveError("S92 did not complete within original guard deadline")
            if not data or len(data) > READ_BYTES:
                raise ReceiveError("invalid transfer size during inter-request guard")
            if not prefix:
                report["first_byte_observed"] = completed
            prefix.extend(data)
            if not STATUS.startswith(prefix):
                raise ReceiveError("unexpected guard traffic; expected only one exact S92")
            if prefix == STATUS:
                deadline = completed + GUARD_SECONDS
                report.update(classification="transfer_status_origin_unresolved",
                              final_byte_observed=completed, recognized_status_frames=1,
                              quiet_start=completed, quiet_deadline=deadline)
                log("recognized_status", classification=report["classification"],
                    wire_hex=STATUS.hex(" ").upper(), first_byte_observed=report["first_byte_observed"],
                    final_byte_observed=completed, quiet_start=completed, quiet_deadline=deadline)

        finished = clock()
        if finished > absolute_deadline:
            raise ReceiveError("absolute inter-request guard deadline exceeded")
        target_observed = report["recognized_status_frames"] == 1
        if prefix and not target_observed:
            raise ReceiveError("incomplete S92 at original guard deadline")
        if target_observed and (last_timeout is None or last_timeout < deadline):
            raise ReceiveError("restarted quiet interval ended without a final receive poll")
        outcome = "CH2_ELIGIBLE" if target_observed else "INCONCLUSIVE"
        reason = None if target_observed else "no exact S92 observed during original guard"
        log("guard_completed", outcome=outcome, stop_reason=reason, completed=finished)
        if clock() > absolute_deadline:
            raise ReceiveError("absolute inter-request guard deadline exceeded")
        report.update(complete=True, outcome=outcome, stop_reason=reason)
        return target_observed
    except (Exception, KeyboardInterrupt) as exc:
        report.update(outcome="FAIL", stop_reason=str(exc) or type(exc).__name__)
        raise
