# Consumer integration

Invoke the documented CLI as a subprocess. Use the version and the matching schema, process exit code, `sequence_complete`, integrity fields and artifact hashes. Never import private USB modules. CYD and other consumers own power, trigger policy, interpretation and workflow. Digital Workbench owns UI and overlays. The CLI does not identify a shared acquisition event or synchronize instrument clocks.

For an intentionally selected revision, install `hantek-dso5k-control @ git+<approved-remote>@<full-reviewed-commit-sha>`. This is a placeholder until a repository and approved release exist. For wheel distribution verify the approved SHA-256 before installation. Confirm with `hantek --version` and schema version before running integration checks. See [CLI](../CLI_CONTRACT.md).

Copy the [portable Hantek integration guide](../../HANTEK_INTEGRATION.md) into the consumer repository. Use the [shared issue workflow](../../CONTRIBUTING.md) for instrument or CLI changes.
