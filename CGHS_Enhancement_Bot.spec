# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = []
binaries = []
hiddenimports = []
tmp_ret = collect_all('selenium')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# The automation core lives in the 'cghs' package. PyInstaller cannot see
# submodules that are imported lazily (cghs.parsing defers 'fitz'), so they
# are collected explicitly - otherwise the frozen EXE raises ModuleNotFound
# at runtime instead of failing at build time.
hiddenimports += collect_submodules('cghs')

# The UI theme module is imported by app.py for presentation only; it is
# listed explicitly so a packaging mistake fails at build time, not at
# runtime with an unstyled or crashed window.
hiddenimports += ['ui_theme']


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='CGHS_Enhancement_Bot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
