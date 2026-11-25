# ACQ-10C supervised native waveform adapter

**ACQ-10H execution is on HOLD.** This adapter is an offline-validated candidate for one separately authorized session. Retained firmware borrows display sample storage and can clip samples there. It can hold the producer mutex across a blocking device-side TTY write. ACQ-10B confirmed a limit on returning write calls and found no defensible deadline for an individual blocked call or proof that host release wakes it. One supervised experiment may proceed only after review and express acceptance of the scoped volatile effects and possible lockup. [ADR-005](../adr/0005-proportionate-instrument-qualification.md) · [Retained firmware findings](NATIVE_WAVEFORM_ACQ10A.md#prior-art-and-original-firmware)

## Prospective invocation

From the reviewed ACQ-10C source worktree and its approved Windows Python/WinUSB environment, set `PYTHONPATH` to its `src` directory. After separate ACQ-10H authorization, use the internal qualification module in an interactive console.

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
python -B -m hantek_dso5k._native_qualification --source-commit <approved-full-sha> --authorization <run-approval-id> --fixture <approved-json-path> --fixture-sha256 <approved-sha256> --evidence <fresh-local-directory>
```

The authorization identifier records an external approval. It is not an authentication token. Supply the exact approved fixture digest and destination for that run. The operator verifies the physical DSO5102P and retained 3.40.0 (151009.0), correct rear USB, exclusive ownership and settled history, and accepts clipping, temporary disturbance and possible lockup requiring manual recovery. The operator personally types `ACCEPT ACQ-10H` only after verifying the preconditions and reading the recovery procedure. Authorization to implement ACQ-10C does not authorize this invocation.

## Fixture and expected values

Use ordinary RUN acquisition with disposable display data, both analog channels enabled, MATH off, Save/Recall idle, a stable non-roll/non-zoom timebase and a recorded 4K-depth viewport if supported. Allow several visible waveform periods. Prefer an existing low-voltage periodic source. The DSO5102P probe-compensator output is a practical fallback with manufacturer typical values around 5 Vpp and 1 kHz into the specified high-impedance load. Verify total loading and use a characterized attenuation path on one channel so the two physical scope inputs have distinguishable amplitudes. Keep the stimulus and measurement connections within their ratings. Use independently known source values, and an existing suitable meter or counter if available, to set broad disjoint voltage ranges and period ranges before capture. Record any reference based on typical specifications as nominal. A compatibility PASS establishes plausibility within those limits. Traceable calibration requires separate references and uncertainty analysis. The Workbench RD6006 can support separate DC plausibility checks and supplies no periodic reference on its own. [Hantek DSO5102P specifications](https://www.hantek.com/m/productdetail/96)

The private fixture JSON contains a descriptive `setup`, `running: true`, `math_enabled: false`, and two maps keyed by `CH1` and `CH2`.

| Map | Required contents |
| --- | --- |
| `channel_scaling` | The [ACQ-10A explicit scaling fields](NATIVE_WAVEFORM_ACQ10A.md#engineering-units), a `viewport` description, and the fixture's expected sample count (up to 10,000). Use probe-adjusted scale exactly once. Use a documented sample-zero reference for a relative time axis. |
| `expectations` | Per-channel `vpp_v` and `period_s` two-element inclusive intervals and a `reference` describing the independent expectation. Specify disjoint positive Vpp intervals to distinguish channel assignment. |

Use the recorded viewport and independently checked scale and timebase inputs to construct the fixture metadata. External 4K reports suggest 3,200 or 3,840 samples per slot and six primary frames with MATH unavailable. The exact predicted count is part of the approved fixture. Count or timebase uncertainty remains visible as failed conversion or mismatch. Preserve a before-state record of panel and acquisition behavior, signal routing, scale/probe/timebase, and stimulus/reference details.

For the probe-compensator option, a single common-low reference with a measured 2:1 attenuation path on CH2 gives nominal CH1/CH2 inputs of approximately 5/2.5 Vpp and a period of 1 ms. For a suitable confirmed setup, examples of broad, nonoverlapping compatibility windows are CH1 3.5–6.5 Vpp, CH2 1.5–3.25 Vpp, and both periods 0.8–1.25 ms. At 500 µs/div, a 16-division window spans 8 ms. Set the actual limits, expected count and probe factors from the bench evidence and obtain approval for that fixture. These illustrative limits are not manufacturer tolerances or measured values for the retained unit.

## Exact supervised sequence

1. Confirm external authorization, fixture digest and destination, clean exact source, physical unit and firmware, exclusive ownership, settled history, adequate local evidence storage and no unresolved marker. Suspend measurement polling and other clients. Inspect initial panel operation. Type the run-specific console confirmation.
2. The adapter acquires the existing cooperative lease, checks one healthy local WinUSB target, persists the interruption marker and claims the validated interface. It observes **12 seconds of strict receive-only silence**. Any bytes, including exact S92, block the write.
3. Attempt exactly one **`53 04 00 02 01 04 5E`** write. Require all seven bytes returned. From write completion, observe one absolute **12-second** window with at most **32,768 RX bytes**, **256 framed responses/status frames**, and **10,000 samples per slot**. Use aligned reads of at most **16,384 bytes** with timeouts capped at **250 ms** and reduced for remaining time. Millisecond timeout quantization allows at most 1 ms for a final no-data poll. A late data transfer, larger overrun, cancellation, transport error or exhausted bounds fails the observation.
4. Preserve raw bytes through the same approved window after classification fails if reads and storage remain healthy and bounds allow. Classification stops at first lost boundary and performs no scan-ahead. A framing failure and a healthy raw-capture transport can coexist. Preserve any exact S92 already classified with original timing and offsets. It neither extends the deadline nor establishes readiness. Zero retries and zero extra writes.
5. Release the interface, then decode the entire observation using ACQ-10A. Require CH1 and CH2 data at the declared counts, explicit unavailable MATH, and exactly one selector-4 terminal. Compare voltage peak-to-peak and rising-crossing periods to the disjoint fixture limits. Check both panel traces are still advancing and configuration is unchanged. When prompted, type `CONTINUING` only if both observations are confirmed without sending another command. Finalize through the lease and admission guard described below.

## Evidence and outcomes

Use a fresh local evidence directory on the same volume as the Windows interruption state directory. It contains `fixture.json`, `journal.jsonl`, startup and response raw files, and the exact attempted request bytes when applicable. `acquisition.json` uses the ACQ-10A v1 contract when the full response window reached decoding. `native_manifest.json` is the final supervised acceptance record.

ACQ-10C1 installs `rear-usb-native-finalizing.json` beside the interruption marker while the cooperative lease is held. It verifies the current marker's file identity and exact session bytes, flushes and hashes the evidence, and syncs a `native_manifest.pending.json` snapshot before retiring the verified marker by file handle on Windows. After confirmed lease release, the shared lease gate still rejects new sessions while the finalization guard exists. A same-volume rename publishes the synced guard file as `native_manifest.json` and clears the admission gate in one step. Only the final manifest at its evidence path can establish aggregate success. A pending manifest or the guard can contain interim or staged fields and cannot establish finalization.

An unresolved finalization retains the guard or original marker for Architecture review. Do not remove either file by hand. The shared lease gate in this candidate enforces guard exclusion for callers using the same Windows account and state directory. Keep older clients that predate the guard and clients using other Windows accounts excluded throughout the session and any reconciliation.

The final manifest records authorization, identity provenance and source SHA; bytes, timing and frame classification; separate transport, wire integrity and primary outcome; slot or voltage-supported channel attribution; exact captured S92; engineering conversion and independent voltage/period checks; operator continuation; host releases; marker disposition; errors; and hashes of the evidence artifacts. Attach raw failure artifacts and logs to the private review.

- `PASS` requires successful transport, complete waveform, both independently plausible channel measurements, confirmed scope continuation, successful cleanup and marker clearance.
- `PASS_WITH_STATUS` requires the same evidence and includes exact S92 within the bounded response. Both successful outcomes exit 0.
- `BLOCKED` records a gate failure before attempting the command, with exit 3. An unresolved startup observation can retain its new marker.
- `FAIL` records failed transfer, validation, continuation or cleanup, with exit 4. A malformed or status-only response, analog unavailability, unexpected MATH, wrong counts, or unknown status cannot pass. Device-generation identity, same-shot coherence and unattended reliability remain unproven.

## Operator-controlled recovery

1. After any failed observation, stop issuing instrument commands and keep monitoring suspended. Preserve the raw capture, authorization, timestamps and marker. Let the host release the interface and lease normally where possible. If it cannot, record which resources are uncertain and stop automated clients. Do not clear the marker by hand.
2. From the panel, observe responsiveness, RUN/STOP and trace movement without changing settings. Check for an unresolved Save/Recall or storage operation and any hazard to attached equipment. If normal, record the evidence and still reconcile the failed operation with Architecture before another active session.
3. If the scope has stalled, follow the separately accepted recovery authority. Have the operator bring attached test equipment to a safe state and preserve any available evidence. Use the least disruptive approved front-panel action only after USB ownership and operation state are understood. If the panel remains unresponsive and the recovery authority permits it, a conventional operator power cycle is a last resort. Preserve the before/after observations. Avoid resets, forced firmware writes or intentional fault injection.
4. Verify a normal boot and retained firmware, responsive panel, normal running acquisition on the fixture, and unchanged recorded configuration. Escalate persistent corruption, symptoms of damage, or inability to restore normal operation. Reconcile intent, action history and the interruption marker under the existing Architecture review and lease process before any new active session.
