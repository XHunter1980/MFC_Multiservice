# -*- mode: python ; coding: utf-8 -*-

import os
import sys

# Импортируем версию из version.py
sys.path.insert(0, os.path.abspath('.'))
try:
    from version import __version__
except ImportError:
    __version__ = "1.0.0"

block_cipher = None

a = Analysis(
    ['MFC_Multiservice_9.6.py'],  # Точка входа (замените на ваш главный файл)
    pathex=[],
    binaries=[],
    datas=[
        ('multiservise.ico', '.'),  # Иконка
        ('version.py', '.'),  # Файл версии
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
    name=f'MFC_Multiservice_{__version__}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # False для GUI приложения, True для консольного
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='multiservise.ico',  # Путь к иконке
)