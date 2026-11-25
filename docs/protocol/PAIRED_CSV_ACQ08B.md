# ACQ-08B bounded S92 guard qualification candidate

**Offline validation only. Hardware qualification pending.** The HANTEK-ADR-003 handoff authorizes one isolated candidate from PR #10. ACQ-07 remains the strict default. This candidate has no authority to access hardware or retire an interruption marker. [Issue #1](https://github.com/zapawc/hantek-dso5k-control/issues/1) · [PR #10](https://github.com/zapawc/hantek-dso5k-control/pull/10)

## Guard policy and outcomes

`files retrieve-pair --experimental-s92-guard` requires the existing clean Git pin, independent references, six operator confirmations, and interactive `YES`. It retains strict 12-second startup silence, a single CH1 S10 and the full 12-second CH1 response and verification. The experiment starts only in the inter-request guard.

- Starting at the first byte, accept only the exact prefix of `53 04 00 92 01 00 EA`. Each incomplete prefix must finish by the original 12-second deadline. No byte scanning or resynchronization. Coalesced trailing traffic fails even if it begins with the exact frame.
- From the read completion that delivered the final byte, require a new 12-second quiet interval. Allow no further transfers. Both this interval and its decision are bounded by 24 seconds from original guard start. A native read or journal I/O may return late. If the host observes a cap overrun or lacks a final receive poll through the quiet deadline, it fails closed. A timeout with no bytes is expected when observing quiet.
- Successful qualification guard makes CH2 eligible for its one exact S10. The controller still applies the destination gate and full CH2 validation. S92 records an origin-unresolved transfer-status observation. It provides neither a primary response nor readiness.
- A full original guard with zero received bytes returns aggregate `INCONCLUSIVE` (exit 7) after normal cleanup, with zero CH2 attempts and no CSV publication. Normal cleanup includes ordinary marker removal only for this newly created session marker after confirmed releases. Any cleanup fault instead returns aggregate `ERROR`.
- Wrong bytes, incomplete prefixes, expiry, duplicate, non-timeout transport error or cancellation fail the guard and prevent CH2; preserve the marker and raw evidence after the usual cleanup attempt. After an eligible guard, later CH2, cleanup or publication failure still makes the aggregate result fail. File publication is independently no-overwrite and non-atomic across both outputs.

The existing strict default retains its original startup, response and guard behavior. In particular, a strict quiet inter-request guard permits CH2 and a strict guard S92 aborts. The passive subcommand has no experimental flag.

## Evidence and compatibility

The explicitly selected experiment reports `schema_version: 0.2-s92-qualification` in CLI/aggregate results once the controller is entered. The existing top-level fields and strict manifest versions are preserved. The experimental `inter_request_guard` keeps the existing window counters and adds:

- `policy`, `original_start`, `original_deadline`, `absolute_deadline`
- `classification`, `first_byte_observed`, `final_byte_observed`, `quiet_start`, `quiet_deadline`
- `outcome` (`NOT_RUN`, `CH2_ELIGIBLE`, `INCONCLUSIVE`, or `FAIL`) and `stop_reason`

Times are monotonic host read completions or computed deadlines, in seconds. The first observed byte time is recorded even for an invalid prefix. The final byte time and status classification are populated only for an accepted exact frame. Journal entries preserve API transfer boundaries, raw offsets, UTC evidence, and guard events. Aggregate success still requires both independently verified outputs, normal cleanup and manifest finalization. Consumers should check the aggregate status as well as per-channel attempt counts and released/marker flags. Existing single-file and passive schemas are untouched. The experimental schema and new exit code require architecture review before consumer adoption.

## Prior art and next evidence

ACQ-07H recorded one exact guard S92 about 2.973 seconds into the guard, after the complete CH1 response and independent hash validation. Offline replay of the recorded prefix retains the strict abort and verifies the experimental restart with a synthetic quiet suffix. That suffix has no corresponding hardware observation. HPA-P01/P02/P03 support exact framing and file decoding, HPA-S01/L01 support ownership and fail-closed evidence, and HPA-A01/A03 bound acquisition provenance. ACQ-08A identified overlapping firmware paths capable of emitting identical S92 bytes. A bounded quiet interval cannot identify the emitter or prove future silence. [Strict adapter contract](PAIRED_CSV_ACQ07.md) · [Prior-art index](../prior-art/INDEX.md)

The minimum later qualification is one separately authorized supervised session. Resolve the retained ACQ-07H marker through a separately reviewed procedure, verify both references and fresh paths, rerun the passive preflight, and reconfirm exact closed files, original firmware, settled history, idle Save/Recall, stopped acquisition and exclusive ownership. In the existing documented PowerShell invocation enable `--experimental-s92-guard`, retain all six flags and require the operator to personally type `YES`. No extra acquisition or status commands, retries, firmware changes or resets. A guard without the target frame is inconclusive with zero CH2 writes. One passing targeted run remains limited to the observed conditions. Acquisition coherence remains `UNPROVEN`.
