# Hantek prior art and reuse index

Use this index before proposing or starting capability development. Begin with the [published v0.1.0 baseline](../releases/V0.1.0_RELEASE_NOTES.md), [current capability matrix](../capabilities/CURRENT.md), and [integration ADR](../adr/0001-cross-project-integration.md). Search the public source, issues, PRs, ADRs, and qualification records for the requested behavior. Review available authorized local archives and fixtures for the same operation and conditions. The index is a starting point for that review.

Repository-relative links point to the source tree containing this index. DEV-07 source, fixtures, and protocol documents link to the reviewed qualification revision. Use the [immutable v0.1.0 tag](https://github.com/zapawc/hantek-dso5k-control/tree/v0.1.0) to verify released support. The packaged S10 command, its exact-frame S92 classifier, and its transfer schema were merged through PR #3 and remain unreleased in the v0.2.0 candidate. DEV-06A qualified one transfer with the supervised predecessor; the installed DEV-07 CLI qualified one bounded CH1 S10 transfer on 2026-11-20 UTC. Hardware qualification is not a release.

### Evidence classifications

- **Historical observation** records what an earlier run or operator observed, including incomplete or failed runs.
- **Offline verified** means static analysis, fixture testing, or archived replay. Replay preserves only the data and timing information actually recorded.
- **Hardware qualified** applies to the specific operation, device/firmware, implementation or predecessor, and conditions demonstrated by the evidence.
- **Released support** applies to the contract shipped at an immutable tag or artifact. Draft and local-only code have their own maturity.

These classifications may coexist. A tested predecessor can support reuse while an integrated command remains offline-verified. Record a narrow evidence gap when state, protocol semantics, implementation, conditions, or safety constraints materially change. Keep IDs stable when updating a record. Add public summaries and opaque archive references where permitted. Keep exact restricted artifact paths, raw captures, vendor material and private evidence in the authorized local locator.

## Protocol

| ID | Capability | Evidence and source | Limits and reuse |
| --- | --- | --- | --- |
| HPA-P01 | S framing and per-frame checksum | Hardware-qualified within the published read-only batch. [_framing.py](../../src/hantek_dso5k/_framing.py), [offline replay tests](https://github.com/zapawc/hantek-dso5k-control/blob/895c518eeb3cac8333bc5960fc2d3fdff0b6992d/tests/test_file_replay.py) | Reuse the common length/checksum primitives. A checksum-valid frame still needs opcode, ordering and request correlation. |
| HPA-P02 | S10/S90 staged file retrieval | Hardware-qualified for one closed CH1 CSV in DEV-06A and one installed DEV-07 CLI run on 2026-11-20 UTC. Historical 64 KiB kernel segments used the same file grammar. Packaged command was merged in [PR #3](https://github.com/zapawc/hantek-dso5k-control/pull/3). [S10 evidence](https://github.com/zapawc/hantek-dso5k-control/blob/895c518eeb3cac8333bc5960fc2d3fdff0b6992d/docs/protocol/NATIVE_CSV_RETRIEVAL.md), [CLI profile](https://github.com/zapawc/hantek-dso5k-control/blob/895c518eeb3cac8333bc5960fc2d3fdff0b6992d/docs/protocol/NATIVE_CSV_CLI.md), [_file_replay.py](https://github.com/zapawc/hantek-dso5k-control/blob/895c518eeb3cac8333bc5960fc2d3fdff0b6992d/src/hantek_dso5k/_file_replay.py), [retrieval tests](https://github.com/zapawc/hantek-dso5k-control/blob/895c518eeb3cac8333bc5960fc2d3fdff0b6992d/tests/test_retrieve.py) | Reuse the strict chunk/terminal parser and independent SHA-256. The command restricts the path and 89,566-byte profile and remains unreleased in v0.2.0. The packaged run verified 89,566 bytes, 9 data frames plus terminal C3, SHA-256 match, zero retries, cleanup and visually preserved frozen traces. It observed zero S92 notifications; DEV-07 S92 handling remains offline-verified. The installed manifest reported `source_commit: null`; staging independently pinned `895c518eeb3cac8333bc5960fc2d3fdff0b6992d`. Record the approved full source SHA and verified wheel SHA-256 as the external release provenance. Historical extraction and this run do not qualify arbitrary paths, sizes, CH2 or back-to-back transfers. [Sanitized hardware result](https://github.com/zapawc/hantek-dso5k-control/pull/3#issuecomment-7052889293). |
| HPA-P03 | S92 status classification | Hardware-observed in screen, measurement and file sessions. DEV-07 exact-frame streaming classification tested offline. [_receive.py](https://github.com/zapawc/hantek-dso5k-control/blob/895c518eeb3cac8333bc5960fc2d3fdff0b6992d/src/hantek_dso5k/_receive.py), [CLI status policy](https://github.com/zapawc/hantek-dso5k-control/blob/895c518eeb3cac8333bc5960fc2d3fdff0b6992d/docs/protocol/NATIVE_CSV_CLI.md), [published batch contract](../CLI_CONTRACT.md) | Separate status from primary replies. Preserve raw evidence and unresolved origin. Reuse classification with each command's timing/acceptance policy. |
| HPA-P04 | S22 measurement read | Hardware-observed checksum-valid S A2 with unavailable `?` for CH1 Frequency. Strict session FAIL remains historical evidence. [Qualification gates](../protocol/QUALIFICATION_GATES.md), [Issue #1](https://github.com/zapawc/hantek-dso5k-control/issues/1), local FW-CAP-03G/03H | Availability, plausibility and freshness need independent evidence. Reuse the trace and fixtures before considering a new stimulus-controlled test. |
| HPA-P05 | Clock and status | Clock read hardware-qualified in the local predecessor. CLI clock operations remain reserved. [Clock plan](../CLOCK_API_AND_SYNC_PLAN.md), [CLI contract](../CLI_CONTRACT.md) | Reuse the read evidence and comparison plan. Write, synchronization and timing guarantees have separate gates. |

## Acquisition

**ACQ-05 proposed reuse (HPA-P01/P02/P03, HPA-A01/A03, HPA-S01, HPA-L01/L02):** Both bounded individual CH1 and CH2 S10 profiles have now been independently hardware-qualified. The offline pair-review candidate validates preserved transfer bytes, per-file manifests and hashes without asserting a common trigger. Live sequential two-file retrieval and acquisition coherence remain unqualified. See [ACQ-05 design and limits](../protocol/PAIRED_CSV_ACQ05.md).


**ACQ-02 update (HPA-P01, HPA-P02, HPA-P03, HPA-A01, HPA-S01, HPA-L01).** The immutable v0.2.0 release contains the qualified CH1 path only. The experimental CH2 candidate reuses the same framing, byte validation, lease and marker, with an explicit reference digest and a silent preflight for CH2. Coverage is synthetic and offline. The exact closed CH2 file on the instrument is unverified. See the [experimental profile](../protocol/NATIVE_CSV_CLI.md#experimental-ch2-offline-candidate).

| ID | Capability | Evidence and source | Limits and reuse |
| --- | --- | --- | --- |
| HPA-A01 | Native capture-then-extract | Operator-observed frozen traces after manual SINGLE and CH1/CH2 Save/Recall. Offline CSV size, hashes, rows and time-axis comparison. [Investigation issue](https://github.com/zapawc/hantek-dso5k-control/issues/1), [CSV workflow](https://github.com/zapawc/hantek-dso5k-control/blob/895c518eeb3cac8333bc5960fc2d3fdff0b6992d/docs/protocol/NATIVE_CSV_CLI.md), local F1/F2 and DEV-03/04 | Prefer the staged workflow. Common time axes alone do not establish hardware simultaneity. Remote Save, CH2 S10, and capture automation remain unqualified. |
| HPA-A02 | S02 waveform and buffer use | Offline waveform conversion in the release. Static investigation of live buffer mutation and mutex hold. [_waveform.py](../../src/hantek_dso5k/_waveform.py), [investigation draft PR #2](https://github.com/zapawc/hantek-dso5k-control/pull/2), [gates](../protocol/QUALIFICATION_GATES.md), local DEV-02/03 | Reuse the converter and transfer-safety trace. Live S02 remains on HOLD. An acknowledgment cannot establish idempotence, freshness or a stable sample buffer. |
| HPA-A03 | Automated SINGLE, EXT and coherent two-channel capture | Offline mode and evidence investigation. [Issue #1](https://github.com/zapawc/hantek-dso5k-control/issues/1), [qualification gates](../protocol/QUALIFICATION_GATES.md) | Need independently observable completion, same-shot identity or common latch, and reversible state changes before qualification. Leave unavailable in consumers until scoped evidence and an immutable delivery exist. |

## Hardware qualification

| ID | Capability | Evidence and source | Limits and reuse |
| --- | --- | --- | --- |
| HPA-H01 | Windows read-only settings and screens | Released preview; tested predecessor completed four primary replies and stable settings comparison. [v0.1.0 release notes](../releases/V0.1.0_RELEASE_NOTES.md), [qualification tests](../../tests/test_qualification.py), local HANTEK-GIT-03 | Qualitative screens; graphics aggregate discrepancies and classified S92 produced strict FAIL/exit 5. Preserve these outcome dimensions. Reuse the qualified operation within its scope. |
| HPA-H02 | Firmware backup and segmented transport | Historical verified kernel reassembly and eight preserved physical partition images. Local NAND consolidation, kernel-segment and handoff records | Reuse hashes, on-wire segment evidence and partition provenance. Live full-image USB transfer, NAND restoration, and arbitrary file reads are independent questions. Keep acquisition scripts and all binary evidence local. |
| HPA-H03 | Logic analyzer | Windows one-capture qualification on the FX2LP analyzer. Offline-only Hantek ARM USB-host feasibility. Local LA-00B/LA-01 | Windows qualification covers the bounded input-free test. Hantek deployment and acquisition require separate evidence and approval. |

## Software components

| ID | Capability | Evidence and source | Limits and reuse |
| --- | --- | --- | --- |
| HPA-S01 | Discovery, lease, marker and bulk transport | Released read-only path and merged, unreleased shared bulk adapter. [_host.py](../../src/hantek_dso5k/_host.py), [_winusb.py](https://github.com/zapawc/hantek-dso5k-control/blob/895c518eeb3cac8333bc5960fc2d3fdff0b6992d/src/hantek_dso5k/_winusb.py), [_session.py](../../src/hantek_dso5k/_session.py), [ownership tests](../../tests/test_ownership.py) | Reuse host census, lease, existing marker and interface validation. A process census alone cannot prove ownership. Preserve conservative cleanup and failure behavior. |
| HPA-S02 | Settings, graphics and structured artifacts | Released schemas and qualified read-only batch. [_settings.py](../../src/hantek_dso5k/_settings.py), [_graphics.py](../../src/hantek_dso5k/_graphics.py), [_rgb565.py](../../src/hantek_dso5k/_rgb565.py), [settings schema](../../schemas/settings-v2.schema.json), [capture schema](../../schemas/capture-manifest-v2.schema.json) | Reuse the existing decoders and versioned contracts. Keep the [transfer schema](https://github.com/zapawc/hantek-dso5k-control/blob/895c518eeb3cac8333bc5960fc2d3fdff0b6992d/schemas/file-transfer-v1.schema.json) independent. |

## Known limitations

| ID | Capability | Evidence and source | Limits and reuse |
| --- | --- | --- | --- |
| HPA-L01 | Response integrity, cleanup and ambiguous history | Historical status and checksum discrepancies, timing uncertainty and zero-write DEV-06 stop. [Capability matrix](../capabilities/CURRENT.md), [qualification gates](../protocol/QUALIFICATION_GATES.md), local FW-CAP-03H and DEV-06A/B | Preserve frame, primary reply, strict session, cleanup and integrity results separately. Review interruption markers under the established lease; no blind retries, resets or flushes. |
| HPA-L02 | Protocol surface and unresolved operations | Offline dispatch inventory, capability registry and qualification backlog. [Qualification gates](../protocol/QUALIFICATION_GATES.md), local FW-CAP-04 | Check handler, side-effect and evidence records before proposing a new opcode. Classify genuine gaps explicitly. |

## Prior Art and Reuse Assessment

Include this compact section in proposals and development returns. Update affected records in the same reviewed change.

1. Cite the applicable IDs, exact source and fixtures, public issue/PR/ADR, and authorized local record locations.
2. State the previously implemented behavior, operating conditions, evidence class, and current support level. Note unresolved risks.
3. Name the components to reuse or modify and the actual missing capability. Explain any unavoidable duplicate implementation.
4. State whether existing qualification covers the proposed operation. If a material gap remains, name it and propose the smallest test that resolves it. Routine reuse within the established scope needs no repeat qualification.

The review and index grant no hardware, firmware, or release authorization. Continue to use the established approval process for newly scoped operations.
