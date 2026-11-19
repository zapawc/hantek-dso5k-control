# HANTEK-DEV-01 — Protocol evidence matrix

**Evidence grades.** Observed refers to existing device records or reports, static refers to installed 2015 executable analysis, and community describes external claims that require target-firmware verification. Firmware offsets below are code locations from the 2015 image. Raw captures and proprietary source material are held locally. The names FW-CAP and HANTEK-GIT identify prior qualification work; they are not public downloads.

| Capability | Best evidence and protocol | Confidence and qualification limit |
| --- | --- | --- |
| Read settings, CH1/CH2 display | Observed S01 → one S81 with 208-byte payload. Static 0xdd628 → 0xd7774 → 0xd6634. Offsets 0 and 10 are display flags, channel groups 0–9/10–19. | High for payload and display flags. Display enabled alone cannot prove a fresh captured channel. |
| Acquisition state/readiness | Static 0xd0954 binds state +0x8a4, 0x3c770 writes it, 0x12394 consumes it for UI. Lookup 0xd6b14 maps internal 0–6 to wire 0,5,2,3,4,6,1. Observed wire 2 and 3. | High for serialization. Community 0 Stop, 1 Ready, 2 Auto, 3 Triggered are leads. A before/after snapshot does not identify a new arm or generation. |
| Trigger type, source, mode, coupling, level and slope | Candidate S01 fields 21, 22, 23, 24, bytes 25–26, and 59 respectively. Prior panel/settings comparisons see CH1 configuration. Community source values CH1 0, CH2 1, EXT 2, EXT/5 3. | High for identified fields, lower for full enumerations and effects. Manufacturer documents EXT and EXT/5 hardware modes. Unqualified wire mapping, thresholds and setup restoration. |
| SINGLE / RUN-STOP / disarm | Static S13 0xdd8ec → 0xd854c queues a shared key; consumer 0xd857c. S93 reflects sampled menu state. Key 18 SINGLE candidate, key 19 RUN/STOP toggle candidate. Historical S13 save is a different key. | High for queue vs acknowledgment. No verified remote arm, completion or disarm. Menu/level keys remain context-sensitive. |
| CH1 and CH2 S02 | Static subtype 1 enters 0xde594 → 0xde038; selectors 0 and 1. Reply grammar below. 0xde4a0 and 0xde51c can clear a transfer flag and invoke callbacks. | High for branches and likely display data. Historical implementation assumed 3,200 samples in a particular mode. No replayable live pair or frozen-buffer lifecycle proof. |
| Multisource S02 | Static selector 4 invokes sources 0, 1 and internal 2 sequentially. Data echo selector 4; unavailable includes attempted internal source. The community protocol reports same-acquisition data via selector 4 on its setup. | High for loop order. Source identification, math treatment, latching and common generation require qualification on this unit. |
| Timebase, sample count and volts | S01 fields 160–162 timebase/zoom candidates, 163–170 horizontal-position fields. S82 prefix has LE24 count, data signed int8 display codes. Community conversion uses 25 counts/div, with position, volts/div and probe metadata. | High for byte count and display-code shape. Units, probe/position sign and factor, effective span/interval, decimation, deep-record length and trigger position are not proved by the USB stream. Distinguish exported/display points from native ADC rate. |
| Settings/screens as corroboration | Historical GIT-03 S01/S20/S20/S01 completed, strict FAIL. S20 rendered 800×480 RGB565 LE images; aggregate differences preserved. | High for qualitative viewing and settings comparison. Screens lack atomicity and do not establish freshness or simultaneous capture. |
| Transfer status and cached measurement | Exact S92 body 01 00 observed early in receive-only and late after primary replies. Transfer timeout/reply code supports asynchronous status as an explanation. S22 valid A2 with ASCII question mark observed in FW-CAP-03G/H. | High for captured framing and timing. S92 cause for an individual event stays unresolved. No freshness or completion witness from either path. |
| Settings-write and transfer-control lookalikes | S11 applies settings. S12 has state-changing trigger/transfer paths. S02 subtype 0 changes transfer state via 0xdd96c → 0xdd788. | High, static. Exclude all three from initial qualification and restoration. |
| Historical file export | Local verified front-media CSV has 4,164 rows with median step about 1 µs. Historical save and S10 transfer are a different workflow. | High for saved artifact and offline conversion. No proven source, trigger origin, or equivalence to S02. Repeating Save creates a file. |

## Candidate bytes and response grammar

The wire format is marker 53, LE16 length equal to payload length plus two, opcode, payload, and the low byte of the sum of all preceding octets.

| Candidate | Exact request in hex | Primary reply |
| --- | --- | --- |
| Settings read | 53 02 00 01 56 | S81 payload 208 bytes |
| Screen read | 53 02 00 20 75 | 76 S A0 data frames followed by one S A0 terminal; data total 768,000 bytes |
| CH1 display | 53 04 00 02 01 00 5A | S82, analog single-source grammar |
| CH2 display | 53 04 00 02 01 01 5B | S82, analog single-source grammar |
| Multisource display | 53 04 00 02 01 04 5E | S82, multiple ordered stream attempts; blocked |
| SINGLE candidate key | 53 04 00 13 12 01 7D | S93 acknowledgment with sampled menu value; blocked |
| RUN/STOP candidate key | 53 04 00 13 13 01 7E | S93 acknowledgment with sampled menu value; blocked |

An S82 body starts with subtype 00 and a three-byte little-endian sample count. Subtype 01 carries the echoed request selector followed by up to 10,000 sample bytes. Subtype 02 carries the echoed selector as a terminator. Subtype 03 carries the attempted internal source when unavailable; a single analog attempt can end there without subtype 02. To classify a completed channel, validate frame checksums, order, source association, exact count and terminal, then preserve the complete receive window. Subtype 03 can provide an unavailable outcome under the separately validated branch grammar.

See [public protocol qualification gates](QUALIFICATION_GATES.md), [settings v2](../SETTINGS_V2.md), [Hantek model specifications](https://www.hantek.com/products/detail/96), and the [community protocol at its archived revision](https://github.com/kooscode/hantek-dso5000p-linux/blob/b0facf139fb1b3da0a8c3ad0fa77a9eda76e1c46/protocol/PROTOCOL.md). Community behavior on other firmware, including reports of unusable stopped-buffer output, needs reproduction on 3.40.0 (151009.0).
