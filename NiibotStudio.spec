# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for Niibot Studio.

Everything the app needs (web UI, icon library, changelog) is bundled inside the
one-folder distribution; user data is written next to the executable in ``data``.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve()

datas = [
    (str(ROOT / "web"), "web"),
    (str(ROOT / "CHANGELOG.md"), "."),
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD-PARTY-NOTICES.md"), "."),
]

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("bleak")
    + collect_submodules("fastapi")
    + collect_submodules("pydantic")
    + ["pypdfium2", "qrcode", "barcode", "multipart", "PIL._tkinter_finder"]
)

a = Analysis(
    [str(ROOT / "niibot_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NiibotStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NiibotStudio",
)
