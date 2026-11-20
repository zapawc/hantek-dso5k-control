# CLI contract

| Command | Status |
| --- | --- |
| `hantek --version` | Software version |
| `hantek qualify readonly --output DIR --confirm-single-client --confirm-settled-history` | Hardware-qualified for the tested Windows batch and qualitative screens. Requires separate hardware approval. DIR must be absent. |
| `hantek offline settings PATH` | Read and decode a saved 208-byte settings payload |
| `hantek offline file-replay RX --filename NAME --expected-bytes N --expected-sha256 HEX --output PATH` | Validate a saved native CSV response window and extract the exact bytes. See [DEV-05 replay contract](protocol/NATIVE_CSV_RETRIEVAL.md#cli-and-structured-output). |
| `hantek files retrieve --instrument-path PATH --reference REF --output FILE --evidence-dir DIR` plus operator confirmations | DEV-07 draft CLI for the one qualified native CSV profile. See the [full contract and example](protocol/NATIVE_CSV_CLI.md). The installed CLI passed a bounded live CH1 retrieval on 2026-11-20 UTC; other device operations still require separate authorization. |
| `hantek offline waveform PATH --output CSV --sample-interval SECONDS --volts-per-division V --probe-factor FACTOR --vertical-position-counts COUNTS` | Parse an existing complete CH1 stream and convert relative coordinates with caller-supplied parameters |
| `hantek device identify --json`, `hantek settings read --json`, `hantek screenshot --output FILE --metadata FILE` | Reserved. Use the qualified batch entry point for settings and screens after approval. |
| `hantek clock read --json`, `hantek clock compare --json`, `hantek clock sync --confirm` | Reserved. See the clock plan. |
| `hantek capture single --channels 1,2 --output DIR` | Unavailable pending separate acquisition approval gates |

Structured results are emitted as one JSON object on stdout. Errors and CLI diagnostics go to stderr. Help and version use human-readable text. Exit 0 means successful offline operation, complete strict qualification or verified file publication. Exit 2 means command-line usage error. Exit 3 means blocked before a claim attempt. Exit 4 means claim, protocol, settings comparison, cleanup, publication or host evidence failure. Exit 5 means completed primary replies with classified status or graphics aggregate discrepancy in the qualification batch. Exit 6 means reserved/unavailable.

Read `capture_manifest.json` and the [v2 schema](../schemas/capture-manifest-v2.schema.json) for automation. A null field means unknown. Step results distinguish transport, primary framing and graphic aggregate. Raw transfers, wire details and diagnostic journals are private implementation evidence. A device-clock value, trigger origin or sample-generation identity is never inferred from host UTC. Software version and source commit are separate. The source commit is populated only by installed VCS metadata when available. Treat an artifact digest as the identity of a wheel installation.

The read-only batch allows four fixed requests, a 12 s receive-only startup, 12 s response per request, a 550 ms quiet guard between requests, and zero retries. A single exact post-reply transfer-status frame is recorded and permits continuation within the reviewed windows. Startup, early, duplicate, guard, malformed and unknown traffic block further writes. Both preview artifacts remain labeled independently of aggregate and session status. No background USB access or hardware fallback occurs. [Capability evidence](capabilities/CURRENT.md)

Settings results and comparison items use [neutral offset IDs](SETTINGS_V2.md). Successful settings output and per-run settings JSON follow the [settings v2 schema](../schemas/settings-v2.schema.json).
