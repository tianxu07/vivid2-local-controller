# GitHub release layout

Use a tag and release title such as `v1.0.0`. Do not commit generated binaries,
ZIP files, logs, local configuration, or build directories to the source tree.

Attach these files to the GitHub Release:

```text
Vivid2Controller-1.0.0-windows-x64.zip             Recommended
Vivid2Controller-1.0.0-windows-x64-onefile.exe    Optional
SHA256SUMS.txt
```

The recommended ZIP must contain the complete PyInstaller one-folder output:

```text
Vivid2Controller/
├── Vivid2Controller.exe
├── LICENSE
├── THIRD_PARTY_LICENSES.txt
├── PYTHON_LICENSE.txt
├── PYINSTALLER_COPYING.txt
└── _internal/
```

Release notes should direct most users to the ZIP, tell them to extract the
entire folder before launching the EXE, identify the optional single-file build
as slower to start, and repeat the unofficial/non-affiliation statement.

The repeatable builder runs offline tests, creates both PyInstaller builds,
copies all visible license notices, makes the ZIP, and writes the checksums:

```powershell
.\packaging\build_release.ps1 -Version 1.0.0
```

Before creating a release:

1. Run the complete offline test suite.
2. Run the privacy audit against the non-ignored source set.
3. Build both PyInstaller specifications on Windows.
4. Confirm all notices above are present in the one-folder output.
5. Create the ZIP from the whole `dist/Vivid2Controller` directory.
6. Generate SHA-256 hashes for both downloadable artifacts.
7. Test on a real RGB Vivid II only after the user explicitly chooses to do so.
