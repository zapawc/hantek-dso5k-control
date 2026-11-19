# License and third-party provenance review

The root MIT text has an unresolved copyright placeholder. Package metadata declares no active project license. The new snapshot has neutral field IDs. Confirm source rights and approved terms before publication. [Provenance review](../../SOURCE_PROVENANCE_RESOLUTION.md)

| Component | Scope and observed license | Handling for preview |
| --- | --- | --- |
| PyUSB 1.3.1 | Optional WinUSB dependency. Installed license is the BSD 3-clause text. | Use the approved upstream wheel. Preserve its license and notices if redistributing it. [Upstream license](https://github.com/pyusb/pyusb/blob/master/LICENSE) |
| libusb-package 1.0.30.1 | Optional Windows backend. Apache-2.0 Python wrapper. Installed wheel includes the native libusb 1.0 DLL. Upstream identifies libusb as LGPL-2.1. | Use the approved upstream wheel as a separate dependency. Review native notice/source obligations before repackaging the DLL or distributing a wheelhouse. [Upstream licensing](https://github.com/pyocd/libusb-package/blob/main/README.md#license) |
| importlib_resources 7.1.0 | Optional runtime dependency. Installed license metadata identifies Apache-2.0. | Use a reviewed separate wheel. Preserve applicable notices if redistributing it. |
| setuptools and wheel | Local build tooling. Neither is shipped in the Hantek wheel. | Lock and record build versions for official artifact generation. |
| GitHub Actions checkout | CI-only upstream action pinned by commit in the workflow. | Excluded from the wheel. Review its pin during release approval. |
| Community protocol references and official firmware | Historical workspace research. Community repository has MIT terms, while vendor firmware and installed files are separate materials. | No community source tree, vendor artifacts, calibration or raw captures are packaged or tracked. The migrated parser and field layout derive from local test code and observations. Keep traces and device files local. |

The Hantek wheel contains Python source, its packaged PowerShell helper and wheel metadata. The offline check verifies package members and confirms the native backend is separate. A future release should link the specific dependency artifact digests and platform, and distinguish installing from upstream from redistributing a wheelhouse. [Release checklist](../../OPEN_SOURCE_READINESS.md)
