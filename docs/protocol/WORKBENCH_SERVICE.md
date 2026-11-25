# Responsive Digital Workbench instrument service

The persistent Hantek process serves both `/v1` and `/dw/v0.1` on IPv4 loopback. Both share one WinUSB worker, one cooperative lease, one operation queue, cached settings and telemetry, and the existing recovery controls. Use a clean reviewed checkout on Windows with the retained DSO5102P firmware 3.40.0 (151009.0) and the existing WinUSB driver. Installation and offline tests perform no instrument I/O. [Original service and recovery policy](PERSISTENT_SERVICE.md) · [ADR-006](../adr/0006-workbench-acquisition-development.md)

## Start the service

```powershell
python -m pip install -e '.[winusb]'
$revision = git rev-parse HEAD
python -B -m hantek_dso5k.service --live --source-commit $revision --evidence 'C:\private\new-run' --instrument-id bench-scope --telemetry-source both --measurements frequency,vpp,duty_cycle --timing validated-quiet --settings-every 5 --physical-probes 10,1
```

Use a fresh private evidence directory on the interruption-state volume. Reuse the same locally configured instrument ID across launches. Each launch gets a new instance ID and token. The optional physical-probes flag attests to the retained fixture routing and attenuation. Set it only when the physical switches are known. The service does not change acquisition state, software attenuation or measurement slots on startup. Starting Run or arming SINGLE can replace a stopped acquisition and requires explicit consent in that state.

The conservative default waits the full bounded observation windows. The optional `validated-quiet` mode can complete S81, S22 and native selector-4 reads after full framing and at least 550 ms of quiet, within the original deadlines. Receiving an exact S92 restarts quiet if the complete primary reply remains valid. Unknown, partial, duplicate or late replies prevent a settled outcome. S92 provides no readiness guarantee. Writes, file retrieval, startup and other guards retain their original windows. Commands are never retried after ambiguous history.

The default S22 rotation accepts `--channels`, `--measurements`, `--poll-interval`, `--priority` and optionally `--configured-slots-only`. The latter explicitly skips unconfigured source/item pairs, even where the instrument might have answered. The software does not assume that assignment alone populates an S22 cache. The `waveform` or `both` telemetry mode uses `--waveform-every` to pace native samples and refreshes S81 independently. The service marks cached observations stale during capture and until each series has refreshed. Settings changed outside the service may be detected only at the next S81 observation. Keep the supervised panel settings fixed during monitoring.

S22 values are display-cache observations with unknown device freshness. Formatted zero is ambiguous and `?` is unavailable. The temporary `s22-probe --configure-slots` comparison writes and restores a fixed CH2 Frequency and Vpp table on the reviewed running fixture. The slot comparison can be combined with `--configure-fixture` to use verified scale and probe settings during the comparison. A verified slot change does not guarantee a populated cache.

Waveform measurements use the verified horizontal viewport and voltage mapping. The API carries the host acquisition identifier, host write/receive timestamps, settings identity, sample count, spacing and coverage. Voltage telemetry requires matching explicitly declared physical and configured probe attenuation. Clipping and unsupported conversion suppress engineering values. Frequency, period and duty cycle require usable edges. On-demand snapshots preserve raw display samples when engineering conversion is unavailable. Host timing establishes neither the exact device acquisition time nor same-shot coherence.

## Monitor and capture

```powershell
$client = 'C:\private\new-run\client.json'
python -B -m hantek_dso5k.service_monitor $client
python -B -m hantek_dso5k.service_client $client measurements
```

The dashboard displays both channels, source, units, host observation age, staleness, availability, update rate and active job. Enter `snapshot`, `single auto yes`, `run yes`, `stop`, `plot`, `shutdown` or `quit`. `--once` prints a single cached frame. The client verifies waveform artifact size and SHA-256 before plotting. Raw-code plots and configured-scale plots carry distinct labels.

Use the same bearer credential from the protected discovery file for both interfaces. The Workbench client uses the authenticated endpoints below.

| Request | Result |
| --- | --- |
| GET /dw/v0.1/identity | Configured local instrument identity, launch identity, profile, model and versions |
| GET /dw/v0.1/status, /capabilities, /settings, /telemetry | Cached state, channel IDs, series, job schemas and configuration identity |
| POST /dw/v0.1/jobs | 202 and Location on admission. Submit kind, arguments and a bounded timeout. |
| GET /dw/v0.1/jobs/{id} | Immutable terminal result, separate acquisition and restoration outcomes, and artifact descriptors |
| GET /dw/v0.1/jobs/{id}/artifacts/{artifact_id} | Verified bytes with the media type and schema from the descriptor |
| POST /dw/v0.1/shutdown | 202 after admission closes. Physical release is confirmed by process exit and the private manifest. |

```json
{"kind":"hantek.triggered","arguments":{"path":"auto","allow_overwrite":true},"timeout_s":90}
```

A cached preflight can reject incompatible settings or a stopped record. The worker rechecks fresh settings before device changes. Triggered jobs use the retained dual-channel 4K fixture profile. The `auto` route falls back only after a settled native response reports an unavailable analog record. Choose the result's selected method and named artifacts. Frozen fallback keeps the native-unavailable waveform response for evidence alongside the CSVs. Cancellation and external-trigger arm readiness are unsupported. Execution budgets are checked at command boundaries. An in-flight USB operation is never interrupted to meet a host deadline. Verified recovery can extend past that deadline. Unresolved state faults the worker and preserves evidence.

The HTTP service rejects invalid Host and browser-origin headers, authenticates every supported route including artifacts, caps POST sizes and client concurrency, and keeps credentials out of its logs. The Windows discovery file grants access to the current account. The client uses loopback, bypasses proxies and refuses redirects. Processes running with equivalent user privileges share that trust boundary.

## Evidence and recovery

Operational mode rotates four rolling batches of sixteen completed or attempted monitor turns. Startup, acquisition jobs, their raw exchanges, acquisition results, faults and finalization remain outside that rotation. `--detailed-monitor-log` keeps every monitoring exchange for a targeted diagnostic session and grows until shutdown. Job lookup retains the most recent 64 records in memory. Artifact files remain in their private directory. Move or delete old completed evidence only after confirming clean finalization and according to the local retention policy. Preserve every run with an unresolved marker or finalization guard. The existing ADR-006 reconciliation process governs restart after faults. Private captures and discovery credentials belong outside Git.
