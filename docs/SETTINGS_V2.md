# Neutral settings identifiers and schema v2

The device still supplies the same 208 bytes. The host exposes the same 119 field boundaries and decodes little-endian integers with the established signedness for multi-byte values. The public identifier is `field_NNN`, where `NNN` is the three-digit decimal start offset. Field keys follow wire order on output. Consumers should use the key or the numeric `offset` to identify a field without relying on JSON object order.

| Offset and public key | Observed interpretation |
| --- | --- |
| 0 and 10 · `field_000`, `field_010` | CH1 and CH2 display flags |
| 2 and 12 · `field_002`, `field_012` | Channel coupling candidates. DC, AC and GND labels retain their previous evidence limits. |
| 20 · `field_020` | Device-reported trigger state. It is volatile and cannot prove acquisition completion. |
| 22 · `field_022` | Configured trigger source. Labels are candidate interpretations. |
| 27 · `field_027` | Eight-byte trigger-frequency field. Volatile. |
| 179 · `field_179` | Display format byte. |
| 189–204 | Eight source/item pairs. The private qualification journal's context summary is unchanged. |

Other decoded fields retain offsets and raw values. Their semantic meaning should not be invented from an identifier. A setting object has `offset`, `hex`, `raw_integer`, and `candidate_label` where the prior decoder supplied one. Concatenating the decoded hex slices by offset reconstructs the exact payload.

## Consumer transition

The pre-release local v1 field vocabulary is replaced before the first release. There are no runtime aliases or compatibility maps in the public candidate. A consumer that previously read v1 should intentionally change its pin and update selectors to `field_NNN` using its own previously recorded numeric offsets, then run its contract tests. Keep v1 archives local. Re-decode an existing binary payload with `hantek offline settings` when v2 evidence is needed.

Successful settings output and the baseline/final settings JSON artifacts use schema 2.0. The qualification manifest also uses schema 2.0 because `settings_comparison.*[].field` now uses neutral identifiers. The comparison categories, ranges and raw initial/final values are unchanged. Offline waveform results and generic error or unavailable envelopes still use 1.0. Transport, pacing, ownership, raw files and exit-code meanings are unchanged. In particular exit 5 is completed primary replies with anomalies and strict session FAIL.

[Settings schema v2](../schemas/settings-v2.schema.json) · [Capture manifest schema v2](../schemas/capture-manifest-v2.schema.json) · [CLI contract](CLI_CONTRACT.md)
