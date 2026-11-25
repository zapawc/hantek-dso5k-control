# ACQ-07 supervised paired retrieval candidate

**ACQ-07H demonstrated the strict guard's protective stop on hardware.** The adapter targets the exact staged CH1 `/mnt/udisk/WaveData163.csv` (89,566 bytes) and CH2 `/mnt/udisk/WaveData164.csv` (89,193 bytes). Supply each independently approved reference digest at invocation. Future sessions require separate authorization.

## ACQ-07H supervised guard stop

At exact source [`a672dfca5288b7fde6f3ab8a9c6ef9a990b958d3`](https://github.com/zapawc/hantek-dso5k-control/commit/a672dfca5288b7fde6f3ab8a9c6ef9a990b958d3) on a DSO5102P with firmware 3.40.0 (151009.0), the strict 12-second startup was quiet and CH1 passed independent SHA-256 validation for 89,566 bytes. The strict inter-request guard received one exact seven-byte S92 at +2.973 seconds into the guard, or +14.979 seconds from CH1 write completion. The controller withheld both outputs and stopped before CH2. There was one attempt, zero retries, confirmed interface and lease release, and a retained marker. The aggregate status was ERROR. [ACQ-08H's later PASS](PAIRED_CSV_ACQ08B.md#acq-08h-supervised-hardware-result) exercised the explicitly selected experimental policy.

## Prior art and reuse

HPA-P01/P02/P03 cover common framing, S10/S90 file decoding and the exact S92 classifier. HPA-S01/L01 cover Windows PnP and process census, the cooperative lease, WinUSB claim, journal and interruption marker. HPA-A01/A03 cover the staged-export workflow and separate acquisition provenance gap. ACQ-05 and ACQ-06 provide historical replay and injected session sequencing. The two hardware-qualified individual transfers do not establish an in-session sequence. The new adapter wires the injected controller to the existing `ClaimedBulkIO`, so polling translates PyUSB timeouts and writes retain the existing 2,000 ms timeout. ACQ-07H observed the strict guard stop on hardware. [Issue #1](https://github.com/zapawc/hantek-dso5k-control/issues/1) · [ACQ-05](PAIRED_CSV_ACQ05.md) · [ACQ-06](PAIRED_CSV_ACQ06.md) · [Integration ADR](../adr/0001-cross-project-integration.md)

## Passive preflight and proposed supervised invocation

Run the candidate from a clean reviewed source worktree. Set `$Python` to the existing Python 3.11 WinUSB-enabled virtual environment, `$Candidate` to the candidate worktree, and `$ApprovedCommit` to the exact architecture-reviewed Git SHA. Use separate existing independent references and three fresh absent paths with existing parents.

```powershell
$env:PYTHONPATH = Join-Path $Candidate 'src'
$pairArgs = @(
  '--source-commit', $ApprovedCommit,
  '--ch1-reference', $CH1Reference, '--ch1-sha256', $CH1Digest, '--ch1-output', $CH1Output,
  '--ch2-reference', $CH2Reference, '--ch2-sha256', $CH2Digest, '--ch2-output', $CH2Output,
  '--evidence-dir', $EvidenceDir
)
& $Python -B -m hantek_dso5k files pair-preflight @pairArgs
```

Passive preflight checks the loaded worktree and clean Git status, reference paths and full digests, fresh destinations, marker absence, and the existing Windows PnP/process/assignment assessment. It does not acquire a mutex, import a USB backend, enumerate via libusb, open an interface, or create a file. The census cannot establish exclusive ownership. Missing process command lines limit its coverage. Imports and CLI help remain passive.

Only after separate run-specific approval and immediately before the supervised command, confirm the DSO5102P and original firmware identity, correct rear connection and settled command history, exact closed exported files on the front drive, idle Save/Recall, stopped acquisition and red RUN/STOP, exclusive host ownership, reviewed references, fresh paths and adequate evidence space. Prior power-cycling and the displayed traces do not establish acquisition generation. Firmware uses `r+b` in this path and FAT metadata or UI activity may change.

```powershell
& $Python -B -m hantek_dso5k files retrieve-pair @pairArgs `
  --confirm-device-identity --confirm-single-client --confirm-settled-history `
  --confirm-completed-file-present --confirm-no-active-save-recall --confirm-independent-reference
```

The active route requires the six flags, interactive stdin/stderr and a single `YES`. It verifies the source again at the prompt boundary. The controller verifies paths and references, takes the cooperative lease, screens ownership under the lease, creates the marker exclusively, and claims the interface. It requires 12 seconds of silence, one CH1 request and a full 12-second response and digest check, another 12 seconds of silence, then one CH2 request and its full 12-second response and digest check. Exact S92 is tracked in response windows. Every byte in a silent window stops progress. The controller has no retry path.

## Failure and evidence matrix

Counts are write attempts. A failed or interrupted write may still have reached the device. Release failures leave the corresponding cleanup flag false. An absent marker in a preclaim blocker means none was created. A previously retained marker is preserved.

| Fault or stop | CH1 / CH2 attempts | CSV output | Marker and ownership disposition |
| --- | --- | --- | --- |
| Wrong source, invalid reference, destination collision at admission, declined confirmation | 0 / 0 | None | No new marker, no lease |
| Existing marker or competing owner | 0 / 0 | None | Existing marker preserved or no marker created. Acquired lease is released |
| Claim failure or traffic in silent preflight | 0 / 0 | None | Marker retained. Cleanup attempted for resources acquired |
| CH1 ambiguity, invalid response or wrong digest | 1 / 0 | None | Marker retained. Interface and lease cleanup attempted |
| Inter-request traffic or new destination collision after CH1 | 1 / 0 | None, CH1 raw evidence retained | Marker retained. Interface and lease cleanup attempted |
| CH2 short write, disconnect, invalid response or digest | 1 / 1 | None, CH1 verification and raw evidence retained | Marker retained. Interface and lease cleanup attempted |
| Ctrl+C after marker creation during the session | Up to the current phase | None | Marker retained. Unwind attempts interface and lease release. Windows native calls may delay delivery of Ctrl+C |
| Interface or lease release failure | Up to 1 / 1 | None | Marker retained; failing release unconfirmed |
| Marker removal failure | 1 / 1 | None | Clearance unconfirmed, interface and lease released |
| Local publication or evidence finalization failure | 1 / 1 | Zero, one or two verified files may remain. Preserve all existing outputs | Marker already cleared after USB and lease cleanup. CLI reports aggregate ERROR |
| Success | 1 / 1 | Both verified, separately published | Marker cleared after confirmed interface and lease release |

The aggregate manifest and raw request and receive files share one fresh private evidence directory. Zero-byte preflight and guard files are valid evidence. A crash or local disk error after the first publication can leave one output or an incomplete manifest. Each publication is independently no-overwrite, and verified files are never rolled back by deleting them. Use both explicit per-file flags and an aggregate PASS manifest before reporting pair completion. If finalization fails, preserve raw evidence and CLI error output.

The origin of S92 remains unresolved. Unknown or incorrectly placed traffic can stop the sequence. A silent guard demonstrates only what was observed during that window. Pair retrieval preserves `same_shot: null`, `acquisition_coherence: UNPROVEN` and `hardware_qualified: false` pending independent qualification. No retry or fallback is authorized after an ambiguous session.

**Preserve the host and marker gates for each future session.**

**Future active sessions require separate authorization.**
