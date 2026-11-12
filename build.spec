# -*- mode: python ; coding: utf-8 -*-

import os
import sys

# Импортируем версию из version.py
sys.path.insert(0, os.path.abspath('.'))
try:
    from version import APP_VERSION
except ImportError:
    APP_VERSION = "1.0.0"

block_cipher = None

a = Analysis(
    ['MFC_Multiservice_9.6.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('multiservise.ico', '.'),
        ('version.py', '.'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=f'MFC_Multiservice_{APP_VERSION}',
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
    icon='multiservise.ico',
)