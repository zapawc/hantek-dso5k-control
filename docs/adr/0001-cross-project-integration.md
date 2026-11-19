# HANTEK-ADR-001 · Cross-project integration

**Status: Accepted**

**Decision owner: Hantek project operator**

## Context

CYD-RF-Diagnostic, Digital Workbench, and future consumers need consistent instrument behavior and a way to request shared changes. Historical firmware research, unit-specific data and raw captures remain in the local evidence archive.

## Decision

GitHub is the authoritative source for shared Hantek software. Once the public repository is approved and provisioned, GitHub Issues are the standard engineering handoff for shared defects, features, protocol/API investigations, hardware qualification proposals and consumer integration requests. Link consumer and shared issues when both projects have work.

The Hantek project owns the instrument CLI, transport safety, protocol implementation, capability status, versioned schemas and instrument-level evidence. Each consumer owns its procedures, stimuli, decision thresholds, analyses, presentation and release acceptance. Consumer code invokes the documented CLI and versioned outputs.

Consumers must pin a verified immutable release artifact digest or a full reviewed commit SHA. A tag may be used as a reference only when its mapping to the approved artifact digest or commit is verified. Updating the pin requires an explicit consumer change and compatibility tests. Consumers must never automatically follow `main` or `latest`.

Issues and pull requests record evidence and outstanding qualification gates. Hardware execution requires separate authorization. Keep proprietary images, calibration, credentials and historical raw captures local. Share only cleared summaries, hashes and sanitized evidence.

## Consequences

The shared issue records the implementation, approved validation and shipped immutable reference. Consumers update at their own pace and close their own acceptance work separately. A framed reply, a strict session result and a user-visible feature claim remain distinct. Local documents and tests are staged for review before repository publication.

[Engineering workflow](../../CONTRIBUTING.md) · [Consumer guide](../../HANTEK_INTEGRATION.md) · [Release policy](../releases/RELEASE_AND_PINNING_POLICY.md)
