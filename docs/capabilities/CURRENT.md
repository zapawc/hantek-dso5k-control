# Capability matrix

| Capability | Evidence | Integrated status |
| --- | --- | --- |
| Windows discovery and ownership | Historical qualification found one WinUSB instance, excluded verified ancestry, and completed one leased claim and cleanup | Hardware-qualified for the reviewed Windows session with operator confirmation. Process census alone cannot prove ownership. |
| Settings | Historical qualification obtained two checksum-valid S81 replies of 208 bytes. All 199 stable bytes matched. | Qualified within the read-only batch for the tested DSO5102P. Trigger-state interpretation remains limited. |
| Screen | Historical qualification captured two complete 768,000-byte buffers with 77 checksum-valid frames each. Both decoded to readable 800 × 480 previews. | Qualified for qualitative remote viewing. Both aggregate checks disagreed. Atomic coherence and freshness remain unverified. |
| Async transfer status | One exact post-primary S92 in each screen window, with quiet startup and guards | Qualified classification and continuation for this observed shape and placement. Both statuses remain origin-unresolved. Strict session FAIL, exit 5. |
| CH1 waveform decode | Archived display window grammar and historical implementations | Offline conversion only, scale and timing inputs caller-supplied |
| S10 native CSV retrieval | DEV-06A predecessor matched one CH1 reference with an S92; installed DEV-07 CLI matched the same reference on 2026-11-20 UTC with zero S92 | Hardware-qualified only for the bounded packaged CH1 operation. Packaged S92 handling remains offline-tested. See [coverage and limits](../protocol/NATIVE_CSV_CLI.md#qualification-coverage). |
| Supervised staged CH1/CH2 CSV pair through S10 | ACQ-07H stopped on guard S92. ACQ-08H passed one experimental guard restart and independently verified 89,566 and 89,193 bytes in one ownership session. | Hardware-qualified for the observed supervised sequence at the [exact qualified source](../protocol/PAIRED_CSV_ACQ08B.md#acq-08h-supervised-hardware-result). Strict guard is the default. Unattended operation and acquisition coherence remain unproven. |
| S02 live retrieval and CH1/CH2 coherence | Transfer-state side effects and generation proof unresolved | Unavailable |
| Acquisition completion and SINGLE/trigger control | Shared UI state with ambiguous transitions | Unavailable |
| Reversible settings restoration | Exact inverse and persistence unproven | Unavailable |
| UTC clock read/compare/sync | Historical clock read, compare design and unqualified setter | Reserved CLI only |

Hardware evidence comes from local precursors. DEV-07 shares the existing claimed WinUSB bulk adapter with the qualification batch and keeps the batch's framing and status policies. The raw captures and reports live outside the public source tree. Device and firmware labels in manifests refer to the known test unit. The batch did not independently re-read firmware identity.
