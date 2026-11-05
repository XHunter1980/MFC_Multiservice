# -*- coding: utf-8 -*-
"""
Автотесты проекта «МФК Мультисервис» (MFC_Multiservice_*.py).

Принципы:
  * Исходный код приложения НЕ изменяется — тесты проверяют его как есть
    (статический анализ) и тестируют вспомогательные модули (version.py, CI-скрипт).
  * Тесты не требуют доступа к реальным МФК и GUI-окружению.

Запуск локально:            pytest tests/ -v
Запуск в CI GitHub Actions: .github/workflows/test.yml
"""

import ast
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from version import APP_VERSION, get_version  # noqa: E402


def find_main_source():
    """Находит основной файл приложения MFC_Multiservice_<ver>.py."""
    candidates = sorted(
        f for f in os.listdir(ROOT)
        if f.startswith("MFC_Multiservice_") and f.endswith(".py")
    )
    assert candidates, "Не найден исходный файл MFC_Multiservice_*.py"
    return os.path.join(ROOT, candidates[0])


@pytest.fixture(scope="module")
def source_text():
    with open(find_main_source(), encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def source_tree(source_text):
    return ast.parse(source_text)


# ---------------------------------------------------------------------------
# Версия и релизная автоматизация
# ---------------------------------------------------------------------------

class TestVersion:
    def test_app_version_format(self):
        """APP_VERSION — корректная строка версии (числа через точку)."""
        assert re.fullmatch(r"\d+(\.\d+)*", APP_VERSION), \
            f"Некорректный формат версии: {APP_VERSION!r}"

    def test_get_version_matches_constant(self):
        assert get_version() == APP_VERSION

    def test_version_info_consistency(self, source_text):
        """Версия релиза (version.py) обязана совпадать с версией исходника
        (UpdaterApp.VERSION_INFO = 'МФК Мультисервис v<VER>')."""
        m = re.search(r'VERSION_INFO\s*=\s*["\'][^"\']*v(\d+(?:\.\d+)*)["\']', source_text)
        assert m, "VERSION_INFO не найден в исходнике приложения"
        assert m.group(1) == APP_VERSION, (
            f"Версия исходника v{m.group(1)} != версия релиза v{APP_VERSION}. "
            f"Обновите APP_VERSION в version.py."
        )

    def test_filename_version_matches(self, ):
        """Имя файла MFC_Multiservice_<ver>.py содержит ту же версию."""
        name = os.path.basename(find_main_source())
        m = re.search(r"MFC_Multiservice_(\d+(?:\.\d+)*)\.py", name)
        assert m, f"В имени файла {name} не найдена версия"
        assert m.group(1) == APP_VERSION

    def test_ci_script_check_passes(self):
        """CI-скрипт сверки версии завершается успешно."""
        script = os.path.join(ROOT, ".github", "scripts", "get_version.py")
        result = subprocess.run(
            [sys.executable, script, "--check"],
            capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == APP_VERSION

    def test_ci_script_rejects_wrong_tag(self):
        """CI-скрипт отклоняет тег, не соответствующий версии исходника."""
        script = os.path.join(ROOT, ".github", "scripts", "get_version.py")
        result = subprocess.run(
            [sys.executable, script, "--tag", "v0.0-test"],
            capture_output=True, text=True
        )
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# Целостность исходного кода приложения (без изменения!)
# ---------------------------------------------------------------------------

class TestSourceIntegrity:
    def test_source_is_valid_python(self, source_text):
        """Основной исходник компилируется (синтаксис корректен)."""
        compile(source_text, find_main_source(), "exec")

    def test_entry_point_exists(self, source_tree):
        """У приложения есть точка входа __main__ (запуск Tkinter-окна)."""
        has_main_guard = any(
            isinstance(node, ast.If) and
            getattr(node.test, "left", None) is not None and
            isinstance(node.test.left, ast.Name) and
            node.test.left.id == "__name__"
            for node in ast.walk(source_tree)
        )
        assert has_main_guard, "Не найден блок if __name__ == '__main__'"

    def test_updater_app_class_exists(self, source_tree):
        """Класс UpdaterApp объявлен и содержит ключевые методы."""
        classes = {n.name: n for n in source_tree.body if isinstance(n, ast.ClassDef)}
        assert "UpdaterApp" in classes
        methods = {f.name for f in classes["UpdaterApp"].body if isinstance(f, ast.FunctionDef)}
        required = {
            "load_settings", "save_settings", "read_excel_file",
            "check_devices", "check_versions", "check_configs",
            "run_updates", "update_firmware", "update_configuration",
            "update_target_file", "reboot_devices_operation",
            "upload_files_to_devices", "collect_device_info",
            "send_command_to_devices", "restart_ppo_operation",
            "check_duplicate_macs", "stop_updates",
        }
        missing = required - methods
        assert not missing, f"Отсутствуют методы UpdaterApp: {missing}"

    def test_progress_tracker_thread_safe(self, source_tree):
        """ProgressTracker использует блокировку при инкременте счётчика."""
        classes = {n.name: n for n in source_tree.body if isinstance(n, ast.ClassDef)}
        assert "ProgressTracker" in classes
        src = ast.dump(classes["ProgressTracker"])
        assert "Lock" in src or "lock" in src.lower()

    def test_icon_file_present(self):
        """Файл иконки для сборки EXE присутствует в репозитории."""
        icon = os.path.join(ROOT, "multiservise.ico")
        assert os.path.isfile(icon)
        with open(icon, "rb") as f:
            header = f.read(4)
        assert header[:2] == b"\x00\x00" and header[2:4] == b"\x01\x00", \
            "multiservise.ico не является корректным ICO-файлом"


# ---------------------------------------------------------------------------
# Инфраструктура сборки
# ---------------------------------------------------------------------------

class TestBuildConfig:
    def test_requirements_cover_imports(self, source_text):
        """requirements.txt покрывает сторонние импорты приложения."""
        req_path = os.path.join(ROOT, "requirements.txt")
        assert os.path.isfile(req_path)
        with open(req_path, encoding="utf-8") as f:
            reqs = f.read().lower()
        tree = ast.parse(source_text)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        stdlib_or_tk = {"os", "tkinter", "threading", "logging", "queue", "json",
                        "time", "datetime", "webbrowser", "concurrent", "collections",
                        "socket", "re", "sys"}
        third_party = imported - stdlib_or_tk
        mapping = {"pandas": "pandas", "paramiko": "paramiko", "openpyxl": "openpyxl"}
        for mod in third_party:
            key = mapping.get(mod, mod)
            assert key in reqs, f"Зависимость '{mod}' не найдена в requirements.txt"

    def test_build_spec_uses_icon_and_version(self):
        """build.spec использует .ico-иконку и версию из version.py."""
        spec_path = os.path.join(ROOT, "build.spec")
        assert os.path.isfile(spec_path)
        with open(spec_path, encoding="utf-8") as f:
            spec = f.read()
        assert "multiservise.ico" in spec, "EXE не будет использовать иконку .ico"
        assert "APP_VERSION" in spec, "build.spec не берёт версию из version.py"
        assert "console=False" in spec, "Сборка должна быть windowed (без консоли)"

    def test_workflows_exist_and_valid_yaml(self):
        """Workflow-файлы присутствуют и парсятся как YAML."""
        wf_dir = os.path.join(ROOT, ".github", "workflows")
        files = sorted(os.listdir(wf_dir))
        assert "release.yml" in files and "test.yml" in files
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml не установлен")
        for fn in files:
            with open(os.path.join(wf_dir, fn), encoding="utf-8") as f:
                data = yaml.safe_load(f)
            assert "jobs" in data, f"{fn}: нет секции jobs"
        with open(os.path.join(wf_dir, "release.yml"), encoding="utf-8") as f:
            rel = yaml.safe_load(f)
        # триггер по тегу
        triggers = rel.get(True, rel.get("on"))
        assert "push" in triggers and "tags" in triggers["push"], \
            "release.yml должен срабатывать на push тегов"


# ---------------------------------------------------------------------------
# Функциональные unit-тесты логики приложения (изолированные фрагменты)
# ---------------------------------------------------------------------------

class TestLogicUnits:
    def test_progress_tracker_counts_and_caps(self):
        """ProgressTracker: подсчёт задач, прогресс не превышает 100%, флаг остановки."""
        # Импортируем класс из исходника без запуска GUI.
        src = find_main_source()
        module = _load_module_from_path(src)
        updates = []
        tracker = module.ProgressTracker(total_tasks=4, update_callback=updates.append)
        tracker.increment()
        tracker.increment()
        assert tracker.completed_tasks == 2
        assert updates[-1] == pytest.approx(50.0)
        tracker.increment(3)  # перебор — должно ограничиться 100%
        assert updates[-1] == pytest.approx(100.0)
        tracker.stop()
        tracker.increment()
        assert tracker.completed_tasks == 5  # после stop инкремент игнорируется

    def test_read_excel_active_filter(self, tmp_path):
        """Логика отбора активных устройств по колонке ENABLE (как в read_excel_file)."""
        pd = pytest.importorskip("pandas")
        xlsx = tmp_path / "devices.xlsx"
        df = pd.DataFrame({
            "IP": ["10.0.0.1", "10.0.0.2", "10.0.0.3"],
            "Login": ["admin", "admin", "admin"],
            "Password": ["pass", 123456, "pass"],
            "ENABLE": ["+", "no", "Y"],
            "NAME": ["MFC1", "MFC2", "ALT_SP1"],
            "ARH": ["arm", "arm", "x86"],
            "Model": ["MFC-9510", "MFC-9510", "ALT_SP"],
        })
        df.to_excel(xlsx, index=False)
        reread = pd.read_excel(xlsx)
        required = {"IP", "Login", "Password", "ENABLE", "NAME", "ARH", "Model"}
        assert required.issubset(reread.columns)
        active = reread[reread["ENABLE"].astype(str).str.strip().str.upper()
                        .isin(["+", "Y", "YES", "TRUE", "1"])]
        assert list(active["IP"]) == ["10.0.0.1", "10.0.0.3"]

    def test_is_alt_sp_detection(self):
        """Распознавание устройств ALT_SP (по Model или префиксу NAME)."""
        module = _load_module_from_path(find_main_source())
        cls = module.UpdaterApp
        checker = cls.is_alt_sp_device
        assert checker(None, {"Model": "ALT_SP", "NAME": "X"})
        assert checker(None, {"Model": "OTHER", "NAME": "alt_sp_01"})
        assert not checker(None, {"Model": "MFC-9510", "NAME": "MFC1"})


# ---------------------------------------------------------------------------
# Вспомогательное: безопасная загрузка модуля приложения без запуска mainloop
# ---------------------------------------------------------------------------

def _load_module_from_path(path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("mfc_multiservice_under_test", path)
    module = importlib.util.module_from_spec(spec)
    # Код под `if __name__ == "__main__"` не выполнится: имя модуля другое.
    spec.loader.exec_module(module)
    return module
