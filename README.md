# Hantek DSO5k Control

A Windows CLI for read-only qualification of the original 2015 DSO5102P and offline inspection of captured data. The packaged read-only batch was hardware-qualified for settings and qualitative screens. All primary replies passed in that run. Graphics aggregate discrepancies and classified asynchronous status kept strict integrity at FAIL, exit 5.

- `hantek qualify readonly` is the gated four-request batch for settings and two screenshots.
- `hantek offline settings` decodes an existing settings payload.
- `hantek offline waveform` converts a bounded historical CH1 stream using caller-supplied scale and interval.
- Trigger control, SINGLE, live waveform capture, acquisition completion and channel coherence are unavailable.

Install locally with `python -m pip install .` for offline analysis, or `python -m pip install ".[winusb]"` on Windows for a separately approved validation. Installation and imports are passive. Run `python -m unittest discover -s tests -v` with `src` on PYTHONPATH, or from an installed checkout. Tests use synthetic streams and mock ownership/transport. CI requires no USB libraries.

Consumers pin an immutable commit SHA or verified release artifact digest. A tag must be protected and checked against the approved SHA or artifact. Never follow an automatically updated branch. See the [CLI contract](docs/CLI_CONTRACT.md), [schema](schemas/capture-manifest-v2.schema.json) and [release policy](docs/releases/RELEASE_AND_PINNING_POLICY.md).

Use the [consumer guide](HANTEK_INTEGRATION.md) and [contribution workflow](CONTRIBUTING.md) for cross-project changes. See the [settings migration guide](docs/SETTINGS_V2.md) and [publication gates](OPEN_SOURCE_READINESS.md).

The proposed open-source license is pending owner approval. See the [open-source review](OPEN_SOURCE_READINESS.md) for current publication gates.
