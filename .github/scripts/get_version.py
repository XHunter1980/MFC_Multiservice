#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CI-утилита: извлечение версии релиза из version.py (единый источник версии).

Версия в version.py должна совпадать с версией исходника
(UpdaterApp.VERSION_INFO = "МФК Мультисервис v<VER>").

Использование:
    python .github/scripts/get_version.py                 # печать APP_VERSION
    python .github/scripts/get_version.py --check         # сверка с исходником (exit 1 при расхождении)
    python .github/scripts/get_version.py --tag v9.6      # сверка тега с APP_VERSION и исходником
    python .github/scripts/get_version.py --github-output # запись в $GITHUB_OUTPUT + сверка
"""

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_GLOB_PREFIX = "MFC_Multiservice_"


def read_app_version():
    """Читает APP_VERSION из version.py без импорта (простой парсинг)."""
    path = os.path.join(ROOT, "version.py")
    with open(path, encoding="utf-8") as f:
        m = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', f.read(), re.M)
    if not m:
        print("ERROR: APP_VERSION не найден в version.py", file=sys.stderr)
        sys.exit(2)
    return m.group(1)


def find_source_file():
    candidates = sorted(
        f for f in os.listdir(ROOT)
        if f.startswith(SRC_GLOB_PREFIX) and f.endswith(".py")
    )
    if not candidates:
        print("ERROR: не найден исходный файл MFC_Multiservice_*.py", file=sys.stderr)
        sys.exit(2)
    return os.path.join(ROOT, candidates[0])


def read_source_version():
    """Достаёт версию из константы VERSION_INFO исходного файла (изменения не вносятся)."""
    src = find_source_file()
    with open(src, encoding="utf-8") as f:
        m = re.search(r'VERSION_INFO\s*=\s*["\'][^"\']*v(\d+(?:\.\d+)*)["\']', f.read())
    if not m:
        print(f"ERROR: версия не найдена в VERSION_INFO файла {os.path.basename(src)}",
              file=sys.stderr)
        sys.exit(2)
    return m.group(1), os.path.basename(src)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true", help="Сверить version.py с исходником")
    p.add_argument("--tag", help="Имя тега релиза (например v9.6) для сверки")
    p.add_argument("--github-output", action="store_true",
                   help="Записать version/tag в $GITHUB_OUTPUT")
    args = p.parse_args()

    app_version = read_app_version()
    src_version, src_name = read_source_version()

    ok = True
    if app_version != src_version:
        print(f"ERROR: версия в version.py ({app_version}) не совпадает с версией "
              f"исходника {src_name} (VERSION_INFO v{src_version}). "
              f"Обновите APP_VERSION в version.py.", file=sys.stderr)
        ok = False

    if args.tag:
        tag = args.tag.strip()
        expected = f"v{app_version}"
        if tag.lower() != expected.lower():
            print(f"ERROR: тег '{tag}' не соответствует версии исходника ('{expected}').",
                  file=sys.stderr)
            ok = False

    if not ok:
        sys.exit(1)

    if args.github_output:
        out = os.environ.get("GITHUB_OUTPUT")
        line = f"version={app_version}\ntag=v{app_version}\n"
        if out:
            with open(out, "a", encoding="utf-8") as f:
                f.write(line)
        else:
            print(line)

    print(app_version)
    print(f"OK: версия исходника ({src_name}, v{src_version}) == version.py (v{app_version})",
          file=sys.stderr)


if __name__ == "__main__":
    main()
