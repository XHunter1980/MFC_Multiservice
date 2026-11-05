# MFC_Multiservice
Утилита-инструментарий параллельной работы с множеством МФК

## Документация
- 📘 [Руководство пользователя](docs/USER_GUIDE.md) — установка, подготовка Excel-файла устройств, описание всех вкладок и типовых сценариев работы.

## Структура репозитория
| Файл / папка | Назначение |
|---|---|
| `MFC_Multiservice_9.6.py` | Основной исходный код приложения (Tkinter GUI + SSH-ядро) |
| `version.py` | Единый источник версии (`APP_VERSION`) для CI и сборки EXE |
| `build.spec` | Spec-файл PyInstaller: сборка Windows EXE с иконкой `multiservise.ico` |
| `multiservise.ico` | Иконка приложения (встраивается в EXE) |
| `requirements.txt` | Зависимости (pandas, openpyxl, paramiko, pytest, pyinstaller) |
| `tests/`, `pytest.ini` | Автотесты (pytest) |
| `.github/workflows/test.yml` | CI: автотесты на каждый push/PR |
| `.github/workflows/release.yml` | CI: сборка EXE и публикация GitHub Release по тегу `v*.*` |
| `.github/scripts/get_version.py` | Сверка версии тега ↔ `version.py` ↔ `VERSION_INFO` исходника |
| `Стенд_103_v2.xlsx` | Пример файла со списком устройств |

## Разработка и тесты
```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

## Сборка EXE (Windows)
```bash
pyinstaller build.spec --noconfirm   # dist\MFC_Multiservice_v<версия>.exe
```

## Релизы
При изменении версии исходника:
1. Обновите `APP_VERSION` в `version.py` (должна совпадать с `UpdaterApp.VERSION_INFO`).
2. Создайте тег и отправьте: `git tag v9.6 && git push origin v9.6`.

GitHub Actions проверит согласованность версий, прогонит автотесты, соберёт Windows EXE (с иконкой `multiservise.ico`) и опубликует релиз. Версия релиза всегда берётся из версии исходника.
