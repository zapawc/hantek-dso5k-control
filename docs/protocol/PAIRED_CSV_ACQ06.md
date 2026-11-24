# ACQ-06 — Injected-only sequential S10 controller

**State:** Offline simulation candidate, **not hardware-qualified**, no operator-executable paired USB command.

The ACQ-05 review of independently completed CH1/CH2 evidence passed. It established file integrity, not common-trigger or same-shot identity. This milestone models bounded **CH1 then CH2** S10 in one claimed session using the existing framing/receive/crypto and host-cleanup abstractions.

## Implemented

`src/hantek_dso5k/_pair_session.py` exports `simulate_pair` with **no default transport, claim, ownership-check, lease, clock, or interruption marker**. It rejects calls unless all are supplied explicitly by offline tests. The production CLI does **not** expose this function. There is no live backend, hardware adapter, or runner.

The controller checks both reference sizes (CH1 89,566; CH2 89,193) and independently supplied SHA-256 values, absent distinct outputs and evidence paths, exact six operator confirmations, absent unresolved marker and ownership census. It then performs:

1. One cooperative injected lease and one injected interface claim, with interruption marker established before claim.
2. Full **12-second silent receive-only preflight**, any bytes abort.
3. Exactly one CH1 S10 request; full 12-second receive window, strict S90 frame/terminal/reference digest validation.
4. Full **12-second silent inter-request guard**; any bytes including S92 stop with zero CH2 writes.
5. Exactly one CH2 S10 request; full 12-second bounded response and exact independent digest validation.
6. Interface and lease cleanup; marker clear only when both transfers and cleanup succeeded.
7. Publish two separate CSVs without overwrite, and produce a separate manifest with `same_shot: null`, `acquisition_coherence: UNPROVEN`, and `hardware_qualified: false`.

The RX windows use the existing known-S92 classifier. During file replies, an exact S92 is classified and recorded without polluting S90 data; during silent windows **any** traffic aborts.

**Failure state:** Any preflight, CH1, guard, CH2, or cleanup failure retains the marker after creation, keeps the aggregate operation in ERROR and preserves private raw evidence. A CH1 verified result may exist in an error manifest, but published output is withheld unless **both** retrievals and cleanup complete. No retries, no fallback to CH1, no automatic reconnect or acquisition control.

**Publication limitation:** Two destination files cannot be atomically published together via the existing `publish_file` utility. If publishing CH1 succeeds then CH2 publication fails, CH1 remains published and the aggregate manifest records ERROR and explicit per-file publication state. Do **not** interpret this as successful pair completion. Existing output is never replaced.

## Tested offline

Synthetic responses cover ordinary success, S92 during inter-request silence, CH1 invalid digest/checksum, CH2 incomplete response, ambiguous first write, short second write, preflight status, existing interruption marker, invalid reference, cleanup failure, and rejection of live defaults. The regression suite includes pre-existing one-file CH1/CH2 tests and offline pair evidence review. CI is required before merging.

## Unresolved for hardware qualification

- Does firmware remain stable after two S10s in one ownership session? A full silent guard may not prove the filesystem read handles have closed; it is an observation boundary, not a proof.
- Is one exact S92 still accepted within each response window and is it always status-only? The origin remains unresolved.
- Does the original firmware preserve file contents without unexpected metadata effects? It opens an existing file using `r+b`; content writes were not observed in traced ARM code.
- Operator verification of closed existing files, model/firmware, stopped acquisition, Save/Recall idle, and no competing USB client must be **contemporaneous** with any separately approved live test.
- Same-shot coherence requires a different acquisition provenance mechanism and is outside ACQ-06.

**Next:** independent review of evidence and actual Windows-host staging, then a **separately approved** minimal live adapter and single supervised sequence. No GitHub merge or hosted CI run authorizes a live paired test. Preserve reference CSVs and raw USB evidence off-repo.

**Prior art:** HPA-P01/P02/P03 protocol, HPA-S01/L01 session/lease, ACQ-04 CH2 live qualification, ACQ-05 paired offline evidence check and its conservative architecture.
