# DEV-05 native CSV retrieval investigation

This report records DEV-05's historical decision. The [DEV-07 CLI contract](NATIVE_CSV_CLI.md) documents the subsequent DEV-06A qualification and the narrower packaged integration.

**Recommendation.** Keep native SINGLE and Save/Recall operator-controlled. Qualify retrieval of one finished front-drive CSV before implementing a live CLI operation. Keep S02 on HOLD. Two historical S10 replies reconstructed correctly; the observed byte streams do not establish clean ownership, a quiet full response window, frozen-record retention during rear-USB retrieval, or behavior on disconnect.

## S10 evidence matrix

ARM offsets refer to the original 2015 application. Historical evidence remains in the development archive.

| Question | Evidence | Confidence and limit |
| --- | --- | --- |
| Request | Dispatcher `0xdebdc` calls `0xdcb98`. The handler subtracts three from the LE16 length and copies from byte offset 5 into a cleared 32-byte buffer. | Direct ARM. Use `53 LENlo LENhi 10 00 <ASCII absolute path> SUM8`; LEN is path bytes plus three. SUM8 is the sum of all preceding frame bytes modulo 256. Omit a wire NUL and cap the path at 31 bytes to retain the zero terminator. |
| Source and access | Existence helper `0x3fc50` and sender `0xdcc50` each use `r+b`; the second open feeds the seek/size helper `0x3fbe8`. | Direct ARM. Opens the supplied path independently of front-panel source/menu selection. A writable mounted file is required. A race between the two opens could invalidate the second open before use. |
| Data | Sender loop `0xdcd30–0xdcdde` works in 10,208-byte chunks and emits a last tail; `0xdc55c` can frame an empty tail. | Direct ARM and two historical nine-data-frame CSV transfers. Data frames are `S 90 01 <file bytes>`; frames contain no index, offset, filename or transaction ID. Require the tail even if empty for an exact multiple. |
| Completion | The sender emits `S 90 02 <sum of file bytes mod 256>` before closing the file. | Direct ARM and historical terminal sums. Validate frame lengths and outer checksum independently, then exact aggregate count, terminal sum and the independent expected SHA-256. Terminal arrival does not establish subsequent firmware cleanup or storage durability. |
| Error handling | `0xdcd60` and `0xdcd70` do not use short-read results to bound published bytes; USB writer chain `0xdbe88 → 0x41ad8` does not propagate the underlying write outcome through the file sender. | Direct ARM. Missing/inaccessible paths can time out. Do not assume a reliable error frame or clean cancellation. A prior multi-megabyte attempt stalled; it does not define a failure threshold. |
| State effects | S10 opens, seeks, reads and closes. The file path has USB/activity bookkeeping. No explicit file-content write, acquisition-control or persistent settings update was found in the traced path. | Bounded static finding. FAT mount metadata, UI activity and asynchronous S92 may change; historical transcripts include pre-file status traffic. Acquisition/display retention must be observed during the pilot. |

For response frames the generic format is `53 LENlo LENhi 90 BODY SUM8`. LEN includes opcode and checksum, so data frames occupy payload byte count plus six wire bytes. The terminal occupies seven wire bytes. Timeouts or disconnects can leave completion ambiguous; no resume, flush, reset or automatic retry is proposed.

## Storage and selection

| Candidate | Assessment |
| --- | --- |
| Closed Save/Recall export at `/mnt/udisk/WaveData<number>.csv` | Preferred. Historical rear-USB transcripts returned exact files from this mount. Prior evidence does not attest to the present mount, exact file presence or writer state. Require evidence for the specific pilot source and an independent reference size/hash. |
| Internal `/dsocsv.tmp` | Native export staging on internal storage. Lifecycle may include open writes, replacement and cleanup. Excluded from qualification. The pilot does not export or run Save/Recall. |
| File selected on the front panel | No direct link to S10 found. S10 takes a literal absolute path; menu selection is not a locator. |
| File enumeration, other paths or command families | No qualified ordinary directory-list operation in the dispatch inventory. Do not issue speculative listing, shell, file-write, directory traversal or C-family requests. |

The pilot restricts filename input to exact ASCII `WaveData[0-9]{1,8}.csv`, with no separators or pattern expansion. Numeric identity is not proof of channel; maintain operator export attribution outside the wire protocol.

## CLI and structured output

The additive, hardware-free command in this branch is `hantek offline file-replay RX --filename NAME --expected-bytes N --expected-sha256 HEX --output PATH`. RX is the complete ordered wire response from an independently delimited window. The filename is a reference label, because S90 does not echo it. The parser accepts 1–98,304 file bytes and at most 102,400 RX bytes, fails on partial or unsolicited traffic including S92, and publishes original bytes without overwriting a destination. Preserve the source wire evidence. Replay does not validate timing or USB boundaries.

Success uses the existing versioned CLI envelope and adds `status`, `filename_reference`, `output_path`, `rx_bytes`, `expected_bytes`, `received_bytes`, `data_frames`, `terminal_sum8` and `sha256`. Exit 0 means exact replay; protocol/reference/collision errors return 4 with a stable `error` and human-readable `detail` in the envelope. Host filesystem errors use the shared error envelope. Existing settings and capture schemas remain at 2.0.

Proposed live interface after qualification (unimplemented) is `hantek files retrieve NAME --expected-bytes N --expected-sha256 HEX --output FILE --evidence-dir DIR --confirm-single-client --confirm-settled-history`. Discover and identify the Windows device/interface through the existing ownership layer. No automatic baseline queries. Require a unique destination, closed-file attestation and an independent size/hash. The prospective 1.0 retrieval manifest should include instrument and endpoints, expected identity/provenance, command and TX count, timing/transfers, frame and terminal validation, expected/actual byte counts and digests, session status, output path or null, and error phase/reason. Use 0 for strict success, 2 for CLI usage, 3 for a pre-claim block, and 4 for any opened-session, protocol, integrity, destination or cleanup failure. The tested v0.1.0 commands and schemas are unchanged.

See the [bounded qualification plan](NATIVE_CSV_QUALIFICATION.md).
