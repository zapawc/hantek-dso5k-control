# Contributing to shared Hantek software

## Open the shared issue

After the public repository and license are explicitly approved, search for duplicates and select Bug, Feature, Protocol/API investigation, Hardware qualification, or Consumer integration request. Link only an approved public originating issue or use an opaque project alias. Before launch, draft the issue locally. Assume that issue text, comments, and attachments are visible to anyone. Use approved synthetic examples or redacted excerpts. Keep raw instrument captures, full manifests, historical evidence ZIPs, private project names and links, personal identifiers, calibration, firmware and credentials local. If an issue depends on restricted evidence, withhold it and agree with a maintainer on a separate private review.

Include the pinned CLI/software and schema versions, immutable SHA or digest, instrument model and known firmware, host and driver, sorted reproduction steps, exact command with redactions, expected behavior, actual behavior, safety implications, and measurable acceptance criteria. Report primary replies, strict session, and screenshot aggregate separately. Exit 5 is documented for completed primary replies with integrity anomalies. Report the discrepancy or downstream impact if it differs from the contract.

## Triage and labels

Assign one issue-type, priority, maturity, and work-status label. The form supplies type and `status:triage` after the corresponding labels exist in the repository. Maintainers create the labels from the plan only after separate GitHub authorization. Review completeness and public suitability before triage. Required fields and disabling blank issues guide submissions. Those settings do not redact or prevent sensitive information in descriptions, comments, or attachments. [Label definitions](docs/integrations/issue-labels.json) · [GitHub form schema](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-githubs-form-schema)

| Dimension | Labels | Triage action |
| --- | --- | --- |
| Type | bug, feature, protocol-investigation, hardware-qualification, consumer-integration | Use the form matching the shared scope. |
| Priority | p0 safety or release blocker, p1 high impact, p2 normal, p3 exploratory | Rank by observed impact and risk. |
| Maturity | unqualified, offline-tested, hardware-qualified | Advance only with scoped evidence. |
| Work status | triage, needs-evidence, ready, in-progress, blocked, in-review | Record owner, next action and external gate. |

Route consumer-only interpretation or threshold work to the originating project. Deduplicate with a link and migration note. Require independent hardware approval and explicit request/stop boundaries before device work. Keep the hardware qualification issue open while the scope is unresolved.

## Branch, review, and ship

Before proposing or starting capability work, use the [prior-art index](docs/prior-art/INDEX.md) and include a Prior Art and Reuse Assessment in the proposal and development return. Cite verified implementations and their scope. Identify the smallest needed qualification only when evidence leaves a material gap. Update the index for reviewed capability or evidence changes and keep restricted evidence local.

1. External contributors may fork after launch and branch from reviewed `main` as `issue/<number>-<summary>`. Keep shared changes in the Hantek repository and keep downstream policy in the consumer repository.
2. Open a pull request linked to the issue. State CLI/schema effects, safety implications, evidence scope and exact offline test outcomes. Put authorized hardware references in a sanitized summary with hashes. CI is offline only.
3. Maintainers review source rights, evidence safety, the acceptance criteria, schema compatibility and scope of hardware claims. Hardware-sensitive changes need separately authorized qualification. Treat new features as unqualified until explicitly validated and released. Use a non-closing reference while validation or the release is pending.
4. Record the merged SHA, test evidence, known limitations and the approved release link with exact wheel SHA-256 or full source SHA. A deliberate downstream pin update belongs in a consumer PR.
5. Close shipped work only when the issue records the acceptance evidence and its immutable delivery reference. Close duplicate, out-of-scope or declined work with a reason and relevant link. A downstream acceptance issue closes under its own criteria.

## Contributor licensing

After the repository opens, confirm you have authority to submit your contribution under the [MIT license](LICENSE). Identify third-party material, its exact source, and applicable terms. Retain required notices. Maintainers may require changes or decline a submission whose rights cannot be verified. Issue discussion alone grants no permission to reuse proprietary code. The project uses the contribution statement in the pull request template.

Consumers must pin an approved immutable Git commit or verified release artifact digest. They deliberately review their own dependency upgrades and never automatically follow `main`.

[Architecture decision](docs/adr/0001-cross-project-integration.md) · [Consumer guide](HANTEK_INTEGRATION.md) · [Release policy](docs/releases/RELEASE_AND_PINNING_POLICY.md) · [License proposal](LICENSE_RECOMMENDATION.md)
