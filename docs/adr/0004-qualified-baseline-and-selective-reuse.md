# HANTEK-ADR-004 · Qualified Windows baseline and selective reuse

**Status: Accepted**

**Decision owner: Hantek Architecture**

## Context

Automated electronic testing is the primary project objective. ACQ-08H demonstrated one supervised sequential retrieval of two existing staged CSVs on the DSO5102P with original firmware 3.40.0 (151009.0). ACQ-09A found useful external acquisition and conversion work, with different firmware, platform and session guarantees.

## Decision

1. Retain original firmware 3.40.0 (151009.0). Firmware upgrades and patches are outside this architecture.
2. Retain the internal Windows/PyUSB/WinUSB transport, single-owner controls, interruption-marker behavior, bounded timeouts and evidence contracts. New hardware operations require separate authorization.
3. Use Strategy B. Reuse external protocol knowledge and appropriately licensed pure functions selectively, after checking provenance and compatibility. Perform external prior-art research and a reuse assessment before new protocol development. This consolidation is documentation only.
4. Treat native S02/S82 display-window acquisition and frozen native CSV export followed by S10 retrieval as complementary. Evaluate native waveforms for current displayed data and the frozen path for completed stopped or larger native records. Transfer integrity alone cannot establish remote export, acquisition completion, freshness or same-shot identity.
5. Design a shared versioned result contract for both paths. Record channel identity, scale and time origin, configuration, source and clock provenance, availability/freshness/coherence states, artifact integrity, and independent transport and cleanup outcomes. A host run ID cannot serve as instrument-generation evidence.
6. Future live camera integration is a separate consumer project. This repository owns versioned instrument results and evidence references. Camera capture, correlation, presentation and consumer-specific automation belong outside it.

## Pinned prior art and reuse limits

| Source | Reviewed revision | Reuse boundary |
| --- | --- | --- |
| [kooscode/hantek-dso5000p-linux](https://github.com/kooscode/hantek-dso5000p-linux/tree/b0facf139fb1b3da0a8c3ad0fa77a9eda76e1c46) | `b0facf139fb1b3da0a8c3ad0fa77a9eda76e1c46` | MIT source with notice obligations. Confirm device-derived table provenance separately. Author-reported native selector-4 observations are on a DSO5102P modified to 200 MHz with firmware 210225. |
| [titos-carrasco/DSO5102P-Python](https://github.com/titos-carrasco/DSO5102P-Python/tree/181f290601c8eea43a00d8aa0add0d85ec8d19da) | `181f290601c8eea43a00d8aa0add0d85ec8d19da` | MIT source with notice obligations. Tested firmware unspecified. The author lists waveform and settings implementations as untested. |
| [Mikrocontroller.net protocol](https://www.mikrocontroller.net/index.php?title=Hantek_Protokoll&oldid=78666) and [discussion](https://www.mikrocontroller.net/index.php?title=Diskussion:Hantek_Protokoll&oldid=70044) | Wiki revisions 78666 and 70044 | Wiki text defaults to CC BY-SA 2.0 DE. Linked code and uploads have separate terms. Earlier model and firmware observations are attributed research. |
| [crt0512/xdso](https://github.com/crt0512/xdso/tree/a3f5230f9ed19b91e193767ec5251a9ddcc312f1) | `a3f5230f9ed19b91e193767ec5251a9ddcc312f1` | No actionable source license identified. Reference only pending clarification. |
| [OpenHantek/openhantek](https://github.com/OpenHantek/openhantek/tree/836cd98f0601bb8023a3437d68161eec5c696d37) | `836cd98f0601bb8023a3437d68161eec5c696d37` | GPL v3 license text. Platform comparison only. The reviewed supported-device list does not identify DSO5102P. |

Record source, immutable revision, notice requirements and provenance before reuse. Linux device access, automatic recovery and broad protocol control require independent qualification on the retained Windows and firmware profile. [Prior-art process](../prior-art/INDEX.md#prior-art-and-reuse-assessment) · [Cross-project integration](0001-cross-project-integration.md) · [ACQ-08H qualification](../protocol/PAIRED_CSV_ACQ08B.md#acq-08h-supervised-hardware-result) · [Issue #1](https://github.com/zapawc/hantek-dso5k-control/issues/1)

## Consequences

ACQ-10A starts with offline selector-4 waveform decoding and acquisition result design. Hardware evidence remains operation-specific and pinned to the exercised source. A shared schema alone establishes neither common acquisition identity nor unattended reliability.
