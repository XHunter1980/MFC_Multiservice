# -*- coding: utf-8 -*-
"""
PyInstaller spec-файл для сборки Windows EXE приложения «МФК Мультисервис».

Версия релиза берётся из version.py (APP_VERSION) — единого источника версии.
Иконка_exe_взята из multiservise.ico.

Сборка локально (Windows):
    pip install -r requirements.txt
    pyinstaller build.spec --noconfirm

Сборка в CI: см. .github/workflows/release.yml
"""

import os
from version import APP_VERSION  # версия релиза = версия исходника

# Имя основного исходного файла меняется вместе с версией
# (MFC_Multiservice_9.6.py -> MFC_Multiservice_9.7.py и т.д.) — ищем glob'ом.
main_script = None
for f in sorted(os.listdir(SPECPATH)):
    if f.startswith("MFC_Multiservice_") and f.endswith(".py"):
        main_script = f
if main_script is None:
    raise SystemExit("Не найден исходный файл MFC_Multiservice_*.py")

ICON = "multiservise.ico"

a = Analysis(
    [os.path.join(SPECPATH, main_script)],
    pathex=[SPECPATH],
    binaries=[],
    datas=[],
    hiddenimports=["openpyxl", "paramiko"],
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
    a.binaries,
    a.datas,
    [],
    name=f"MFC_Multiservice_v{APP_VERSION}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,            # windowed-приложение (без консоли)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,                # иконка приложения из .ico файла
)
