# ACQ-05 — Paired CH1/CH2 retrieval architecture and offline evidence review

**Status:** Offline implementation candidate only. No USB execution, paired-transfer qualification, automated acquisition, or release approval.

## Decision and evidence

- HPA-P01/P02/P03: reuse the existing checksum/framing, S10/S90 file grammar and exact S92 classifier.
- HPA-A01/A03: manually exported CH1 and CH2 CSVs have matching observed axes, but this is **not** common-trigger, same-shot or sample-generation proof.
- HPA-S01/HPA-L01: reuse exclusive host lease, WinUSB claim, interruption marker and conservative stop behavior. The CH1 and CH2 single-file commands have each passed one independently supervised retrieval on the original DSO5102P firmware, not an automatic sequence.
- CH1 qualified artifact: `WaveData163.csv`, 89,566 bytes; S10 one request, zero retries, one successful retrieval.
- CH2 qualified artifact: `WaveData164.csv`, 89,193 bytes, SHA-256 `e50bb350e4e0577952f3d6f3dd67e08663b647321c2a1a7b11489f9e4b2ad076`; on 2026-11-24 the one-shot returned 9 S90 data frames, one terminal, one exact S92, 12-second silent preflight, one S10 write, no retries, verified output and cleanup. The CH2 manifest reports `source_commit: null`; the independently staged code was Git commit `199b75c998b2261a99bf134c359e705fb26f14af`.

Prior art coverage applies to **individual file retrieval only**. New transfer sequencing is a distinct qualification boundary.

## Implemented: `hantek offline pair-review`

This command **never opens USB**. It requires for **each channel**: the original strict transfer manifest, the retrieved CSV, the private evidence directory containing `request_attempt.bin`, `preflight_rx.bin`, and `response_rx.bin`, and an independently supplied 64-hex reference digest.

It checks:
1. Two complete successful manifests for exact filenames, sizes, one request and zero retries; settled marker and interface/lease cleanup.
2. Raw file sizes and SHA-256 against manifest evidence records; exact request bytes.
3. Reconstructs the framed S90 data from saved response RX, recognizes only the exact known S92 shape, and rechecks per-frame/terminal integrity through the existing parser.
4. Confirms the reconstructed bytes match both the retrieved CSV and the independent digest.
5. Writes one new output without overwrite; records individual file verification but **always** reports `same_shot: null`, `acquisition_coherence: UNPROVEN`, and `automated_paired_retrieval_qualified: false`.

This is validation of **provided evidence**, not a proof that the raw evidence was independently captured on the claimed instrument or that manifests have been signed. Validate provenance externally. Do not upload raw captures to the public repository.

### Usage

Use existing private CH1 and CH2 evidence in their own historical folders:

```powershell
hantek offline pair-review `
  --ch1-manifest 'C:\Private\CH1\transfer_manifest.json' `
  --ch1-csv 'C:\Private\CH1\WaveData163.csv' `
  --ch1-evidence 'C:\Private\CH1' `
  --ch1-sha256 '69080b701123c1a5faeac60eb571205c43f4e96517f9f8485451739296ea31f4' `
  --ch2-manifest 'C:\Private\CH2\transfer_manifest.json' `
  --ch2-csv 'C:\Private\CH2\WaveData164.csv' `
  --ch2-evidence 'C:\Private\CH2' `
  --ch2-sha256 'e50bb350e4e0577952f3d6f3dd67e08663b647321c2a1a7b11489f9e4b2ad076' `
  --output 'C:\Private\pair-review.json'
```

**Preconditions:** Use a source checkout with this candidate installed, provide each manifest's exact original evidence directory and retrieved output file, and choose an absent output path with an existing parent directory. The command does not alter input artifacts.

## Proposed next phase: sequential S10 controller (NOT implemented or authorized)

A paired controller must be a **single bounded operation**, not a shell loop that calls `files retrieve` twice:

1. Before any claim, verify both independent reference CSVs, filenames, exact sizes and expected digests, two fresh output paths and one absent fresh evidence root; establish operator confirmations and no unsettled marker.
2. Acquire one cooperative lease and perform one host census, inspect the device once and claim once. Establish an interruption marker before USB access. An abandoned marker blocks entry.
3. Run a full 12-second receive-only preflight, permitting only the specifically reviewed behavior. **A quiet preflight is the conservative default for both channels in the new workflow.**
4. Send CH1 S10 *once*, observe its full 12-second RX window, parse and validate all frames, terminal and independent digest; record physical-interface cleanup risk. Do **not** publish a result that implies CH2 completion.
5. Require a new 12-second receive-only silent inter-request guard. **Any** bytes, including S92, are an ambiguity for sequencing: STOP, retain marker, do not attempt CH2.
6. Send CH2 S10 *once*, run the same bounded response window, verify exact bytes and digest. No resend, no retry, no reconnect, no fallback, and no file enumeration.
7. Release interface and lease, clear marker only on fully confirmed cleanup, then publish the two outputs and an aggregate manifest with explicit per-file status. Preserve raw evidence for both attempts.
8. If CH1 succeeds but CH2 fails, explicitly report partial success and **do not** publish a success-level pair manifest; never infer that the two files share one acquisition generation.

**Unresolved implementation decisions for separate review:** whether the observed S92 timing changes with sequential requests, whether intervening 12-second silence suffices as a firmware-settle gate, how to preserve independently verified CH1 bytes if CH2 fails, and how to make two-file publication robust to host crashes. No observation so far establishes safe same-session back-to-back operation.

## Qualification plan

**Offline only first:** test normal sequences, unsolicited status in guard, short/ambiguous USB write, CH1/CH2 checksum/length/hash failures, partial terminal, disconnect, overbudget, cleanup errors, prior marker, duplicate destination, stale reference, and output publication failure. Require zero CH2 writes on any CH1 or inter-request failure. All regression tests must pass in Windows and hosted CI.

**Hardware gate separately:** Explicit architecture approval for one supervised sequence after evidence review. Confirm current files on the front stick, no active Save/Recall, RUN/STOP stopped, correct firmware, independent references, no competing client, and preservation of evidence. Do not repeat completed individual CH1/CH2 qualification merely for baseline.

## Version and data handling

- Published `v0.2.0` remains immutable and CH1-only.
- This branch does **not** qualify shared acquisition, triggering, remote Save/Recall, S02, arbitrary file access, automatic startup, or restoration.
- Do not commit original CSVs, USB captures, host inventories, or other private evidence.
- Maintain a distinct code/source commit SHA, and an artifact digest when packaged; `source_commit: null` in an installed CLI is not proof of a different source.
