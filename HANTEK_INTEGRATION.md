# Hantek integration for consumer projects

Copy this guide into a consumer project as a reference. Use the public canonical Hantek GitHub repository for shared source, versioned schemas, capability status and Issues. Consumer projects own their procedures, acceptance thresholds, analysis and presentation. Use the `hantek` CLI as a subprocess. Keep USB protocol code and firmware research in the Hantek project.

## Install an approved pin

Record the approved source commit or release asset SHA-256, CLI version, and supported schema version in the consumer lock or integration record. Use the full Git SHA for a source pin. Build from that revision in a reviewed build environment and lock the resulting artifacts. Treat a tag as a label whose mapping must be verified. Never follow an updated branch automatically.

For a wheel-based Windows install, obtain the reviewed Hantek and backend wheels and a requirements file containing exact versions and SHA-256 hashes for the target Python/Windows combination. Add the Hantek wheel as an exact version and hash, and do likewise for its reviewed backend dependencies. An offline-only environment needs only the Hantek wheel. After verifying the release provenance, install into a dedicated environment.

~~~powershell
python -m venv .hantek-env
& .\.hantek-env\Scripts\python.exe -m pip install --no-index --find-links .\approved-wheels --require-hashes -r .\hantek-preview.lock
& .\.hantek-env\Scripts\hantek.exe --version
~~~

The lock and wheelhouse names above are illustrative. A separately approved release must supply trusted versions and digests. An installed local wheel may report a null `source_commit`. In that case use the wheel digest and the release's recorded source commit as provenance.

## Invoke and interpret the CLI

Use argument arrays, capture stdout and stderr separately, and retain the process exit code. Offline examples use existing files.

~~~powershell
& .\.hantek-env\Scripts\hantek.exe offline settings .\saved-settings.bin
& .\.hantek-env\Scripts\hantek.exe offline waveform .\saved-ch1-s82.bin --output .\converted.csv --sample-interval 0.000001 --volts-per-division 1 --probe-factor 1 --vertical-position-counts 0
~~~

Waveform scale and interval in that example are illustrative caller inputs. Conversion uses a historical CH1 stream and outputs record-relative time. It cannot establish the physical scale, fresh acquisition, a common trigger origin, or CH1/CH2 coherence.

For a separately authorized Windows hardware session only, use `hantek qualify readonly --output DIR --confirm-single-client --confirm-settled-history` with an absent output directory. The two confirmation flags must reflect actual operator ownership and settled history. The qualified batch reads baseline settings, two screens, and final settings. It performs a leased ownership check and a receive-only preflight. Stop on a gate failure or ambiguous traffic. A subsequent attempt requires review.

| Exit | Consumer handling |
| --- | --- |
| 0 | Strict read-only success or successful offline operation. Check the structured result. |
| 2 | CLI usage error. Correct the invocation. |
| 3 | Blocked before claim attempt. Resolve ownership or the recorded gate. |
| 4 | Claim, incomplete outcome, protocol, comparison, cleanup or evidence failure. Preserve partial evidence and stop. |
| 5 | Primary sequence completed with classified status or graphics aggregate discrepancy. Read each step and record strict session FAIL. Use the previews only for qualitative inspection. A downstream procedure requiring strict integrity fails this result. |
| 6 | Reserved or unavailable command. Keep the capability out of automated workflows. |

Read `schema_version` and validate qualification results against `schemas/capture-manifest-v2.schema.json` from the installed pin's source release. Read `sequence_complete`, `strict_session`, `steps`, `settings_comparison`, and artifact digests before consuming captures. The versioned manifest describes primary replies and integrity separately. Null means unavailable or unverified. Raw packets and diagnostic journals are private implementation evidence.

The preview qualifies Windows ownership/discovery, read-only settings, qualitative screens and structured evidence. Trigger control, SINGLE, live waveform/coherence, acquisition completion, power-startup capture, settings restoration and RTC synchronization remain unqualified.

## File an actionable shared issue

Choose the closest issue form, link the originating project issue, provide the exact pin and instrument context, and include expected/actual behavior, precise CLI arguments, safe reproduction, sanitized evidence, safety implications and acceptance criteria. Leave raw captures, unit identifiers, proprietary data and credentials local. The issue requests engineering work. It does not approve hardware access.

**CYD-RF-Diagnostic example · NOT YET IMPLEMENTED**

Request synchronized CH1/CH2 single-shot waveform acquisition with an independently verifiable trigger and common sample time origin. The example targets an approved preview pin, manifest schema 2.0, a DSO5102P with historical firmware baseline `3.40.0 (151009.0)`, and Windows WinUSB. The preview supplies read-only settings and screens. No SINGLE or live waveform command was executed. The shared acceptance criteria are a bounded arm/wait/completion test, shared acquisition identity, channel geometry and scale, a verified common trigger index or time origin, and independently reviewed restoration. CYD owns the DUT power/startup procedure and acceptance thresholds. This request authorizes no hardware or power actions.

## Deliberately upgrade

Open a consumer change with old and new pins and a release/issue link. Verify new source or artifact digests, test CLI and schema compatibility including exit 5, and run consumer acceptance under its own authorization. Preserve the previous reviewed pin for rollback. Merge the dependency update only after review.

Consumers migrating from the unpublished local preview should update selectors to neutral decimal-offset IDs and use schema 2.0 for settings and capture manifests. See the [settings v2 migration guide](docs/SETTINGS_V2.md).
