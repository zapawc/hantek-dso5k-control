# Hantek DSO5k Control

Windows CLI for the original-firmware Hantek DSO5102P, with gated read-only instrument qualification, **bounded native CSV file retrieval**, and offline analysis of existing captures.

**Development status:** `v0.2.0` release candidate is in preparation; its package and GitHub release have **not** been published. The existing `v0.1.0` preview remains the published baseline. See [proposed v0.2.0 release notes](docs/releases/V0.2.0_RELEASE_NOTES.md).

## Commands

- `hantek qualify readonly` — separately authorized Windows qualification batch for settings and qualitative screenshots. Historical primary responses passed; graphics aggregate discrepancies and classified asynchronous status produced **strict FAIL / exit 5**.
- `hantek offline settings` — decode an existing saved settings payload.
- `hantek offline waveform` — convert a bounded historical CH1 stream with caller-supplied timing and scale.
- `hantek offline file-replay` — validate an existing native S90 CSV response stream and extract its exact bytes with an independently supplied hash.
- `hantek files retrieve` — supervised, **one-request** S10 retrieval of an existing, completed `/mnt/udisk/WaveData163.csv` from a known original-firmware DSO5102P; limited to an 89,566-byte profile. Requires six operator-confirmation flags, 12-second receive-only preflight, exclusive ownership, an independent reference, and a fresh output/evidence location. The installed DEV-07 implementation passed its bounded CH1 live qualification on 2026-11-20 UTC. **No other path, CH2 transfer, or automated acquisition is qualified.**

The instrument must be in a verified suitable state before any hardware operation. Do not treat the offline commands, source installation or unit tests as hardware authorization. Installation and imports never open USB.

## Installation and testing

For source-based offline analysis use `python -m pip install .`; for approved Windows hardware access use `python -m pip install ".[winusb]"` in a dedicated environment with reviewed, pinned dependencies. Prefer an approved immutable Git commit or release wheel with verified SHA-256 for consumers. Run offline regressions with `python -m unittest discover -s tests -v` and `src` on `PYTHONPATH`, or from an installed checkout. CI uses synthetic streams and does not access the scope.

## Limitations and documentation

Automated SINGLE/EXT trigger control, remote CSV Save/Recall, S02 live streaming, guaranteed acquisition freshness, CH1/CH2 same-shot coherence, restoration, clock synchronization and firmware modification are unavailable or unqualified. The packaged file-retrieval S92 classifier has offline coverage, but no S92 notification occurred during its recorded live qualification.

See the [CLI contract](docs/CLI_CONTRACT.md), [capability matrix](docs/capabilities/CURRENT.md), [native CSV contract](docs/protocol/NATIVE_CSV_CLI.md), [file-transfer schema](schemas/file-transfer-v1.schema.json), [release policy](docs/releases/RELEASE_AND_PINNING_POLICY.md), [consumer guide](HANTEK_INTEGRATION.md) and [contribution workflow](CONTRIBUTING.md).

The source is provided under [MIT](LICENSE), subject to [third-party notices](THIRD_PARTY_NOTICES.md). Keep firmware, proprietary captures, private evidence, credentials and host-specific logs out of the public repository. See the [prior-art and reuse index](docs/prior-art/INDEX.md).
