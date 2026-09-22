# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('hand_landmarker.task', '.')]
binaries = []
hiddenimports = []
tmp_ret = collect_all('mediapipe')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

for pkg in [
    'winrt',
    'winrt.windows.foundation',
    'winrt.windows.foundation.collections',
    'winrt.windows.devices.enumeration',
    'winrt.windows.graphics.imaging',
    'winrt.windows.media.capture',
    'winrt.windows.media.capture.frames',
    'winrt.windows.media.mediaproperties',
]:
    try:
        tmp_w = collect_all(pkg)
        datas += tmp_w[0]; binaries += tmp_w[1]; hiddenimports += tmp_w[2]
    except Exception:
        pass

# Exclude unused transitive dependency stacks (audio, tk/tcl, unused automation)
excludes = [
    'sounddevice',
    '_sounddevice',
    '_sounddevice_data',
    'tkinter',
    '_tkinter',
    'tcl',
    'pyautogui',
    'mouseinfo',
    'pymsgbox',
    'pyscreeze',
    'pytweening',
    'pygetwindow',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='HandSlideController',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='HandSlideController',
)
