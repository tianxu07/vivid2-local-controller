# Optional single-file Windows build. The one-folder build remains preferred.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, collect_submodules


project_root = Path(SPECPATH)
bleak_datas, bleak_binaries, bleak_hiddenimports = collect_all("bleak")
notice_datas = [
    (str(project_root / "LICENSE"), "."),
    (str(project_root / "THIRD_PARTY_LICENSES.txt"), "."),
    (str(project_root / "packaging" / "PYTHON_LICENSE.txt"), "."),
    (str(project_root / "packaging" / "PYINSTALLER_COPYING.txt"), "."),
]
winrt_binaries = collect_dynamic_libs("winrt")
hiddenimports = sorted(
    set(
        bleak_hiddenimports
        + collect_submodules("bleak.backends.winrt")
        + collect_submodules("winrt")
    )
)

a = Analysis(
    [str(project_root / "vivid2_gui.py")],
    pathex=[str(project_root)],
    binaries=bleak_binaries + winrt_binaries,
    datas=bleak_datas + notice_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unittest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Vivid2Controller",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=True,
    version=str(project_root / "packaging" / "version_info.txt"),
)
