# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


root = Path.cwd()

datas = [
    (str(root / "static"), "static"),
    (str(root / "translation_variants.json"), "."),
]

a = Analysis(
    ["windows_app.py"],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="The Already Said",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="The Already Said",
)
