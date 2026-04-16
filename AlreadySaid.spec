# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


root = Path.cwd()

datas = [
    (str(root / "static"), "static"),
    (str(root / "translation_variants.json"), "."),
]

hiddenimports = ["objc", "AppKit", "Foundation", "WebKit", "PyObjCTools"]

a = Analysis(
    ["desktop_app.py"],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
    console=False,
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

app = BUNDLE(
    coll,
    name="The Already Said.app",
    icon=None,
    bundle_identifier="org.local.the-already-said",
)
