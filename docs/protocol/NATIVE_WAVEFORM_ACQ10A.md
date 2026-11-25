# Native waveform foundation (ACQ-10A)

ACQ-10A provides two offline Python functions. `hantek_dso5k.native_waveform.decode_native_waveform` validates one complete recorded response window. `hantek_dso5k.acquisition.build_native_result` builds a version 1 host observation from that window. Pass an iterable of captured byte transfers and the expected request selector to either function. The existing offline CH1 command, published schemas and live command profiles retain their existing behavior.

## Prior art and original firmware

The retained 3.40.0 (151009.0) application trace distinguishes the first body byte `00`, which invokes the transfer-state handler, from body `01 <source>`, which dispatches a waveform read. Source `0` in the second byte is CH1. The source-4 branch runs the shared waveform sender for internal slots 0, 1 and 2 in order, then calls the terminal builder with source 4 even when a slot sender returns a failure. The sender and reply builders establish unsigned little-endian count serialization with three count bytes on the wire, the request selector in data frames, and the internal slot in unavailable replies. The single-channel branches omit the terminal when the sender reports unavailable. This is static evidence for the retained application.

The same sender borrows display/acquisition storage and can clip 16-bit samples in place. The provider releases its mutex before the sender acquires it, and the sender holds the producer mutex during part of the USB write sequence. The source-4 dispatch performs three independent provider calls. The trace leaves producer progress, cancellation/backpressure behavior, a cross-channel latch, and delivery after Stop unresolved. A terminal can follow incomplete or unavailable slots and requires independent byte and coverage validation.

The pinned Koos report describes source 4, display-width scaling and running versus stopped behavior on the author's 3.40.0 (210225) setup. The author reports same-acquisition data on that setup. Retained-firmware replay in this project has no selector-4 hardware specimen. This change reuses the project's framing, exact S92 constant and pure CH1 conversion arithmetic. It draws on the external protocol description without incorporating source code or settings tables. [Pinned protocol and limits](https://github.com/kooscode/hantek-dso5000p-linux/blob/b0facf139fb1b3da0a8c3ad0fa77a9eda76e1c46/protocol/PROTOCOL.md#L72-L139) · [Prior art](../prior-art/INDEX.md) · [Qualification gates](QUALIFICATION_GATES.md) · [ADR-004](../adr/0004-qualified-baseline-and-selective-reuse.md)

## Offline response contract

| S82 body | Required interpretation |
| --- | --- |
| `00 <u24 LE count>` | Start the next slot with a positive declared number of signed display codes. |
| `01 <selector> <one or more codes>` | Append samples to the started slot, echoing the request selector. Finish the slot at exactly the declared count. |
| `03 <slot>` | Mark the next slot unavailable. Require the matching slot ID and no in-progress sample block. The reason for absence remains unknown. |
| `02 <selector>` | End the primary response after all slots are accounted for. Require one terminal for source 4 and for successful source 0/1 data. A sole unavailable record completes a source 0/1 reply without a terminal. |

Selector 4 requires CH1, CH2 and MATH in that order, each with data or an explicit unavailable marker. Selector 0 covers CH1 and selector 1 covers CH2. Available blocks carry no channel ID. Their attribution follows slot order, so a swap or replacement of otherwise structurally identical anonymous available blocks cannot be detected on the wire.

The decoder joins captured fragments within a 256 KiB offline byte budget, validates lengths and checksums, and consumes the entire window without resynchronizing. It bounds each data frame to 10,000 samples and each slot to 40,000 samples. An exact `53 04 00 92 01 00 EA` at a frame boundary is recorded by raw byte offset before, during or after the primary response. It supplies no coverage, completion, or readiness evidence. Unknown status shapes and other trailing data fail the observation. The offline classifier makes no decision about acceptance of an S92 during a live guard.

## Host acquisition result

The [v1 JSON Schema](../../schemas/acquisition-result-v1.schema.json) describes the host observation. `source_method` is currently `native_s02_s82`. A later CSV adapter can align to the common fields with its own source-specific validation. Existing S10 results and schemas remain the qualified file-transfer contract.

- `host_run_id` identifies the host observation. `device_generation_id` remains null, `data_freshness` is unknown and `acquisition_coherence` is unproven. `channel_association` reports only S82 slot attribution.
- Supply instrument model, firmware and their evidence source in `instrument`. Unknown values are null. A claimed identity requires provenance.
- Supply `timing.clock_context` identifying the host clock instance, its monotonic domain and the UTC source. `request_write`, `receive_first` and `receive_end` record host write completion, first receive completion and observation end. Each event accepts an ISO 8601 UTC timestamp and monotonic seconds, with null allowed for each unavailable coordinate or the entire event. Ordering uses values from the same monotonic domain. These are host observations.
- Supply the JSON configuration snapshot with the observation and the applicable per-channel scaling metadata below. Artifact descriptors contain `role`, `locator`, `bytes` and lowercase `sha256`. Callers compute and verify these against their evidence. The builder performs no file I/O.
- Wire verification means framing, checksums, exact count, coverage and terminal passed the selected profile. A failed observation carries an error and no decoded channel arrays. Preserve the original capture independently for inspection. A reported unavailable slot in a valid response is distinct from a failed observation.

### Engineering units

Pass metadata for each relevant analog channel under `configuration_snapshot["channel_scaling"]`, keyed by `CH1` or `CH2`. The caller must independently establish metadata for the observed record. The builder requires `verified: true` and a descriptive `provenance` with the following inputs.

| Field | Meaning |
| --- | --- |
| `volts_per_division` | Scope-input V/div before external probe correction. |
| `probe_attenuation` | Positive factor applied once, such as 10 for a verified 10× probe. |
| `vertical_offset_counts` | Signed position offset in display-code units. |
| `seconds_per_division` | Verified timebase in seconds per division. |
| `display_divisions` or `display_span_s` | Verified viewport width in divisions or equivalent full sampled span in seconds. Supply viewport/menu evidence in the snapshot wherever it affects width. |
| `sample_count` | Verified count for that slot, required to match the wire declaration. |
| `sample_interval_s` | Explicit positive interval, required to agree with full span divided by sample count within relative tolerance `1e-9`. |
| `time_origin_s`, `time_reference` | Time of sample zero and name of the independently established reference. |

Voltage uses `(display_code - vertical_offset_counts) / 25 × volts_per_division × probe_attenuation`. The time for sample index `i` is `time_origin_s + i × sample_interval_s`. When specified, width in divisions must agree with any supplied span after multiplying by seconds per division. The full span convention is `N × dt`. The first-to-last sample interval is `(N - 1) × dt`. No viewport, attenuation, scale or trigger origin is inferred from a settings index or an unverified device label. Raw signed display codes remain distinct from volts. Missing, unverified, invalid or inconsistent inputs produce a raw-only result with a reason. MATH data remain raw-only pending a range and units profile. Conversion alone leaves measurement validity unverified, including possible clipping and calibration error. [Legacy conversion](../../src/hantek_dso5k/_waveform.py) · [External timing and voltage model](https://github.com/kooscode/hantek-dso5000p-linux/blob/b0facf139fb1b3da0a8c3ad0fa77a9eda76e1c46/protocol/PROTOCOL.md#L92-L139)

## ACQ-10H proposal — HOLD

Before authorizing a session, finish the retained-firmware review of both providers, their borrowed-buffer reuse and clipping exposure, and device-side TTY/gadget backpressure, cancellation and mutex progress. Establish a safe continuous mode with a bounded or explicitly accepted disposable-record effect and a reviewed cleanup path. A host deadline does not bound the device-side writer. Pin a separately reviewed live implementation and obtain written approval for the one request, any volatile effect and the operator setup. This document authorizes no device operation.

1. Use the same DSO5102P and retained firmware 3.40.0 (151009.0). Use an existing calibrated dual-output source, or one calibrated source with a characterized passive split/attenuation path to give two distinct amplitudes and a common edge. Use suitable probes and safe grounding/levels. Use an existing independent instrument, generator timebase or external event witness to establish amplitude, period and edge correspondence with recorded uncertainty. Hold if available bench equipment cannot establish the references. No equipment purchase is assumed.
2. Have the operator set up ordinary running acquisition with a disposable display record, CH1 and CH2 enabled and MATH disabled. Select and record a known timebase, observed menu/viewport, memory depth, scales, probes and offsets, and record the expected sample count or a justified range. Use unsaturated signals and document before/after panel, waveform and acquisition-state evidence. Use operator observations for this assessment. Any S01/S20 snapshots, S12 activity, panel actions outside the approved setup, or further commands need their own review and authorization.
3. Use the existing Windows identity and ownership checks, cooperative lease, validated WinUSB interface and interruption-marker handling. Require settled command history, no unresolved marker, and 12 seconds of strict receive-only silence. Stop on any traffic or transport anomaly. For the sole write, the reviewed candidate bytes are **`53 04 00 02 01 04 5E`**. Require the full seven-byte write receipt, zero retries and no recovery mutation.
4. Proposed host observation bounds are 12 seconds from completed write, 32,768 raw RX bytes, 256 frames, and 10,000 samples per slot with aligned WinUSB reads. Keep polling through the bounded observation once a valid terminal arrives, while treating any unexpected bytes, missing coverage, checksum/count error, partial frame, timeout without completion or transport fault as a failed observation. Record exact S92 separately with its timing. Use conservative strict-session acceptance for the first gate, requiring zero statuses for a full PASS. Report any complete primary separately from that strict outcome. The tighter live plan requires separate review against the selected viewport and proven device-side progress.
5. Require complete CH1 and CH2, explicit MATH unavailability, a single `02 04` terminal and exact counts. Compare both waveforms to independently specified voltage, period and expected relative edge position within predeclared instrument/fixture uncertainty and the sample interval. Record observed before/after state, exact request and received transfers, clock context, evidence hashes, and normal interface and lease cleanup. Preserve failure evidence and leave ambiguous history marked. A passing one-request result applies to that running setup. Same-shot identity, stopped delivery and unattended reliability require separate evidence.
