# Native CSV retrieval in the DEV-07 draft

The Windows CLI retrieves one completed, previously exported CSV. Capture, freeze and export remain operator actions. The command is gated to `/mnt/udisk/WaveData163.csv` and the 89,566-byte profile on the original 2015 DSO5102P. The DEV-06A supervised runner qualified one such transfer. The DEV-07 packaged CLI passed a separately authorized, bounded CH1 hardware retrieval on 2026-11-20 UTC. This draft functionality is not yet released; future hardware activity still requires approval. See [qualification record](NATIVE_CSV_QUALIFICATION.md#dev-07-packaged-cli--completed-qualification-2026-11-20-utc).

## Command

Example after separate hardware authorization and current operator verification. The local reference must be an independently preserved copy of the exact closed file and its parent storage must be accessible. Use fresh local paths for the destination and evidence directory.

```powershell
hantek files retrieve --instrument-path /mnt/udisk/WaveData163.csv `
  --reference C:\Data\original.csv --output C:\Data\retrieved.csv `
  --evidence-dir C:\Data\transfer-evidence --max-bytes 89566 --transfer-timeout 12 `
  --confirm-device-identity --confirm-single-client --confirm-settled-history `
  --confirm-completed-file-present --confirm-no-active-save-recall `
  --confirm-independent-reference
```

The flags declare that the operator has verified the unit and original firmware, single-client ownership, settled USB history, the exact closed CSV on the writable front FAT32 mount, no active Save/Recall or other writer, and independent reference provenance. A Windows reference alone cannot establish device-side presence. The firmware opens the device file with `r+b`; there is no explicit content write in the reviewed S10 path. The operation can affect access metadata or USB/UI activity.

`--max-bytes` defaults to 89,566 and must be between 89,566 and 98,304. It is an admission ceiling for the fixed-size profile. Raising it does not permit a larger file. `--transfer-timeout` is in seconds and accepts only 12 for this profile. A shorter window could miss delayed unexpected bytes. Each raw RX window has a 102,400-byte cap inclusive of status traffic. Requests are aligned as needed to stay within that cap. The CLI uses a 12-second receive-only preflight and one 12-second window after the sole write. A fault may terminate observation early. There are no retries, resets, extra queries or file-enumeration operations.

## Result and diagnostics

One JSON object goes to stdout. A new `--evidence-dir` also receives `transfer_manifest.json` plus local raw USB API evidence. The report uses the [file-transfer 1.0 schema](../../schemas/file-transfer-v1.schema.json). It records reference and received size/digest, data frame count, checksum, accepted status counts by phase, output publication, device and cleanup evidence, marker disposition and error phase. Transfer completion means the bounded S90 reply was fully validated. An output collision or cleanup failure can still make the invocation fail. A retained marker requires review before another device session.

- Exit 0 means verified file publication without replacement.
- Exit 2 means CLI usage error. No transfer report is expected.
- Exit 3 means blocked before claim.
- Exit 4 means claim, transfer, cleanup, output publication or evidence finalization failed. Inspect the individual result fields.

The recognized S92 frame is recorded at the exact frame boundary, including USB transfer timestamps and its opaque payload. It may arrive fragmented or coalesced and is counted separately from S90 responses. Its origin remains unresolved. Other shapes and malformed or incomplete traffic block publication. The raw journal contains API-level transfer boundaries. It does not show physical USB packets or information suppressed by PyUSB. Existing settings and capture contracts retain schema version 2.0 and their established observation policies.

## Qualification coverage

| Capability | Status |
| --- | --- |
| One staged CH1 CSV through S10, byte count, terminal sum and independent SHA-256 | Hardware-qualified in DEV-06A predecessor and DEV-07 installed CLI, each for the bounded CH1 artifact; DEV-07 passed 2026-11-20 UTC |
| S92 status classification and clean interface, lease and marker cleanup | S92 observed in DEV-06A predecessor; packaged DEV-07 S92 handling offline-tested (zero S92 observed live), packaged DEV-07 cleanup hardware-qualified on 2026-11-20 UTC |
| Packaged retrieval orchestration, fragmented/coalesced notifications, failure paths and versioned JSON | Single bounded CH1 happy path hardware-qualified; additional timing, notification and failure cases remain offline regression and archive replay |
| CH2 S10, back-to-back automated transfers or arbitrary paths/sizes | Unqualified |
| Remote CSV Save, SINGLE SEQ, external triggering, sample freshness and same-shot CH1/CH2 coherence | Outside the retrieval contract and unqualified |

The DEV-07 live transfer manifest recorded `source_commit: null`; the staging log independently pins the tested checkout to `895c518eeb3cac8333bc5960fc2d3fdff0b6992d`. This distinction must be retained in reports and does not justify rerunning the device transfer.
