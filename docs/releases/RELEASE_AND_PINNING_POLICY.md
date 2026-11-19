# Release and pinning policy

Use semantic versions and an independent output schema version. Consumers pin a reviewed full Git commit, or a release artifact with its verified SHA-256. A version tag alone is insufficient unless protected and verified against the reviewed source or digest. Changes to a consumer pin require deliberate review and compatibility tests. Avoid floating branches and automatic updates.

Before publication, audit staged artifacts, proprietary content, credentials, provenance and license coverage, dependency digests, and protected-tag policy. Run offline CI and get separate publication approval. HANTEK-GIT-03 qualified the packaged Windows read-only batch for the tested scope. Keep its evidence outside the repository and document exit 5 and its limitations. Clock sync, waveform and mutation gates remain separate. [Open-source publication gates](../../OPEN_SOURCE_READINESS.md)
