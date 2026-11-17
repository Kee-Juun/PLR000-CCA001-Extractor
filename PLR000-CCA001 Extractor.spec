# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all
from pathlib import Path
import sys

root = Path(SPECPATH)
sys.path.insert(0, str(root))
from tools.build_environment import isolate_windows_dll_search_path
from tools.startup_splash_build import build_splash
isolate_windows_dll_search_path()

datas = [('assets', 'assets')]
binaries = []
hiddenimports = ['pythoncom', 'pywintypes', 'win32timezone']
hiddenimports += collect_submodules('win32com')
hiddenimports += collect_submodules('PyQt6.QtMultimedia')
tmp_ret = collect_all('keyring')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    # Include the DLL policy in Analysis' cache key to invalidate older bundles.
    hooksconfig={'plr_build': {'dll_search_policy': 1}},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
splash = build_splash(root, Path(workpath), a)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    splash.binaries,
    a.binaries,
    a.datas,
    [],
    name='PLR000-CCA001 Extractor',
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
    icon=['assets\\app_icon.ico'],
)
