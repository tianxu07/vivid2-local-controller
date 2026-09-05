# Local v1.3.0 packaging preparation

The product is **Chihiros Local Controller 1.3.0** with RGB Vivid II, A2 Max,
Magnetic Light II, Magnetic Light, Cooling Fan and Z Light support. This phase
creates artifacts for physical validation from `feature/zlight`, with no commit,
tag, push, merge, GitHub release or upload. Public v1.2.0 remains the previous
release until a separately authorized publication.
Existing release artifacts remain untouched.

## Prerequisites and audit

Use Windows x64 Python and the project's environment with
`requirements-build.txt` installed. Before building, provide an ignored local
JSON string array of all known private identifiers and machine paths to reject,
including identities from local GUI preferences, logs and research captures. The
default location is `config/release-private-patterns.json`; never commit it.

The source audit checks the current non-ignored source set, including new files.
It rejects private identifiers/paths, secret signatures and private artifact
paths. Generated payload checks inspect every runtime file, compressed ZIP
entries, executable archive entries and decompressed Python modules. Private
probe tooling, including `chihiros.magnetic2_diagnostic`, is explicitly excluded
from both specifications. Keep any expanded audit pattern file private and ignored.

This audit covers the current source tree and artifacts, **not Git history**.
Earlier development commits may contain private identifiers. Review history
before any later public push; a clean payload does not sanitize historical commits.

## Build locally

From the repository root:

```powershell
.\packaging\build_release.ps1 -Version 1.3.0 -RunName physical-validation
```

The builder audits source, runs all hardware-free tests, then creates both
PyInstaller variants. Each run uses new timestamped version directories and
refuses to overwrite existing output. It never launches the application.

The outputs under `release/v1.3.0-<run>/` are:

```text
onedir/ChihirosLocalController/
  ChihirosLocalController.exe
  README.txt
  LICENSE
  THIRD_PARTY_LICENSES.txt
  PYTHON_LICENSE.txt
  PYINSTALLER_COPYING.txt
  _internal/
ChihirosLocalController-1.3.0-windows-x64.zip
ChihirosLocalController-1.3.0-windows-x64-onefile.exe
SHA256SUMS.txt
```

The ZIP contains precisely the complete audited one-folder application, with
its documentation and licenses. SHA256SUMS.txt covers the ZIP and optional
one-file executable. Logs, captures, environments, private configurations,
research/decompilation artifacts and tests are not package contents.
The audit report and intermediate build diagnostics stay in the separate
`build-v1.3.0-<run>` directory, not the public payload.

For direct developer builds, the generalized specifications are
`ChihirosLocalController.spec` and `ChihirosLocalController-onefile.spec`.
The builder is preferred because it also audits contents, exposes notices next
to the EXE, assembles the complete ZIP and produces checksums.

## Stop before publication

Inspect the local output and perform physical testing only as an explicit user
action. Re-test the packaged application on the intended Windows machine.
No automatic smoke test may scan, connect, or send BLE commands. Unsigned builds
may produce Windows warnings. Do not distribute until artifact review and Git
history privacy review are complete. No publication is performed by these tools.
