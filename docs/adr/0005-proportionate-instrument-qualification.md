# HANTEK-ADR-005 · Proportionate instrument qualification

**Status: Accepted**

**Decision owner: Hantek Architecture**

## Qualification gates

| Operating mode | Evidence required |
| --- | --- |
| Supervised experiment | One exact operation on a reviewed source commit, narrowly scoped and bounded on the host. Require a disposable fixture, explicit risk acceptance, an operator at the instrument, separately approved run and recovery procedures, and complete evidence. Authorization may accept a specifically understood risk of temporary acquisition lockup without a complete static proof of device recovery. |
| Interactive service operation | Demonstrate normal operation for the intended workload, meaningful fault detection and operator-visible state, safe serialization and restoration, and a reviewed way for an operator to recover and reconcile the instrument before resuming. |
| Unattended automation | Require scoped repeatability, bounded failure and recovery behavior supported by evidence, reliable interruption handling, configuration restoration, and a fail-closed policy for unknown history. Supervised evidence alone does not qualify automation. |

ACQ-10C may prepare the supervised adapter on retained firmware 3.40.0 (151009.0). Accepted preparation risks include volatile clipping of disposable samples, temporary acquisition or display disturbance, and possible acquisition lockup that requires operator intervention. The operator must accept these risks before any separately authorized run. Device-side recovery remains unproven. Evidence of permanent hardware damage, persistent configuration corruption, or mutation outside the approved volatile scope stops work for Architecture review. ACQ-10H stays on HOLD pending adapter review, operator risk acceptance and explicit run authorization.

The first experiment evaluates running display snapshots and ordinary continuation. Frozen acquisition and recovery guarantees have separate gates. Same-shot identity is outside this experiment. [Supervised procedure](../protocol/NATIVE_WAVEFORM_ACQ10C.md) · [Qualified Windows baseline](0004-qualified-baseline-and-selective-reuse.md)

## Persistent service direction

One service owns the instrument exclusively and serializes all requests. It polls selected live measurements into a timestamped cache and exposes cache age and fault state. It suspends polling during acquisition, snapshots the configuration, applies capture-specific settings where authorized, arms and observes the capture, and preserves the waveform before destructive state changes. It then restores and verifies the original configuration and resumes monitoring only after successful restoration. Ambiguous history or unsuccessful restoration latches an explicit fault and keeps polling suspended until reconciliation.

Native display-window snapshots and frozen native CSV retrieval serve different workflows. Each operation requires qualification appropriate to its use. ACQ-10C implements only the supervised one-request adapter.
