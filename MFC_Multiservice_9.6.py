import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import pandas as pd
import threading
import logging
from logging.handlers import RotatingFileHandler
from queue import Queue
import paramiko
import json
import time
from datetime import datetime
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
import socket


class ProgressTracker:
    def __init__(self, total_tasks, update_callback):
        self.total_tasks = total_tasks
        self.update_callback = update_callback
        self.completed_tasks = 0
        self.lock = threading.Lock()
        self.stopped = False  # Добавляем флаг остановки

    def increment(self, value=1):
        """Увеличивает счетчик выполненных задач и обновляет прогресс"""
        with self.lock:
            if self.stopped:
                return
            self.completed_tasks += value
            progress = min(100, (self.completed_tasks / self.total_tasks) * 100)
            self.update_callback(progress)

    def stop(self):
        """Останавливает обновление прогресса"""
        with self.lock:
            self.stopped = True


class UpdaterApp:
    """Основной класс приложения для управления устройствами"""
    CONFIG_FILE = "updater_settings.json"
    VERSION_INFO = "МФК Мультисервис v9.6"
    HISTORY_LIMIT = 50
    MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB
    MAX_LOG_BACKUPS = 3
    SSH_PORT = 22
    CONNECT_TIMEOUT = 10
    ALT_SP_TIMEOUT = 30  # Увеличенный таймаут для ALT_SP

    def __init__(self, root):
        """Инициализация главного окна приложения"""
        self.root = root
        self.root.title(self.VERSION_INFO)
        self.root.geometry("450x700")
        self.root.resizable(True, True)

        self.settings = {}
        self.devices = []  # Список устройств
        self.device_info = []  # Собранная информация об устройствах
        self.setup_logging()
        self.load_settings()

        # Основной контейнер для виджетов
        self.main_container = ttk.Frame(self.root, padding="5")
        self.main_container.pack(fill=tk.BOTH, expand=True)
        self.main_container.columnconfigure(0, weight=1)
        self.main_container.rowconfigure(3, weight=1)

        # Очередь для логов и флаги состояния
        self.log_queue = Queue()
        self.check_in_progress = False
        self.selected_devices = []
        self.thread_pool = ThreadPoolExecutor(max_workers=10)
        self.retry_attempts = 3
        self.base_timeout = 5
        self.running = False
        self.stop_event = threading.Event()

        # История путей и команд
        self.remote_path_history = deque(maxlen=self.HISTORY_LIMIT)
        self.command_history = deque(maxlen=self.HISTORY_LIMIT)

        # Загрузка истории из настроек
        if 'remote_path_history' in self.settings:
            self.remote_path_history = deque(self.settings['remote_path_history'], maxlen=self.HISTORY_LIMIT)
        if 'command_history' in self.settings:
            self.command_history = deque(self.settings['command_history'], maxlen=self.HISTORY_LIMIT)

        self.setup_theme()
        self.create_widgets()
        self.monitor_log_queue()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.blinking = False  # Флаг для мигания прогресс-бара
        self.ALT_SU_PASSWORD = self.su_password.get() #Пароль SU для устройств ALT_SP берется из поля su GUI

        # Добавляем отслеживание изменения состояния тестового режима
        self.dry_run.trace_add('write', lambda *args: self.update_test_mode_label())

    def is_alt_sp_device(self, device):
        """Проверка, является ли устройство ALT_SP"""
        return device.get('Model') == 'ALT_SP' or device.get('NAME', '').upper().startswith('ALT_SP')

    def setup_logging(self):
        """Настройка системы логирования"""
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)

        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        # Обработчик для вывода в консоль
        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        self.logger.addHandler(ch)

        # Обработчик для записи в файл с ротацией
        if 'log_dir' in self.settings and self.settings.get('log_enabled', True):
            log_file = os.path.join(self.settings['log_dir'], 'updater.log')
            fh = RotatingFileHandler(
                log_file,
                maxBytes=self.MAX_LOG_SIZE,
                backupCount=self.MAX_LOG_BACKUPS
            )
            fh.setFormatter(formatter)
            self.logger.addHandler(fh)

    def load_settings(self):
        """Загрузка настроек из файла конфигурации"""
        self.settings = {}
        try:
            if os.path.exists(self.CONFIG_FILE):
                with open(self.CONFIG_FILE, 'r') as f:
                    self.settings = json.load(f)

                    # Очистка несуществующих путей
                    for path_key in ['firmware_dir', 'config_file',
                                     'target_file', 'log_dir', 'upload_dir']:
                        if path_key in self.settings and self.settings[path_key]:
                            if not os.path.exists(self.settings[path_key]):
                                self.settings[path_key] = ""

                    # Всегда очищаем путь к файлу Excel при запуске
                    if 'excel_path' in self.settings:
                        self.settings['excel_path'] = ""

        except Exception as e:
            self.logger.error(f"Ошибка загрузки настроек: {str(e)}")

    def save_settings(self):
        """Сохранение текущих настроек в файл"""
        try:
            self.settings = {
                'excel_path': self.excel_path.get(),
                'firmware_update': self.firmware_update.get(),
                'firmware_dir': self.firmware_dir.get(),
                'config_update': self.config_update.get(),
                'config_file': self.config_file.get(),
                'target_update': self.target_update.get(),
                'target_file': self.target_file.get(),
                'log_enabled': self.log_enabled.get(),
                'log_dir': self.log_dir.get(),
                'check_firmware': self.check_firmware.get(),
                'check_config': self.check_config.get(),
                'reboot_devices': self.reboot_devices.get(),
                'upload_dir': self.upload_dir.get(),
                'remote_path': self.remote_path.get(),
                'command_text': self.command_text.get(),
                'remote_path_history': list(self.remote_path_history),
                'command_history': list(self.command_history),
                'use_su': self.use_su.get(),
                'su_password': self.su_password.get()
            }

            with open(self.CONFIG_FILE, 'w') as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            self.logger.error(f"Ошибка сохранения настроек: {str(e)}")

    def setup_theme(self):
        """Настройка цветовой темы приложения"""
        self.style = ttk.Style()
        self.style.theme_use('clam')

        # Цветовая палитра
        self.bg_color = '#f0f0f0'
        self.fg_color = '#333333'
        self.accent_color = '#4a6baf'
        self.success_color = '#2e7d32'
        self.error_color = '#c62828'
        self.warning_color = '#f9a825'
        self.info_color = '#1565c0'
        self.version_color = '#6a1b9a'
        self.reboot_color = '#006064'
        self.check_color = '#5d4037'
        self.collect_color = '#ff4f00'
        self.upload_color = '#9400d3'
        self.command_color = '#013220'
        self.message_color = '#002137'

        # Базовые стили
        self.style.configure('.', background=self.bg_color, foreground=self.fg_color)
        self.style.configure('TFrame', background=self.bg_color)
        self.style.configure('TLabelframe', background=self.bg_color)
        self.style.configure('TLabel', background=self.bg_color)
        self.style.configure('TButton', padding=4)
        self.style.configure('TCheckbutton', background=self.bg_color)
        self.style.configure('Status.TLabel', font=('Segoe UI', 9, 'bold'))

        # Стили для кнопок
        self.style.configure('Run.TButton', foreground='white', background=self.accent_color)
        self.style.configure('Stop.TButton', foreground='white', background=self.error_color)
        self.style.configure('Reboot.TButton', foreground='white', background=self.reboot_color)
        self.style.configure('Check.TButton', foreground='white', background=self.check_color)
        self.style.configure('Collect.TButton', foreground='white', background=self.collect_color)
        self.style.configure('Upload.TButton', foreground='white', background=self.upload_color)
        self.style.configure('Command.TButton', foreground='white', background=self.command_color)

        # Стиль для прогресс-бара (базовый)
        self.style.configure("Green.Horizontal.TProgressbar",
                             troughcolor=self.bg_color,
                             background='#00c853')  # Начальный цвет - зеленый

    def create_widgets(self):
        """Создание всех виджетов интерфейса"""
        # Фрейм для выбора файла с устройствами
        file_frame = ttk.LabelFrame(self.main_container, text="Список устройств", padding=5)
        file_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        file_frame.columnconfigure(1, weight=1)

        ttk.Label(file_frame, text="Файл Excel:").grid(row=0, column=0, sticky="w")
        self.excel_path = tk.StringVar(value=self.settings.get('excel_path', ''))
        ttk.Entry(file_frame, textvariable=self.excel_path).grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(file_frame, text="Обзор", command=self.browse_excel).grid(row=0, column=2, sticky="e")
        ttk.Button(file_frame, text="Выбрать", command=self.show_device_selection_dialog).grid(row=0, column=3,
                                                                                               sticky="e")

        # Вкладки приложения
        self.tab_control = ttk.Notebook(self.main_container)
        self.tab_control.grid(row=1, column=0, sticky="nsew", pady=(0, 5))

        self.create_main_tab()  # Вкладка обновлений
        self.create_checks_tab()  # Вкладка операций
        self.create_commands_tab()  # Вкладка команд
        self.create_settings_tab()  # Вкладка настроек
        self.create_about_tab()  # Вкладка "О программе"

        # Фрейм прогресса
        progress_frame = ttk.LabelFrame(self.main_container, text="Прогресс", padding=5)
        progress_frame.grid(row=2, column=0, sticky="ew", pady=(0, 5))

        progress_bar_frame = ttk.Frame(progress_frame)
        progress_bar_frame.pack(fill=tk.X, expand=True, padx=5, pady=5)

        self.progress = ttk.Progressbar(progress_bar_frame, style="Green.Horizontal.TProgressbar")
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.progress_percent = tk.StringVar(value="0%")
        ttk.Label(progress_bar_frame, textvariable=self.progress_percent, width=5).pack(side=tk.RIGHT, padx=5)

        self.current_operation = tk.StringVar(value="Готов к работе")
        ttk.Label(progress_frame, textvariable=self.current_operation, style='Status.TLabel').pack(anchor=tk.W, padx=5)

        # Фрейм лога операций
        log_frame = ttk.LabelFrame(self.main_container, text="Журнал операций", padding=5)
        log_frame.grid(row=3, column=0, sticky="nsew")

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            wrap=tk.WORD,
            state='disabled',
            font=('Consolas', 9),
            padx=5,
            pady=5,
            height=10
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Создаем фрейм для кнопки "Сброс и очистка" и метки тестового режима
        bottom_frame = ttk.Frame(self.main_container)
        bottom_frame.grid(row=4, column=0, sticky="ew", pady=(5, 0))

        # Метка тестового режима (слева)
        self.test_mode_label = ttk.Label(
            bottom_frame,
            text="ТЕСТОВЫЙ РЕЖИМ!",
            foreground='#ff8c00',  # Темно-оранжевый цвет
            font=('Segoe UI', 9, 'bold')
        )
        self.test_mode_label.pack(side=tk.LEFT, padx=5)
        self.test_mode_label.pack_forget()  # Скрываем по умолчанию

        # Кнопка "Сброс и очистка" (справа)
        ttk.Button(
            bottom_frame,
            text="Сброс и очистка",
            command=self.clear_log,
            style='Accent.TButton'
        ).pack(side=tk.RIGHT)

        # Настройка тегов для цветного вывода в лог
        self.log_text.tag_config('error', foreground=self.error_color)
        self.log_text.tag_config('success', foreground=self.success_color)
        self.log_text.tag_config('warning', foreground=self.warning_color)
        self.log_text.tag_config('info', foreground=self.info_color)
        self.log_text.tag_config('version', foreground=self.version_color)
        self.log_text.tag_config('unavailable', foreground=self.error_color, font=('Consolas', 9, 'bold'))
        self.log_text.tag_config('config', foreground='#7b1fa2')
        self.log_text.tag_config('reboot', foreground=self.reboot_color)
        self.log_text.tag_config('upload', foreground=self.upload_color)
        self.log_text.tag_config('command', foreground=self.command_color)
        self.log_text.tag_config('message', foreground=self.message_color)

    def create_main_tab(self):
        """Создание вкладки 'Обновление'"""
        self.tab_main = ttk.Frame(self.tab_control)
        self.tab_control.add(self.tab_main, text="Обновление")

        # Фрейм с кнопками действий
        btn_frame = ttk.Frame(self.tab_main)
        btn_frame.pack(fill=tk.X, pady=5)

        ttk.Button(btn_frame, text="Выполнить обновления", command=self.start_update_thread,
                   style='Run.TButton').pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        ttk.Button(btn_frame, text="Остановить", command=self.stop_updates,
                   style='Stop.TButton').pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        ttk.Button(btn_frame, text="Перезагрузить", command=self.start_reboot_thread,
                   style='Reboot.TButton').pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)

        # Фрейм выбора типов обновлений
        options_frame = ttk.LabelFrame(self.tab_main, text="Выбор обновлений", padding=5)
        options_frame.pack(fill=tk.X, pady=5)

        cb_frame = ttk.Frame(options_frame)
        cb_frame.pack(fill=tk.X)

        self.firmware_update = tk.BooleanVar(value=self.settings.get('firmware_update', False))
        self.config_update = tk.BooleanVar(value=self.settings.get('config_update', False))
        self.target_update = tk.BooleanVar(value=self.settings.get('target_update', False))

        ttk.Checkbutton(cb_frame, text="Прошивка", variable=self.firmware_update).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(cb_frame, text="Конфигурация", variable=self.config_update).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(cb_frame, text="Target2", variable=self.target_update).pack(side=tk.LEFT, padx=5)

        # Фреймы для выбора файлов
        file_frame = ttk.Frame(options_frame)
        file_frame.pack(fill=tk.X, pady=5)

        # Прошивка
        firmware_frame = ttk.Frame(file_frame)
        firmware_frame.pack(fill=tk.X, pady=2)
        ttk.Label(firmware_frame, text="Директория прошивки:").pack(side=tk.LEFT)
        self.firmware_dir = tk.StringVar(value=self.settings.get('firmware_dir', ''))
        ttk.Entry(firmware_frame, textvariable=self.firmware_dir).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(firmware_frame, text="...", command=lambda: self.browse_directory(self.firmware_dir),
                   width=3).pack(side=tk.LEFT)

        # Конфигурация
        config_frame = ttk.Frame(file_frame)
        config_frame.pack(fill=tk.X, pady=2)
        ttk.Label(config_frame, text="Файл конфигурации:").pack(side=tk.LEFT)
        self.config_file = tk.StringVar(value=self.settings.get('config_file', ''))
        ttk.Entry(config_frame, textvariable=self.config_file).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(config_frame, text="...",
                   command=lambda: self.browse_file(self.config_file, [("XML files", "*.xml")]),
                   width=3).pack(side=tk.LEFT)

        # Target2
        target_frame = ttk.Frame(file_frame)
        target_frame.pack(fill=tk.X, pady=2)
        ttk.Label(target_frame, text="Файл target2:").pack(side=tk.LEFT)
        self.target_file = tk.StringVar(value=self.settings.get('target_file', ''))
        ttk.Entry(target_frame, textvariable=self.target_file).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(target_frame, text="...",
                   command=lambda: self.browse_file(self.target_file, [("XML files", "*.xml")]),
                   width=3).pack(side=tk.LEFT)

    def create_checks_tab(self):
        """Создание вкладки 'Операции'"""
        self.tab_checks = ttk.Frame(self.tab_control)
        self.tab_control.add(self.tab_checks, text="Операции")

        # Кнопки проверок
        btn_frame = ttk.Frame(self.tab_checks)
        btn_frame.pack(fill=tk.X, pady=5)

        ttk.Button(btn_frame, text="Проверить устройства", command=self.start_device_check_thread,
                   style='Check.TButton').pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        ttk.Button(btn_frame, text="Проверить прошивки", command=self.start_version_check_thread,
                   style='Check.TButton').pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        ttk.Button(btn_frame, text="Проверить конфиги", command=self.start_config_check_thread,
                   style='Check.TButton').pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)

        # Кнопки сбора информации
        collect_frame = ttk.Frame(self.tab_checks)
        collect_frame.pack(fill=tk.X, pady=5)

        ttk.Button(collect_frame, text="Собрать информацию", command=self.start_collect_info_thread,
                   style='Collect.TButton').pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        ttk.Button(collect_frame, text="Проверить дубликаты MAC",
                   command=self.check_duplicate_macs,
                   style='Collect.TButton').pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)

        # Фрейм загрузки файлов
        upload_frame = ttk.LabelFrame(self.tab_checks, text="Загрузка файлов на устройства", padding=5)
        upload_frame.pack(fill=tk.X, pady=5)

        # Локальная директория
        dir_frame = ttk.Frame(upload_frame)
        dir_frame.pack(fill=tk.X, pady=2)
        ttk.Label(dir_frame, text="Локальная директория:").pack(side=tk.LEFT)
        self.upload_dir = tk.StringVar(value=self.settings.get('upload_dir', ''))
        ttk.Entry(dir_frame, textvariable=self.upload_dir).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(dir_frame, text="...", command=lambda: self.browse_directory(self.upload_dir),
                   width=3).pack(side=tk.LEFT)

        # Удаленный путь
        remote_frame = ttk.Frame(upload_frame)
        remote_frame.pack(fill=tk.X, pady=2)
        ttk.Label(remote_frame, text="Удаленный путь:").pack(side=tk.LEFT)
        self.remote_path = tk.StringVar(value=self.settings.get('remote_path', '/tmp/'))
        self.remote_path_cb = ttk.Combobox(remote_frame, textvariable=self.remote_path)
        self.remote_path_cb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.remote_path_cb['values'] = list(self.remote_path_history)
        ttk.Button(remote_frame, text="+",
                   command=lambda: self.add_to_history(self.remote_path.get(), self.remote_path_history,
                                                       self.remote_path_cb),
                   width=3).pack(side=tk.LEFT)

        # Кнопки загрузки и остановки
        upload_btn_frame = ttk.Frame(upload_frame)
        upload_btn_frame.pack(fill=tk.X, pady=5)

        ttk.Button(upload_btn_frame, text="Загрузить файлы в устройства", command=self.start_upload_files_thread,
                   style='Upload.TButton').pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        ttk.Button(upload_btn_frame, text="Остановить", command=self.stop_updates,
                   style='Stop.TButton').pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)

    def create_settings_tab(self):
        """Создание вкладки 'Настройки'"""
        self.tab_settings = ttk.Frame(self.tab_control)
        self.tab_control.add(self.tab_settings, text="Настройки")

        # Фрейм опций проверок
        check_frame = ttk.LabelFrame(self.tab_settings, text="Опции проверок", padding=5)
        check_frame.pack(fill=tk.X, pady=5)

        self.check_firmware = tk.BooleanVar(value=self.settings.get('check_firmware', True))
        self.check_config = tk.BooleanVar(value=self.settings.get('check_config', True))

        ttk.Checkbutton(check_frame, text="Проверять версию прошивки", variable=self.check_firmware).pack(anchor=tk.W)
        ttk.Checkbutton(check_frame, text="Проверять версию конфига", variable=self.check_config).pack(anchor=tk.W)

        # Фрейм других настроек
        settings_frame = ttk.LabelFrame(self.tab_settings, text="Другие настройки", padding=5)
        settings_frame.pack(fill=tk.X, pady=5)

        self.dry_run = tk.BooleanVar(value=self.settings.get('dry_run', False))
        self.log_enabled = tk.BooleanVar(value=self.settings.get('log_enabled', True))
        self.reboot_devices = tk.BooleanVar(value=self.settings.get('reboot_devices', False))

        ttk.Checkbutton(settings_frame, text="Тестовый режим", variable=self.dry_run).pack(anchor=tk.W)
        ttk.Checkbutton(settings_frame, text="Вести лог", variable=self.log_enabled).pack(anchor=tk.W)
        ttk.Checkbutton(settings_frame, text="Перезагружать устройства", variable=self.reboot_devices).pack(anchor=tk.W)

        # Директория логов
        log_frame = ttk.Frame(settings_frame)
        log_frame.pack(fill=tk.X, pady=2)
        ttk.Label(log_frame, text="Директория логов:").pack(side=tk.LEFT)
        self.log_dir = tk.StringVar(value=self.settings.get('log_dir', ''))
        ttk.Entry(log_frame, textvariable=self.log_dir).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(log_frame, text="...", command=lambda: self.browse_directory(self.log_dir),
                   width=3).pack(side=tk.LEFT)

    def create_commands_tab(self):
        """Создание вкладки 'Команды'"""
        self.tab_commands = ttk.Frame(self.tab_control)
        self.tab_control.add(self.tab_commands, text="Команды")

        # Фрейм для ввода команд
        command_frame = ttk.LabelFrame(self.tab_commands, text="Консольная команда", padding=5)
        command_frame.pack(fill=tk.X, pady=5)

        self.command_text = tk.StringVar(value=self.settings.get('command_text', ''))
        self.command_cb = ttk.Combobox(command_frame, textvariable=self.command_text)
        self.command_cb.pack(fill=tk.X, padx=5, pady=2)
        self.command_cb['values'] = list(self.command_history)

        # Фрейм для настроек SU
        su_frame = ttk.Frame(command_frame)
        su_frame.pack(fill=tk.X, pady=5)

        self.use_su = tk.BooleanVar(value=self.settings.get('use_su', False))
        ttk.Checkbutton(su_frame, text="Суперпользователь", variable=self.use_su).pack(side=tk.LEFT, padx=5)

        ttk.Label(su_frame, text="Пароль SU:").pack(side=tk.LEFT, padx=5)
        self.su_password = tk.StringVar(value=self.settings.get('su_password', ''))
        ttk.Entry(su_frame, textvariable=self.su_password, show="*").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        ttk.Button(su_frame, text="+",
                   command=lambda: self.add_to_history(self.command_text.get(), self.command_history, self.command_cb),
                   width=0).pack(side=tk.RIGHT)

        # Кнопки отправки команды и остановки
        command_btn_frame = ttk.Frame(command_frame)
        command_btn_frame.pack(fill=tk.X, pady=5)

        ttk.Button(command_btn_frame, text="Послать консольную команду", command=self.start_send_command_thread,
                   style='Command.TButton').pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        ttk.Button(command_btn_frame, text="Остановить", command=self.stop_updates,
                   style='Stop.TButton').pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)

        # Новая кнопка для рестарта ППО
        restart_btn_frame = ttk.Frame(command_frame)
        restart_btn_frame.pack(fill=tk.X, pady=5)

        # Создаем новый стиль для кнопки рестарта
        self.style.configure('Restart.TButton', foreground='white', background='#1e88e5')  # Синеватый цвет
        ttk.Button(restart_btn_frame, text="Рестарт ППО", command=self.restart_ppo,
                   style='Restart.TButton').pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)

    def create_about_tab(self):
        """Создание вкладки 'О программе'"""
        self.tab_about = ttk.Frame(self.tab_control)
        self.tab_control.add(self.tab_about, text="О программе")

        about_frame = ttk.Frame(self.tab_about)
        about_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Заголовок
        header_frame = ttk.Frame(about_frame)
        header_frame.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(
            header_frame,
            text=self.VERSION_INFO,
            font=('Segoe UI', 14, 'bold'),
            foreground='#4a6baf'
        ).pack(side=tk.LEFT)

        # Описание возможностей
        desc_text = "• Обновление прошивки и конфигурации\n" \
                    "• Мониторинг состояния устройств\n" \
                    "• Контроль версий ППО и конфигурации\n" \
                    "• Загрузка файлов и удаленное выполнение команд \n" \
                    "• Сбор информации и распознавание конфликтов MAC"

        ttk.Label(
            about_frame,
            text=desc_text,
            justify=tk.LEFT,
            font=('Segoe UI', 9)
        ).pack(anchor=tk.W, pady=(0, 10))

        # Контактная информация
        contact_frame = ttk.Frame(about_frame)
        contact_frame.pack(fill=tk.X, pady=(0, 5))

        dev_frame = ttk.Frame(contact_frame)
        dev_frame.pack(fill=tk.X, pady=1)

        ttk.Label(
            dev_frame,
            text="Разработчик:",
            width=12,
            anchor=tk.W,
            font=('Segoe UI', 9)
        ).pack(side=tk.LEFT)

        ttk.Label(
            dev_frame,
            text="Екимов Д.А.",
            foreground='#4a6baf',
            font=('Segoe UI', 9)
        ).pack(side=tk.LEFT)

        company_frame = ttk.Frame(contact_frame)
        company_frame.pack(fill=tk.X, pady=1)

        ttk.Label(
            company_frame,
            text="Компания:",
            width=12,
            anchor=tk.W,
            font=('Segoe UI', 9)
        ).pack(side=tk.LEFT)

        ttk.Label(
            company_frame,
            text="ИскраТехно",
            foreground='#4a6baf',
            font=('Segoe UI', 9)
        ).pack(side=tk.LEFT)

        site_frame = ttk.Frame(contact_frame)
        site_frame.pack(fill=tk.X, pady=1)

        ttk.Label(
            site_frame,
            text="Сайт:",
            width=12,
            anchor=tk.W,
            font=('Segoe UI', 9)
        ).pack(side=tk.LEFT)

        site_link = ttk.Label(
            site_frame,
            text="iskratechno.ru",
            foreground='#00A0E1',
            cursor="hand2",
            font=('Segoe UI', 9)
        )
        site_link.pack(side=tk.LEFT)
        site_link.bind("<Button-1>", lambda e: webbrowser.open("https://iskratechno.ru/"))

        ttk.Label(
            about_frame,
            text="© 2025 ИскраТехно",
            font=('Segoe UI', 7),
            foreground='#666666'
        ).pack(side=tk.BOTTOM, pady=(5, 0))

    def show_device_selection_dialog(self):
        """Отображение диалога выбора устройств"""
        if not self.devices:
            messagebox.showwarning("Предупреждение", "Сначала загрузите список устройств")
            return

        selection_window = tk.Toplevel(self.root)
        selection_window.title("Выбор устройств")
        selection_window.geometry("700x400")

        frame = ttk.Frame(selection_window)
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Таблица устройств
        tree = ttk.Treeview(frame, columns=("selected", "name", "ip", "login", "arh"), show="headings")
        tree.heading("selected", text="Выбрано")
        tree.heading("name", text="Имя")
        tree.heading("ip", text="IP-адрес")
        tree.heading("login", text="Логин")
        tree.heading("arh", text="Архитектура")

        tree.column("selected", width=80, anchor=tk.CENTER)
        tree.column("name", width=150, anchor=tk.W)
        tree.column("ip", width=150, anchor=tk.W)
        tree.column("login", width=150, anchor=tk.W)
        tree.column("arh", width=150, anchor=tk.W)

        # Стилизация таблицы
        style = ttk.Style()
        style.configure("Treeview",
                        background="#ffffff",
                        fieldbackground="#ffffff",
                        foreground="#333333",
                        rowheight=25)
        style.configure("Treeview.Heading",
                        background=self.accent_color,
                        foreground="white",
                        font=('Segoe UI', 9, 'bold'))
        style.map("Treeview",
                  background=[('selected', self.accent_color)],
                  foreground=[('selected', 'white')])

        # Заполнение таблицы устройствами
        for device in self.devices:
            selected = "✓" if device.get('Selected', True) else ""
            name = device.get('NAME', 'N/A')
            arh = device.get('ARH', 'N/A')
            tree.insert("", tk.END, values=(selected, name, device['IP'], device['Login'], arh))

        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(fill=tk.BOTH, expand=True)

        def toggle_selection(event):
            """Обработчик клика по строке для выбора/снятия выбора устройства"""
            item = tree.identify_row(event.y)
            col = tree.identify_column(event.x)

            if col == "#1":
                values = list(tree.item(item, 'values'))
                idx = tree.index(item)

                if values[0] == "✓":
                    values[0] = ""
                    self.devices[idx]['Selected'] = False
                else:
                    values[0] = "✓"
                    self.devices[idx]['Selected'] = True

                tree.item(item, values=values)

        tree.bind("<Button-1>", toggle_selection)

        # Кнопки управления выбором
        btn_frame = ttk.Frame(selection_window)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(btn_frame, text="Выбрать все",
                   command=lambda: self.toggle_all_devices(tree, True)).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Снять все",
                   command=lambda: self.toggle_all_devices(tree, False)).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Применить",
                   command=lambda: self.apply_device_selection(selection_window)).pack(side=tk.RIGHT)

    def toggle_all_devices(self, tree, select):
        """Выбрать/снять все устройства"""
        for i, device in enumerate(self.devices):
            device['Selected'] = select
            values = list(tree.item(tree.get_children()[i], 'values'))
            values[0] = "✓" if select else ""
            tree.item(tree.get_children()[i], values=values)

    def apply_device_selection(self, window):
        """Применить выбранные устройства"""
        selected_count = sum(1 for device in self.devices if device.get('Selected', True))
        self.log_to_gui(f"Выбрано {selected_count} устройств для операций", 'info')
        window.destroy()

    def get_selected_devices(self):
        """Получить список выбранных устройств"""
        return [device for device in self.devices if device.get('Selected', True)]

    def validate_architecture(self, devices):
        """Проверка архитектуры устройств с компактным диалоговым окном"""
        if not devices:
            return True

        architectures = {device.get('ARH', '').strip() for device in devices if device.get('Selected', True)}
        if len(architectures) <= 1:
            return True

        # Создаем и настраиваем диалоговое окно
        dialog = tk.Toplevel(self.root)
        dialog.title("Предупреждение")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        # Устанавливаем минимальные размеры
        dialog.minsize(300, 150)

        # Позиционируем по центру главного окна
        self.root.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 150
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 75
        dialog.geometry(f"+{x}+{y}")

        # Основное содержимое
        main_frame = ttk.Frame(dialog, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Сообщение
        message = (
                "Обнаружены разные архитектуры:\n" +
                "\n".join(f"- {arch if arch else 'Не указана'}" for arch in architectures) +
                "\n\nПродолжить операцию?"
        )

        msg_label = ttk.Label(main_frame, text=message, wraplength=280, justify=tk.LEFT)
        msg_label.pack(pady=(0, 10))

        # Фрейм для кнопок
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack()

        # Переменная для результата
        self._dialog_result = False

        def set_result(value):
            self._dialog_result = value
            dialog.destroy()

        # Кнопки
        ttk.Button(btn_frame, text="Да", command=lambda: set_result(True), width=8).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Нет", command=lambda: set_result(False), width=8).pack(side=tk.LEFT, padx=5)

        # Обработка закрытия окна (эквивалентно "Нет")
        dialog.protocol("WM_DELETE_WINDOW", lambda: set_result(False))

        # Ждем закрытия окна
        dialog.wait_window()

        return self._dialog_result

    def on_close(self):
        """Обработчик закрытия приложения"""
        self.save_settings()
        self.thread_pool.shutdown(wait=False)
        self.root.destroy()

    def browse_excel(self):
        """Выбор файла Excel с устройствами"""
        filepath = filedialog.askopenfilename(
            title="Выберите файл Excel",
            filetypes=[("Файлы Excel", "*.xlsx *.xls"), ("Все файлы", "*.*")],
            initialdir=os.path.dirname(self.excel_path.get()) if self.excel_path.get() else None
        )
        if filepath:
            self.excel_path.set(filepath)
            self.read_excel_file(filepath)

    def browse_directory(self, dir_var):
        """Выбор директории"""
        dirpath = filedialog.askdirectory(
            title="Выберите директорию",
            initialdir=os.path.dirname(dir_var.get()) if dir_var.get() else None
        )
        if dirpath:
            dir_var.set(dirpath)

    def browse_file(self, file_var, filetypes):
        """Выбор файла"""
        filepath = filedialog.askopenfilename(
            title="Выберите файл",
            filetypes=filetypes,
            initialdir=os.path.dirname(file_var.get()) if file_var.get() else None
        )
        if filepath:
            file_var.set(filepath)

    def read_excel_file(self, filepath):
        """Чтение файла Excel с устройствами"""
        try:
            df = pd.read_excel(filepath)
            required_columns = {'IP', 'Login', 'Password', 'ENABLE', 'NAME', 'ARH', 'Model'}

            if not required_columns.issubset(df.columns):
                messagebox.showerror("Ошибка",
                                     "Файл Excel должен содержать колонки IP, Login, Password, NAME, ENABLE, ARH и Model")
                return

            self.devices = []
            for _, row in df.iterrows():
                if str(row['ENABLE']).strip().upper() in ['+', 'Y', 'YES', 'TRUE', '1']:
                    device = dict(row)
                    # Преобразуем пароль в строку, если он числовой
                    if isinstance(device['Password'], (int, float)):
                        device['Password'] = str(device['Password'])
                    device['Selected'] = True
                    self.devices.append(device)

            self.log_to_gui(f"Загружено {len(self.devices)} активных устройств из {filepath}", 'message')
            self.logger.info(f"Загружено {len(self.devices)} активных устройств из {filepath}")

            self.show_device_selection_dialog()

        except Exception as e:
            self.log_to_gui(f"Ошибка чтения файла Excel: {str(e)}", 'error')
            self.logger.error(f"Ошибка чтения файла Excel", exc_info=True)
            messagebox.showerror("Ошибка", f"Ошибка чтения файла Excel: {str(e)}")

    def get_target_firmware_version(self):
        """Получение целевой версии прошивки из файла"""
        if not self.firmware_dir.get():
            self.log_to_gui("Директория с прошивкой не выбрана", 'error')
            self.logger.error("Директория с прошивкой не выбрана")
            return None

        version_file = os.path.join(self.firmware_dir.get(), 'version_dev.txt')
        if not os.path.exists(version_file):
            self.log_to_gui(f"Файл версии не найден: {version_file}", 'error')
            self.logger.error(f"Файл версии не найден: {version_file}")
            return None

        try:
            with open(version_file, 'r') as f:
                version = f.read().strip()
                self.logger.info(f"Прочитана целевая версия прошивки: {version}")
                return version
        except Exception as e:
            self.log_to_gui(f"Ошибка чтения файла версии: {str(e)}", 'error')
            self.logger.error("Ошибка чтения файла версии", exc_info=True)
            return None

    def log_to_gui(self, message, msg_type='info'):
        """Логирование сообщений в GUI и файл"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        formatted_message = f"[{timestamp}] {message}"
        self.log_queue.put((formatted_message, msg_type))

        if self.log_enabled.get() and self.log_dir.get():
            log_file = os.path.join(self.log_dir.get(), 'updater.log')
            try:
                with open(log_file, 'a') as f:
                    f.write(f"{timestamp} - {msg_type.upper()} - {message}\n")
            except Exception as e:
                self.logger.error(f"Ошибка записи в лог-файл: {str(e)}")

    def monitor_log_queue(self):
        """Мониторинг очереди логов для вывода в GUI"""
        while not self.log_queue.empty():
            message, msg_type = self.log_queue.get()

            if "version mismatch" in message.lower() or "needs update" in message.lower():
                msg_type = 'error'

            self.log_text.config(state='normal')
            self.log_text.insert(tk.END, message + "\n", msg_type)
            self.log_text.see(tk.END)
            self.log_text.config(state='disabled')

        self.root.after(100, self.monitor_log_queue)

    def clear_log(self):
        """Очистка лога"""
        self.log_text.config(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state='disabled')
        # Сброс прогресс-бара
        self.progress['value'] = 0
        self.progress_percent.set("0%")
        self.stop_blinking()  # Останавливаем мигание
        self.current_operation.set("Журнал очищен,готовность")

    def update_progress(self, value):
        """Обновление прогресс-бара с изменением цвета в зависимости от процента"""
        progress = min(100, value)
        self.progress['value'] = progress
        self.progress_percent.set(f"{int(progress)}%")

        if progress == 0:
            color = '#d50000'  # Ярко-красный
        elif progress < 10:
            color = '#d50000'  # Ярко-красный
        elif progress < 20:
            color = '#ff3d00'  # Оранжево-красный
        elif progress < 30:
            color = '#ff6d00'  # Тёмно-оранжевый
        elif progress < 40:
            color = '#ff9100'  # Оранжевый
        elif progress < 50:
            color = '#ffab00'  # Золотисто-жёлтый
        elif progress < 60:
            color = '#ffc400'  # Насыщенный жёлтый
        elif progress < 70:
            color = '#ffd600'  # Лимонно-жёлтый
        elif progress < 80:
            color = '#ffe500'  # Светло-жёлтый
        elif progress < 90:
            color = '#fff200'  # Бледно-жёлтый
        elif progress < 100:
            color = '#ffff00'  # Чистый жёлтый (максимальная яркость)
        else:
            self.stop_blinking()  # Останавливаем мигание
            color = '#00c853'  # Зелёный (только для 100%)


        # Обновляем стиль прогресс-бара
        self.style.configure("Green.Horizontal.TProgressbar",
                             troughcolor=self.bg_color,
                             background=color)

        self.root.update_idletasks()

    def check_device_availability(self, ip):
        """Проверка доступности устройства по IP"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((ip, self.SSH_PORT))
            sock.close()
            return result == 0
        except Exception as e:
            self.log_to_gui(f"Ошибка проверки доступности {ip}: {str(e)}", 'error')
            return False

    def start_device_check_thread(self):
        """Запуск потока проверки устройств"""
        if self.running:
            messagebox.showwarning("Предупреждение", "Операция уже выполняется")
            return

        self.running = True
        self.stop_event.clear()
        self.current_operation.set("Проверка доступности устройств")
        thread = threading.Thread(target=self.check_devices, daemon=True)
        thread.start()

    def check_devices(self):
        """Проверка доступности устройств"""
        if not self.excel_path.get():
            messagebox.showwarning("Предупреждение", "Сначала выберите файл Excel")
            self.running = False
            return

        if not self.devices:
            messagebox.showwarning("Предупреждение", "Нет загруженных устройств")
            self.running = False
            return

        active_devices = self.get_selected_devices()
        if not active_devices:
            messagebox.showwarning("Предупреждение", "Не выбрано ни одного устройства")
            self.running = False
            return

        self.log_to_gui("=== Начало проверки доступности устройств ===", 'message')
        self.update_progress(0)
        device_count = len(active_devices)

        progress_tracker = ProgressTracker(device_count, self.update_progress)

        futures = []
        results = []

        def check_single_device(device):
            """Проверка доступности одного устройства"""
            try:
                ip = device['IP']
                name = device.get('NAME', 'N/A')
                available = self.check_device_availability(ip)
                progress_tracker.increment()
                return ip, name, available, None
            except Exception as e:
                progress_tracker.increment()
                return device['IP'], device.get('NAME', 'N/A'), False, str(e)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(check_single_device, device) for device in active_devices]

            for future in as_completed(futures):
                if not self.running or self.stop_event.is_set():
                    break

                ip, name, available, error = future.result()
                if error:
                    self.log_to_gui(f"Ошибка проверки {ip} ({name}): {error}", 'error')
                else:
                    if available:
                        msg = f"Устройство {ip} ({name}) - Доступно"
                        self.log_to_gui(msg, 'success')
                    else:
                        msg = f"Устройство {ip} ({name}) - Недоступно"
                        self.log_to_gui(msg, 'unavailable')

        self.log_to_gui("=== Проверка устройств завершена ===", 'message')
        self.running = False
        self.current_operation.set("Готов к работе")

    def start_version_check_thread(self):
        """Запуск потока проверки версий прошивки"""
        if self.running:
            messagebox.showwarning("Предупреждение", "Операция уже выполняется")
            return

        self.running = True
        self.stop_event.clear()
        self.current_operation.set("Проверка версий прошивки")
        thread = threading.Thread(target=self.check_versions, daemon=True)
        thread.start()

    def check_versions(self):
        """Проверка версий прошивки на устройствах"""
        if not self.excel_path.get():
            messagebox.showwarning("Предупреждение", "Сначала выберите файл Excel")
            self.running = False
            return

        if not self.devices:
            messagebox.showwarning("Предупреждение", "Нет загруженных устройств")
            self.running = False
            return

        active_devices = self.get_selected_devices()
        if not active_devices:
            messagebox.showwarning("Предупреждение", "Не выбрано ни одного устройства")
            self.running = False
            return

        # Получаем целевую версию только если включена проверка версий
        target_version = None
        if self.check_firmware.get():
            target_version = self.get_target_firmware_version()
            if not target_version:
                self.running = False
                return

        self.log_to_gui(f"=== Проверка версий прошивки {f'(целевая: {target_version})' if target_version else ''} ===",
                        'message')
        self.update_progress(0)
        device_count = len(active_devices)

        progress_tracker = ProgressTracker(device_count, self.update_progress)

        futures = []

        def check_single_device(device):
            """Проверка версии прошивки на одном устройстве"""
            try:
                ip = device['IP']
                name = device.get('NAME', 'N/A')
                version = None
                error = None

                if not self.check_device_availability(ip):
                    self.log_to_gui(f"Устройство {ip} ({name}) недоступно", 'unavailable')
                    progress_tracker.increment()
                    return ip, name, None, "Device unavailable"

                for attempt in range(self.retry_attempts):
                    try:
                        ssh = paramiko.SSHClient()
                        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        ssh.connect(
                            ip,
                            username=device['Login'],
                            password=device['Password'],
                            timeout=self.base_timeout * (attempt + 1),
                            look_for_keys=False,
                            allow_agent=False
                        )
                        stdin, stdout, stderr = ssh.exec_command('cat /rtsoft/release_version')
                        version = stdout.read().decode().strip()

                        progress_tracker.increment()
                        return ip, name, version, None
                    except Exception as e:
                        error = str(e)
                        if attempt == self.retry_attempts - 1:
                            raise
                    finally:
                        if 'ssh' in locals():
                            ssh.close()

            except Exception as e:
                progress_tracker.increment()
                return device['IP'], device.get('NAME', 'N/A'), None, str(e)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(check_single_device, device) for device in active_devices]

            for future in as_completed(futures):
                if not self.running or self.stop_event.is_set():
                    break

                ip, name, version, error = future.result()
                if error:
                    self.log_to_gui(f"Ошибка проверки версии для {ip} ({name}): {error}", 'error')
                else:
                    if version:
                        if target_version:
                            if version == target_version:
                                msg = f"Устройство {ip} ({name}) имеет правильную версию: {version}"
                                self.log_to_gui(msg, 'success')
                            else:
                                msg = f"Несоответствие версии на устройстве {ip} ({name}) (текущая: {version}, целевая: {target_version})"
                                self.log_to_gui(msg, 'error')
                        else:
                            msg = f"Устройство {ip} ({name}) имеет версию: {version}"
                            self.log_to_gui(msg, 'info')
                    else:
                        msg = f"Не удалось получить версию с {ip} ({name})"
                        self.log_to_gui(msg, 'error')

        self.log_to_gui("=== Проверка версий завершена ===", 'message')
        self.running = False
        self.current_operation.set("Готов к работе")

    def start_config_check_thread(self):
        """Запуск потока проверки конфигураций"""
        if self.running:
            messagebox.showwarning("Предупреждение", "Операция уже выполняется")
            return

        self.running = True
        self.stop_event.clear()
        self.current_operation.set("Проверка конфигураций")
        thread = threading.Thread(target=self.check_configs, daemon=True)
        thread.start()

    def check_configs(self):
        """Проверка конфигураций на устройствах с проверкой размера файла"""
        if not self.excel_path.get():
            messagebox.showwarning("Предупреждение", "Сначала выберите файл Excel")
            self.running = False
            return

        if not self.devices:
            messagebox.showwarning("Предупреждение", "Нет загруженных устройств")
            self.running = False
            return

        active_devices = self.get_selected_devices()
        if not active_devices:
            messagebox.showwarning("Предупреждение", "Не выбрано ни одного устройства")
            self.running = False
            return

        local_config_path = self.config_file.get()
        local_description = None
        local_size = 0

        if local_config_path:
            try:
                # Получаем размер локального файла конфигурации
                local_size = os.path.getsize(local_config_path)

                with open(local_config_path, 'r', encoding='utf-8') as f:
                    for i, line in enumerate(f):
                        if i == 1:
                            if '<Global description="' in line:
                                start = line.find('<Global description="') + len('<Global description="')
                                end = line.find('"', start)
                                local_description = line[start:end]
                            break
            except UnicodeDecodeError:
                try:
                    local_size = os.path.getsize(local_config_path)
                    with open(local_config_path, 'r', encoding='cp1251') as f:
                        for i, line in enumerate(f):
                            if i == 1:
                                if '<Global description="' in line:
                                    start = line.find('<Global description="') + len('<Global description="')
                                    end = line.find('"', start)
                                    local_description = line[start:end]
                                break
                except Exception as e:
                    self.log_to_gui(f"Ошибка чтения локального конфига: {str(e)}", 'error')
                    local_description = None

        self.log_to_gui("=== Проверка описаний и размеров конфигураций ===", 'message')
        self.update_progress(0)
        device_count = len(active_devices)

        progress_tracker = ProgressTracker(device_count, self.update_progress)

        futures = []

        def check_single_device(device):
            """Проверка конфигурации на одном устройстве"""
            try:
                ip = device['IP']
                name = device.get('NAME', 'N/A')
                remote_description = None
                remote_size = 0
                error = None

                if not self.check_device_availability(ip):
                    self.log_to_gui(f"Устройство {ip} ({name}) недоступно", 'unavailable')
                    progress_tracker.increment()
                    return ip, name, None, 0, "Device unavailable"

                for attempt in range(self.retry_attempts):
                    try:
                        ssh = paramiko.SSHClient()
                        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                        if self.is_alt_sp_device(device):
                            try:
                                ssh.connect(
                                    ip,
                                    username=device['Login'],
                                    password=device['Password'],
                                    timeout=self.ALT_SP_TIMEOUT,
                                    look_for_keys=False,
                                    allow_agent=False
                                )
                                chan = ssh.invoke_shell()
                                time.sleep(0.5)

                                if not chan.recv_ready():
                                    raise Exception("ALT_SP shell not ready")

                                chan.send('su\n')
                                time.sleep(0.5)

                                if not chan.recv_ready():
                                    raise Exception("su prompt not received")

                                chan.send(f'{self.ALT_SU_PASSWORD}\n')
                                time.sleep(0.5)

                                if not chan.recv_ready():
                                    raise Exception("su login failed")

                                # Получаем описание конфига
                                chan.send('cat /rtsoft/newcfg/config.xml | head -n 2 | tail -n 1\n')
                                time.sleep(1)
                                config_content = chan.recv(65535).decode().strip()

                                # Получаем размер файла конфига
                                chan.send('stat -c%s /rtsoft/newcfg/config.xml\n')
                                time.sleep(1)
                                size_output = chan.recv(65535).decode().strip()

                                # Обработка вывода для ALT_SP
                                # Разделяем вывод по строкам и берем только цифры
                                size_lines = size_output.split('\n')
                                size_str = ''.join(c for c in size_lines[-2] if
                                                    c.isdigit())  # Предпоследняя строка содержит размер
                                try:
                                    remote_size = int(size_str) if size_str else 0
                                except ValueError:
                                    remote_size = 0
                                    self.log_to_gui(f"Не удалось определить размер файла из вывода: {size_output}",
                                                        'warning')
                                chan.close()
                            except Exception as e:
                                raise Exception(f"ALT_SP connection failed: {str(e)}")
                        else:
                            ssh.connect(
                                ip,
                                username=device['Login'],
                                password=device['Password'],
                                timeout=self.base_timeout * (attempt + 1),
                                look_for_keys=False,
                                allow_agent=False
                            )
                            # Получаем описание конфига
                            stdin, stdout, stderr = ssh.exec_command(
                                'cat /rtsoft/newcfg/config.xml | head -n 2 | tail -n 1')
                            config_content = stdout.read().decode().strip()

                            # Получаем размер файла конфига
                            stdin, stdout, stderr = ssh.exec_command('stat -c%s /rtsoft/newcfg/config.xml')
                            size_output = stdout.read().decode().strip()
                            remote_size = int(size_output)

                        if '<Global description="' in config_content:
                            start = config_content.find('<Global description="') + len('<Global description="')
                            end = config_content.find('"', start)
                            remote_description = config_content[start:end]

                        break
                    except Exception as e:
                        error = str(e)
                        if attempt == self.retry_attempts - 1:
                            raise
                    finally:
                        if 'ssh' in locals():
                            ssh.close()

                if remote_description:
                    msg = f"Устройство {ip} ({name}) имеет описание конфигурации: {remote_description}"
                    self.log_to_gui(msg, 'info')

                if self.check_config.get() and local_description is not None and remote_description is not None:
                    if local_description == remote_description and local_size == remote_size:
                        msg = f"Совпадение описания и размера конфигурации на устройстве {ip} ({name})"
                        self.log_to_gui(msg, 'success')
                    else:
                        mismatch_msgs = []
                        if local_description != remote_description:
                            mismatch_msgs.append("описания конфигурации")
                        if local_size != remote_size:
                            mismatch_msgs.append("размера файла")

                        msg = (f"Несоответствие {' и '.join(mismatch_msgs)} на устройстве {ip} ({name})\n"
                               f"Локальное: {local_description} ({local_size} байт)\n"
                               f"Удаленное: {remote_description} ({remote_size} байт)")
                        self.log_to_gui(msg, 'error')

                progress_tracker.increment()
                return ip, name, remote_description, remote_size, error
            except Exception as e:
                progress_tracker.increment()
                return device['IP'], device.get('NAME', 'N/A'), None, 0, str(e)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(check_single_device, device) for device in active_devices]

            for future in as_completed(futures):
                if not self.running or self.stop_event.is_set():
                    break

                ip, name, remote_description, remote_size, error = future.result()
                if error:
                    self.log_to_gui(f"Ошибка проверки конфига для {ip} ({name}): {error}", 'error')

        self.log_to_gui("=== Проверка конфигураций завершена ===", 'message')
        self.running = False
        self.current_operation.set("Готов к работе")

    def start_update_thread(self):
        """Запуск потока обновлений"""
        if self.running:
            messagebox.showwarning("Предупреждение", "Операция уже выполняется")
            return

        if not self.validate_inputs():
            return

        self.running = True
        self.stop_event.clear()
        self.current_operation.set("Подготовка обновлений")
        thread = threading.Thread(target=self.run_updates, daemon=True)
        thread.start()

    def validate_inputs(self):
        """Проверка введенных данных перед обновлением"""
        if not self.excel_path.get():
            messagebox.showwarning("Предупреждение", "Сначала выберите файл Excel")
            return False

        if not any([self.firmware_update.get(), self.config_update.get(), self.target_update.get()]):
            messagebox.showwarning("Предупреждение", "Выберите хотя бы один тип обновления")
            return False

        if self.firmware_update.get() and not self.firmware_dir.get():
            messagebox.showwarning("Предупреждение", "Выберите директорию с прошивкой")
            return False

        if self.config_update.get() and not self.config_file.get():
            messagebox.showwarning("Предупреждение", "Выберите файл конфигурации")
            return False

        if self.target_update.get() and not self.target_file.get():
            messagebox.showwarning("Предупреждение", "Выберите файл target2")
            return False

        active_devices = [d for d in self.devices if d.get('Selected', True)]
        if not active_devices:
            messagebox.showwarning("Предупреждение", "Не выбрано ни одного устройства для обновления")
            return False

        # Проверка архитектуры только для обновления прошивки или target
        if any([self.firmware_update.get(), self.target_update.get()]):
            if not self.validate_architecture(active_devices):
                return False

        return True

    def run_updates(self):
        """Основной метод выполнения обновлений"""
        try:
            self.log_to_gui("=== Начало обновлений ===", 'message')
            self.update_progress(0)

            # Определение типов обновлений
            updates = []
            if self.config_update.get():
                updates.append('config')
            if self.target_update.get():
                updates.append('target')
            if self.firmware_update.get():
                updates.append('firmware')

            if not updates:
                self.log_to_gui("Не выбраны типы обновлений", 'warning')
                return

            # Фильтрация активных устройств
            active_devices = [d for d in self.devices if d.get('Selected', True) and not self.skip_device(d)]
            if not active_devices:
                self.log_to_gui("Нет выбранных устройств для обновления", 'warning')
                return

            # Расчет общего количества шагов для прогресс-бара
            total_steps = len(active_devices) * len(updates)
            if self.reboot_devices.get():
                total_steps += len(active_devices)

            progress_tracker = ProgressTracker(total_steps, self.update_progress)

            # Список устройств, требующих перезагрузки
            devices_to_reboot = []

            # Выполнение обновлений по типам
            for update_type in updates:
                if not self.running or self.stop_event.is_set():
                    break

                if update_type == 'config':
                    self.current_operation.set("Обновление конфигурации")
                    updated_devices = self.update_configuration(active_devices, progress_tracker)
                    if updated_devices:  # Проверяем, что список не None
                        devices_to_reboot.extend(updated_devices)
                elif update_type == 'target':
                    self.current_operation.set("Обновление target2")
                    updated_devices = self.update_target_file(active_devices, progress_tracker)
                    if updated_devices:  # Проверяем, что список не None
                        devices_to_reboot.extend(updated_devices)
                elif update_type == 'firmware':
                    self.current_operation.set("Обновление прошивки")
                    updated_devices = self.update_firmware(active_devices, progress_tracker)
                    if updated_devices:  # Проверяем, что список не None
                        devices_to_reboot.extend(updated_devices)

            # Перезагрузка устройств после обновлений (только тех, где было выполнено обновление)
            if (self.running and not self.stop_event.is_set() and
                    self.reboot_devices.get() and devices_to_reboot):
                self.current_operation.set("Перезагрузка устройств")
                self.reboot_devices_operation(devices_to_reboot, progress_tracker)

            self.log_to_gui("=== Все обновления завершены ===", 'message')

        except Exception as e:
            self.log_to_gui(f"Ошибка обновления: {str(e)}", 'error')
        finally:
            self.running = False
            self.current_operation.set("Готов к работе")

    def update_firmware(self, active_devices, progress_tracker):
        """Обновление прошивки на устройствах"""
        if not self.firmware_dir.get():
            self.log_to_gui("Ошибка: Директория с прошивкой не выбрана", 'error')
            return

        # Проверка необходимых файлов
        required_files = ['RemoteInstallServer', 'update.stu']
        img_files = [f for f in os.listdir(self.firmware_dir.get()) if f.endswith('.img')]

        if not img_files:
            self.log_to_gui("Ошибка: Не найдены .img файлы в директории прошивки", 'error')
            return

        required_files.extend(img_files)

        # Проверка наличия всех необходимых файлов
        missing_files = []
        for file in required_files:
            if not os.path.exists(os.path.join(self.firmware_dir.get(), file)):
                missing_files.append(file)

        if missing_files:
            self.log_to_gui(f"Ошибка: Отсутствуют необходимые файлы в директории прошивки: {', '.join(missing_files)}",
                            'error')
            return

        update_count = 0

        futures = []

        def update_single_device(device):
            """Обновление прошивки на одном устройстве"""
            nonlocal update_count
            ip = device['IP']
            name = device.get('NAME', 'N/A')
            success = False
            error = None

            try:
                # Проверка версии прошивки, если включена соответствующая опция
                if self.check_firmware.get():
                    current_version = None
                    target_version = self.get_target_firmware_version()

                    for attempt in range(self.retry_attempts):
                        try:
                            ssh = paramiko.SSHClient()
                            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                            ssh.connect(
                                device['IP'],
                                username=device['Login'],
                                password=device['Password'],
                                timeout=self.base_timeout * (attempt + 1),
                                look_for_keys=False,
                                allow_agent=False
                            )
                            stdin, stdout, stderr = ssh.exec_command('cat /rtsoft/release_version')
                            current_version = stdout.read().decode().strip()

                            if current_version and target_version and current_version == target_version:
                                msg = f"Пропуск {ip} ({name}) - уже имеет версию {target_version}"
                                self.log_to_gui(msg, 'info')
                                progress_tracker.increment()
                                return ip, name, True, None
                            break
                        except Exception as e:
                            if attempt == self.retry_attempts - 1:
                                raise
                        finally:
                            if 'ssh' in locals():
                                ssh.close()

                msg = f"Начало обновления прошивки на {ip} ({name})"
                self.log_to_gui(msg, 'info')

                if not self.dry_run.get():
                    for attempt in range(self.retry_attempts):
                        try:
                            ssh = paramiko.SSHClient()
                            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                            ssh.connect(
                                device['IP'],
                                username=device['Login'],
                                password=device['Password'],
                                timeout=30 * (attempt + 1),
                                look_for_keys=False,
                                allow_agent=False
                            )

                            # Создание временной директории
                            ssh.exec_command('mkdir -p /tmp')

                            sftp = ssh.open_sftp()

                            # Копирование файлов на устройство
                            for file in required_files:
                                local_path = os.path.join(self.firmware_dir.get(), file)
                                remote_path = f'/tmp/{file}'

                                try:
                                    sftp.put(local_path, remote_path)
                                    msg = f"Скопирован {file} на {ip} ({name})"
                                    self.log_to_gui(msg, 'info')

                                except Exception as e:
                                    msg = f"Ошибка копирования {file} на {ip} ({name}): {str(e)}"
                                    self.log_to_gui(msg, 'error')
                                    raise

                            # Установка прав на исполняемый файл
                            ssh.exec_command('chmod 755 /tmp/RemoteInstallServer')

                            # Запуск обновления
                            cmd = 'cd /tmp && ./RemoteInstallServer runst /tmp/update.stu'
                            if self.is_alt_sp_device(device):
                                chan = ssh.invoke_shell()
                                time.sleep(0.5)

                                if not chan.recv_ready():
                                    raise Exception("ALT_SP shell not ready")

                                chan.send('su\n')
                                time.sleep(0.5)

                                if not chan.recv_ready():
                                    raise Exception("su prompt not received")

                                chan.send(f'{self.ALT_SU_PASSWORD}\n')
                                time.sleep(0.5)

                                if not chan.recv_ready():
                                    raise Exception("su login failed")

                                chan.send(f'{cmd}\n')
                                msg = f"Обновление прошивки запущено на {ip} ({name}, устройство перезагрузится)"
                                self.log_to_gui(msg, 'info')
                                time.sleep(180)
                                output = chan.recv(65535).decode().strip()
                                chan.close()
                                errors = ""
                            else:
                                stdin, stdout, stderr = ssh.exec_command(cmd)
                                msg = f"Обновление прошивки запущено на {ip} ({name}, устройство перезагрузится)"
                                self.log_to_gui(msg, 'info')
                                output = stdout.read().decode().strip()
                                errors = stderr.read().decode().strip()

                            if output:
                                self.log_to_gui(f"Вывод обновления с {ip} ({name}): {output}", 'info')
                            if errors:
                                self.log_to_gui(f"Предупреждения при обновлении с {ip} ({name}): {errors}",
                                                'warning')

                            msg = f"Обновление прошивки завершено на {ip} ({name})"
                            self.log_to_gui(msg, 'success')
                            update_count += 1
                            success = True
                            break
                        except Exception as e:
                            error = str(e)
                            if attempt == self.retry_attempts - 1:
                                raise
                            time.sleep(2)

                        finally:
                            if 'ssh' in locals():
                                ssh.close()

            except Exception as e:
                error = str(e)
                msg = f"Ошибка обновления прошивки для {ip} ({name}): {error}"
                self.log_to_gui(msg, 'error')
            finally:
                progress_tracker.increment()
                return ip, name, success, error

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(update_single_device, device) for device in active_devices]

            for future in as_completed(futures):
                if not self.running or self.stop_event.is_set():
                    break

                ip, name, success, error = future.result()
                if error:
                    self.log_to_gui(f"Ошибка обновления прошивки для {ip} ({name}): {error}", 'error')

        self.log_to_gui(f"=== Обновление прошивки завершено: обновлено {update_count} из {len(active_devices)} устройств ===", 'message')

    def update_configuration(self, active_devices, progress_tracker):
        """Обновление конфигурации на устройствах с проверкой версии и размера"""
        if not self.config_file.get():
            self.log_to_gui("Ошибка: Файл конфигурации не выбран", 'error')
            return

        config_path = self.config_file.get()

        # Проверка локального файла конфигурации
        if not os.path.exists(config_path):
            self.log_to_gui(f"Ошибка: Файл конфигурации не найден: {config_path}", 'error')
            return

        # Получаем информацию о локальном файле конфигурации
        local_size = os.path.getsize(config_path)
        local_description = None

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                for i, line in enumerate(f):
                    if i == 1 and '<Global description="' in line:
                        start = line.find('<Global description="') + len('<Global description="')
                        end = line.find('"', start)
                        local_description = line[start:end]
                        break
        except UnicodeDecodeError:
            try:
                with open(config_path, 'r', encoding='cp1251') as f:
                    for i, line in enumerate(f):
                        if i == 1 and '<Global description="' in line:
                            start = line.find('<Global description="') + len('<Global description="')
                            end = line.find('"', start)
                            local_description = line[start:end]
                            break
            except Exception as e:
                self.log_to_gui(f"Ошибка чтения локального конфига: {str(e)}", 'error')
                local_description = None

        update_count = 0
        devices_updated = []  # Список устройств, на которых было выполнено обновление

        def update_single_device(device):
            """Обновление конфигурации на одном устройстве"""
            nonlocal update_count, devices_updated
            ip = device['IP']
            name = device.get('NAME', 'N/A')
            success = False
            error = None
            updated = False  # Флаг, было ли выполнено обновление

            try:
                if self.stop_event.is_set():
                    return None, None, None, "Operation stopped"
                msg = f"Проверка конфигурации на {ip} ({name})"
                self.log_to_gui(msg, 'info')

                if not self.dry_run.get():
                    for attempt in range(self.retry_attempts):
                        ssh = None
                        sftp = None
                        try:
                            ssh = paramiko.SSHClient()
                            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                            timeout = self.ALT_SP_TIMEOUT if self.is_alt_sp_device(device) else self.base_timeout * (
                                    attempt + 1)

                            ssh.connect(
                                ip,
                                username=device['Login'],
                                password=device['Password'],
                                timeout=timeout,
                                look_for_keys=False,
                                allow_agent=False
                            )

                            sftp = ssh.open_sftp()
                            temp_path = '/tmp/config_tmp.xml'
                            backup_path = '/rtsoft/newcfg/config_old.xml'
                            remote_path = '/rtsoft/newcfg/config.xml'

                            # Получаем информацию о текущей конфигурации на устройстве
                            remote_description = None
                            remote_size = 0
                            try:
                                # Получаем описание конфига
                                stdin, stdout, stderr = ssh.exec_command(
                                    'cat /rtsoft/newcfg/config.xml | head -n 2 | tail -n 1')
                                config_line = stdout.read().decode().strip()

                                # Получаем размер файла конфига
                                stdin, stdout, stderr = ssh.exec_command('stat -c%s /rtsoft/newcfg/config.xml')
                                remote_size = int(stdout.read().decode().strip())

                                if '<Global description="' in config_line:
                                    start = config_line.find('<Global description="') + len('<Global description="')
                                    end = config_line.find('"', start)
                                    remote_description = config_line[start:end]
                            except Exception as e:
                                self.log_to_gui(f"Ошибка получения информации о конфиге на {ip}: {str(e)}", 'warning')

                            # Проверяем, нужно ли обновлять конфигурацию
                            if (self.check_config.get() and
                                    local_description is not None and
                                    remote_description is not None and
                                    local_description == remote_description and
                                    local_size == remote_size):
                                msg = (f"Конфигурация на {ip} ({name}) уже актуальна (версия: {remote_description}, "
                                       f"размер: {remote_size} байт) - пропуск")
                                self.log_to_gui(msg, 'info')
                                progress_tracker.increment()
                                return ip, name, True, None, False

                            # Если проверка не требуется или конфигурация отличается - выполняем обновление
                            msg = f"Обновление конфигурации на {ip} ({name})"
                            self.log_to_gui(msg, 'info')

                            # 1. Загружаем новый файл
                            sftp.put(config_path, temp_path)

                            # 2. Заменяем файл с проверками
                            try:
                                sftp.stat(remote_path)
                                # Файл существует - делаем резервную копию
                                stdin, stdout, stderr = ssh.exec_command(
                                    f'mv {remote_path} {backup_path} && echo "BACKUP_SUCCESS" || echo "BACKUP_FAILED"')
                                result = stdout.read().decode().strip()
                                if "BACKUP_FAILED" in result:
                                    raise Exception("Не удалось создать резервную копию")

                                stdin, stdout, stderr = ssh.exec_command(
                                    f'mv {temp_path} {remote_path} && echo "MOVE_SUCCESS" || echo "MOVE_FAILED"')
                                result = stdout.read().decode().strip()
                                if "MOVE_FAILED" in result:
                                    raise Exception("Не удалось переместить новый конфиг")
                            except IOError:
                                # Оригинальный файл не существует, просто перемещаем новый
                                stdin, stdout, stderr = ssh.exec_command(
                                    f'mv {temp_path} {remote_path} && echo "MOVE_SUCCESS" || echo "MOVE_FAILED"')
                                result = stdout.read().decode().strip()
                                if "MOVE_FAILED" in result:
                                    raise Exception("Не удалось переместить новый конфиг")

                            #Записываем текущие дату и время в специальный файл
                            stdin, stdout, stderr = ssh.exec_command("date +'%d-%m-%Y %H:%M:%S.%6N' > /rtsoft/newcfg/config.time")
                            result = stdout.read().decode().strip()

                            msg = f"Конфигурация успешно обновлена на {ip} ({name})"
                            self.log_to_gui(msg, 'success')
                            update_count += 1
                            success = True
                            updated = True  # Обновление было выполнено
                            break
                        except Exception as e:
                            error = str(e)
                            if attempt == self.retry_attempts - 1:
                                raise
                            time.sleep(1)
                        finally:
                            if sftp:
                                sftp.close()
                            if ssh:
                                ssh.close()

            except Exception as e:
                error = str(e)
                self.log_to_gui(f"Ошибка обновления конфигурации для {ip} ({name}): {error}", 'error')
            finally:
                progress_tracker.increment()
                if updated:
                    devices_updated.append(device)  # Добавляем устройство в список обновленных
                return ip, name, success, error, updated

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(update_single_device, device) for device in active_devices]

            for future in as_completed(futures):
                if not self.running or self.stop_event.is_set():
                    break

                ip, name, success, error, updated = future.result()
                if error:
                    self.log_to_gui(f"Ошибка обновления конфигурации для {ip} ({name}): {error}", 'error')

        self.log_to_gui(
            f"=== Обновление конфигурации завершено: обновлено {update_count} из {len(active_devices)} устройств ===",
            'message')

        # Возвращаем список устройств, на которых было выполнено обновление
        return devices_updated

    def update_target_file(self, active_devices, progress_tracker):
        """Обновление target2 на устройствах"""
        if not self.target_file.get():
            self.log_to_gui("Ошибка: Файл target2 не выбран", 'error')
            return

        target_path = self.target_file.get()

        # Проверка локального файла
        if not os.path.exists(target_path):
            self.log_to_gui(f"Ошибка: Файл target2 не найден: {target_path}", 'error')
            return

        update_count = 0

        def update_single_device(device):
            """Обновление target2 на одном устройстве"""
            nonlocal update_count
            ip = device['IP']
            name = device.get('NAME', 'N/A')
            success = False
            error = None

            try:
                msg = f"Обновление target2 на {ip} ({name})"
                self.log_to_gui(msg, 'info')

                if not self.dry_run.get():
                    for attempt in range(self.retry_attempts):
                        ssh = None
                        sftp = None
                        try:
                            ssh = paramiko.SSHClient()
                            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                            timeout = self.ALT_SP_TIMEOUT if self.is_alt_sp_device(device) else self.base_timeout * (
                                        attempt + 1)

                            ssh.connect(
                                ip,
                                username=device['Login'],
                                password=device['Password'],
                                timeout=timeout,
                                look_for_keys=False,
                                allow_agent=False
                            )

                            sftp = ssh.open_sftp()
                            temp_path = '/tmp/target2_tmp.xml'
                            backup_path = '/rtsoft/newcfg/target2_old.xml'
                            remote_path = '/rtsoft/newcfg/target2.xml'

                            # 1. Проверяем существование оригинального файла
                            try:
                                sftp.stat(remote_path)
                                self.log_to_gui(f"Найдена текущая версия target2 на {ip} ({name})", 'info')
                            except IOError:
                                self.log_to_gui(f"Внимание: оригинальный target2.xml не найден на {ip} ({name})",
                                                'warning')

                            # 2. Загружаем новый файл
                            sftp.put(target_path, temp_path)

                            # 3. Заменяем файл с проверками
                            try:
                                sftp.stat(remote_path)
                                # Файл существует - делаем резервную копию
                                stdin, stdout, stderr = ssh.exec_command(
                                    f'mv {remote_path} {backup_path} && echo "BACKUP_SUCCESS" || echo "BACKUP_FAILED"')
                                result = stdout.read().decode().strip()
                                if "BACKUP_FAILED" in result:
                                    raise Exception("Не удалось создать резервную копию target2")

                                stdin, stdout, stderr = ssh.exec_command(
                                    f'mv {temp_path} {remote_path} && echo "MOVE_SUCCESS" || echo "MOVE_FAILED"')
                                result = stdout.read().decode().strip()
                                if "MOVE_FAILED" in result:
                                    raise Exception("Не удалось переместить новый target2")
                            except IOError:
                                # Оригинальный файл не существует, просто перемещаем новый
                                stdin, stdout, stderr = ssh.exec_command(
                                    f'mv {temp_path} {remote_path} && echo "MOVE_SUCCESS" || echo "MOVE_FAILED"')
                                result = stdout.read().decode().strip()
                                if "MOVE_FAILED" in result:
                                    raise Exception("Не удалось переместить новый target2")

                            msg = f"Файл target2 успешно обновлен на {ip} ({name})"
                            self.log_to_gui(msg, 'success')
                            update_count += 1
                            success = True
                            break
                        except Exception as e:
                            error = str(e)
                            if attempt == self.retry_attempts - 1:
                                raise
                            time.sleep(1)
                        finally:
                            if sftp:
                                sftp.close()
                            if ssh:
                                ssh.close()

            except Exception as e:
                error = str(e)
                self.log_to_gui(f"Ошибка обновления target2 для {ip} ({name}): {error}", 'error')
            finally:
                progress_tracker.increment()
                return ip, name, success, error

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(update_single_device, device) for device in active_devices]

            for future in as_completed(futures):
                if not self.running or self.stop_event.is_set():
                    break

                ip, name, success, error = future.result()
                if error:
                    self.log_to_gui(f"Ошибка обновления target2 для {ip} ({name}): {error}", 'error')

        self.log_to_gui(f"=== Обновление target2 завершено: обновлено {update_count} из {len(active_devices)} устройств ===", 'message')

    def _wait_for_shell(self, chan, timeout=10):
        """Ожидание готовности shell"""
        start_time = time.time()
        while not chan.recv_ready():
            if time.time() - start_time > timeout:
                raise Exception("Таймаут ожидания shell")
            time.sleep(0.1)

    def _wait_for_command(self, chan, cmd, timeout=10):
        """Ожидание выполнения команды"""
        start_time = time.time()
        while not chan.recv_ready():
            if time.time() - start_time > timeout:
                raise Exception(f"Таймаут выполнения команды: {cmd}")
            time.sleep(0.1)
        # Читаем ответ (можно добавить проверку ошибок)
        chan.recv(1024)

    def start_reboot_thread(self):
        """Запуск потока перезагрузки устройств"""
        if self.running:
            messagebox.showwarning("Предупреждение", "Операция уже выполняется")
            return

        active_devices = self.get_selected_devices()
        if not active_devices:
            messagebox.showwarning("Предупреждение", "Не выбрано ни одного устройства")
            return

        self.running = True
        self.stop_event.clear()
        self.current_operation.set("Перезагрузка устройств")
        thread = threading.Thread(target=self.reboot_devices_operation, args=(active_devices,), daemon=True)
        thread.start()

    def reboot_devices_operation(self, devices_to_reboot, progress_tracker=None):
        """Перезагрузка указанных устройств"""
        if not devices_to_reboot:
            self.log_to_gui("Нет устройств для перезагрузки", 'info')
            return

        self.log_to_gui("=== Начало перезагрузки устройств ===", 'message')
        device_count = len(devices_to_reboot)
        reboot_count = 0

        if progress_tracker is None:
            progress_tracker = ProgressTracker(device_count, self.update_progress)

        futures = []

        def reboot_single_device(device):
            """Перезагрузка одного устройства"""
            nonlocal reboot_count
            ip = device['IP']
            name = device.get('NAME', 'N/A')
            success = False
            error = None

            try:
                if self.stop_event.is_set():
                    return None, None, None, "Operation stopped"
                msg = f"Перезагрузка устройства {ip} ({name})"
                self.log_to_gui(msg, 'reboot')

                if not self.dry_run.get():
                    for attempt in range(self.retry_attempts):
                        try:
                            ssh = paramiko.SSHClient()
                            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                            if self.is_alt_sp_device(device):
                                try:
                                    ssh.connect(
                                        ip,
                                        username=device['Login'],
                                        password=device['Password'],
                                        timeout=self.ALT_SP_TIMEOUT,
                                        look_for_keys=False,
                                        allow_agent=False
                                    )
                                    chan = ssh.invoke_shell()
                                    time.sleep(0.5)

                                    # Проверка готовности shell
                                    if not chan.recv_ready():
                                        raise Exception("ALT_SP shell not ready")

                                    chan.send('su\n')
                                    time.sleep(0.5)

                                    # Проверка приглашения для ввода пароля
                                    if not chan.recv_ready():
                                        raise Exception("su prompt not received")

                                    chan.send(f'{self.ALT_SU_PASSWORD}\n')
                                    time.sleep(0.5)

                                    # Проверка успешности входа
                                    if not chan.recv_ready():
                                        raise Exception("su login failed")

                                    chan.send('systemctl reboot\n')
                                    time.sleep(1)
                                    chan.close()
                                except Exception as e:
                                    raise Exception(f"ALT_SP connection failed: {str(e)}")
                            else:
                                ssh.connect(
                                    device['IP'],
                                    username=device['Login'],
                                    password=device['Password'],
                                    timeout=self.base_timeout * (attempt + 1),
                                    look_for_keys=False,
                                    allow_agent=False
                                )
                                stdin, stdout, stderr = ssh.exec_command('systemctl reboot')

                            time.sleep(1)
                            reboot_count += 1
                            msg = f"Команда перезагрузки отправлена на {ip} ({name})"
                            self.log_to_gui(msg, 'success')
                            success = True
                            break
                        except Exception as e:
                            error = str(e)
                            if attempt == self.retry_attempts - 1:
                                raise
                            time.sleep(1)
                        finally:
                            if 'ssh' in locals():
                                ssh.close()

            except Exception as e:
                error = str(e)
                msg = f"Не удалось перезагрузить {ip} ({name}): {error}"
                self.log_to_gui(msg, 'error')
            finally:
                progress_tracker.increment()
                return ip, name, success, error

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(reboot_single_device, device) for device in devices_to_reboot]

            for future in as_completed(futures):
                if not self.running or self.stop_event.is_set():
                    break

                ip, name, success, error = future.result()
                if error:
                    self.log_to_gui(f"Ошибка перезагрузки для {ip} ({name}): {error}", 'error')

        self.log_to_gui(f"=== Перезагрузка завершена: перезагружено {reboot_count} из {device_count} устройств ===",
                        'message')
        self.running = False
        self.current_operation.set("Готов к работе")

    def start_upload_files_thread(self):
        """Запуск потока загрузки файлов на устройства"""
        if self.running:
            messagebox.showwarning("Предупреждение", "Операция уже выполняется")
            return

        if not self.upload_dir.get():
            messagebox.showwarning("Предупреждение", "Выберите директорию для загрузки")
            return

        if not os.path.isdir(self.upload_dir.get()):
            messagebox.showwarning("Предупреждение", "Указанная директория не существует")
            return

        active_devices = [d for d in self.devices if d.get('Selected', True)]
        if not self.validate_architecture(active_devices):
            return

        self.running = True
        self.stop_event.clear()
        self.current_operation.set("Загрузка файлов на устройства")
        thread = threading.Thread(target=self.upload_files_to_devices, daemon=True)
        thread.start()

    def upload_files_to_devices(self):
        """Загрузка файлов на устройства с проверкой целостности и переименованием существующих"""
        if not self.excel_path.get():
            messagebox.showwarning("Предупреждение", "Сначала выберите файл Excel")
            self.running = False
            return

        if not self.devices:
            messagebox.showwarning("Предупреждение", "Нет загруженных устройств")
            self.running = False
            return

        if not self.upload_dir.get():
            messagebox.showwarning("Предупреждение", "Выберите директорию для загрузки")
            self.running = False
            return

        if not os.path.isdir(self.upload_dir.get()):
            messagebox.showwarning("Ошибка", "Указанная директория не существует")
            self.running = False
            return

        active_devices = self.get_selected_devices()
        if not active_devices:
            messagebox.showwarning("Предупреждение", "Не выбрано ни одного устройства")
            self.running = False
            return

        if not self.validate_architecture(active_devices):
            self.running = False
            return

        self.log_to_gui("=== Начало загрузки файлов на устройства ===", 'upload')
        self.update_progress(0)

        # Сбор всех файлов для загрузки
        files_to_upload = []
        local_base_dir = self.upload_dir.get()

        try:
            for root, dirs, files in os.walk(local_base_dir):
                for file in files:
                    local_path = os.path.join(root, file)
                    relative_path = os.path.relpath(local_path, local_base_dir)
                    files_to_upload.append((local_path, relative_path))
        except Exception as e:
            self.log_to_gui(f"Ошибка сканирования директории: {str(e)}", 'error')
            self.running = False
            return

        if not files_to_upload:
            self.log_to_gui("Нет файлов для загрузки в указанной директории", 'warning')
            self.running = False
            return

        remote_base_path = self.remote_path.get().strip()
        if not remote_base_path.endswith('/'):
            remote_base_path += '/'

        device_count = len(active_devices)
        total_files = len(files_to_upload)
        success_count = 0

        # Расширения для исполняемых файлов
        executable_extensions = {'.sh', '.bin', '.run', '.exe', ''}
        binary_extensions = {'.so', '.dll', '.a', '.lib'}

        progress_tracker = ProgressTracker(device_count * (total_files * 3 + 1), self.update_progress)

        def mkdir_p(sftp, remote_directory):
            """Рекурсивное создание директорий"""
            if remote_directory == '/':
                return
            try:
                sftp.stat(remote_directory)
            except IOError:
                dirname, basename = os.path.split(remote_directory)
                mkdir_p(sftp, dirname)
                sftp.mkdir(remote_directory)

        def check_file_integrity(sftp, local_path, remote_path):
            """Проверка целостности скопированного файла"""
            try:
                local_size = os.path.getsize(local_path)
                remote_size = sftp.stat(remote_path).st_size

                if local_size != remote_size:
                    return False, f"Размеры не совпадают (локальный: {local_size}, удаленный: {remote_size})"

                # Дополнительная проверка контрольной суммы (если нужно)
                # Можно добавить вычисление md5/sha1 для более строгой проверки

                return True, "Файл проверен успешно"
            except Exception as e:
                return False, f"Ошибка проверки целостности: {str(e)}"

        def upload_to_device(device):
            """Загрузка файлов на одно устройство с проверками"""
            nonlocal success_count
            ip = device['IP']
            name = device.get('NAME', 'N/A')
            device_success = True
            error = None
            operation_details = []
            uploaded_files = 0

            try:
                if self.stop_event.is_set():
                    return None, None, None, "Operation stopped"
                if not self.check_device_availability(ip):
                    msg = f"Устройство {ip} ({name}) недоступно, пропуск"
                    self.log_to_gui(msg, 'unavailable')
                    progress_tracker.increment(total_files * 3 + 1)
                    operation_details.append(msg)
                    return ip, name, False, operation_details

                self.log_to_gui(f"Начало загрузки на {ip} ({name})", 'info')

                if not self.dry_run.get():
                    ssh = None
                    sftp = None
                    try:
                        ssh = paramiko.SSHClient()
                        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        timeout = self.ALT_SP_TIMEOUT if self.is_alt_sp_device(device) else self.CONNECT_TIMEOUT

                        ssh.connect(
                            ip,
                            username=device['Login'],
                            password=device['Password'],
                            timeout=timeout,
                            look_for_keys=False,
                            allow_agent=False
                        )
                        sftp = ssh.open_sftp()
                        progress_tracker.increment()  # Учет установки соединения

                        # Создание всех необходимых директорий
                        dirs_created = set()
                        for local_path, relative_path in files_to_upload:
                            remote_path = remote_base_path + relative_path.replace('\\', '/')
                            remote_dir = os.path.dirname(remote_path)

                            if remote_dir not in dirs_created:
                                try:
                                    mkdir_p(sftp, remote_dir)
                                    dirs_created.add(remote_dir)
                                    operation_details.append(f"Создана директория: {remote_dir}")
                                except Exception as e:
                                    msg = f"Ошибка создания директории {remote_dir}: {str(e)}"
                                    operation_details.append(msg)
                                    self.log_to_gui(msg, 'error')
                                    device_success = False
                                    break

                        if not device_success:
                            return ip, name, False, operation_details

                        # Загрузка файлов
                        for local_path, relative_path in files_to_upload:
                            if not os.path.exists(local_path):
                                msg = f"Файл не найден: {local_path}"
                                operation_details.append(msg)
                                self.log_to_gui(msg, 'error')
                                progress_tracker.increment(3)  # Пропуск 3 шагов (переименование, загрузка, проверка)
                                continue

                            remote_path = remote_base_path + relative_path.replace('\\', '/')
                            file_status = {
                                'filename': relative_path,
                                'renamed': False,
                                'copied': False,
                                'verified': False,
                                'errors': []
                            }

                            try:
                                # 1. Проверка и переименование существующего файла
                                try:
                                    sftp.stat(remote_path)
                                    old_path = f"{remote_path}.old_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                                    sftp.rename(remote_path, old_path)
                                    file_status['renamed'] = True
                                    file_status['old_path'] = old_path
                                    msg = f"Существующий файл переименован: {remote_path} -> {old_path}"
                                    operation_details.append(msg)
                                    self.log_to_gui(msg, 'info')
                                except IOError:
                                    pass  # Файл не существует, не нужно переименовывать

                                progress_tracker.increment()

                                # 2. Загрузка нового файла
                                try:
                                    file_size = os.path.getsize(local_path)

                                    def callback(sent, total):
                                        progress = (sent / total) / (device_count * total_files)
                                        progress_tracker.increment(progress)

                                    sftp.put(local_path, remote_path, callback=callback)
                                    file_status['copied'] = True
                                    msg = f"Файл загружен: {relative_path}"
                                    operation_details.append(msg)
                                    self.log_to_gui(msg, 'info')
                                except Exception as e:
                                    msg = f"Ошибка загрузки {relative_path}: {str(e)}"
                                    file_status['errors'].append(msg)
                                    operation_details.append(msg)
                                    self.log_to_gui(msg, 'error')
                                    device_success = False

                                progress_tracker.increment()

                                # 3. Проверка целостности
                                if file_status['copied']:
                                    try:
                                        integrity_ok, integrity_msg = check_file_integrity(sftp, local_path,
                                                                                           remote_path)
                                        if integrity_ok:
                                            file_status['verified'] = True
                                            msg = f"Проверка целостности успешна: {relative_path}"
                                            operation_details.append(msg)
                                            self.log_to_gui(msg, 'success')
                                        else:
                                            msg = f"Ошибка проверки целостности {relative_path}: {integrity_msg}"
                                            file_status['errors'].append(msg)
                                            operation_details.append(msg)
                                            self.log_to_gui(msg, 'error')
                                            device_success = False
                                    except Exception as e:
                                        msg = f"Ошибка при проверке целостности {relative_path}: {str(e)}"
                                        file_status['errors'].append(msg)
                                        operation_details.append(msg)
                                        self.log_to_gui(msg, 'error')
                                        device_success = False

                                progress_tracker.increment()

                                # 4. Установка прав
                                if file_status['copied'] and not file_status['errors']:
                                    try:
                                        file_ext = os.path.splitext(relative_path)[1].lower()
                                        if file_ext in executable_extensions or file_ext in binary_extensions:
                                            chmod_cmd = f'chmod 755 "{remote_path}"'
                                        else:
                                            chmod_cmd = f'chmod 644 "{remote_path}"'

                                        stdin, stdout, stderr = ssh.exec_command(chmod_cmd)
                                        exit_status = stdout.channel.recv_exit_status()
                                        if exit_status == 0:
                                            msg = f"Права установлены для {relative_path}"
                                            operation_details.append(msg)
                                        else:
                                            msg = f"Ошибка установки прав для {relative_path}"
                                            file_status['errors'].append(msg)
                                            operation_details.append(msg)
                                            self.log_to_gui(msg, 'warning')
                                    except Exception as e:
                                        msg = f"Ошибка при установке прав для {relative_path}: {str(e)}"
                                        file_status['errors'].append(msg)
                                        operation_details.append(msg)
                                        self.log_to_gui(msg, 'warning')

                                if file_status['copied'] and not file_status['errors']:
                                    uploaded_files += 1

                            except Exception as e:
                                msg = f"Критическая ошибка при обработке файла {relative_path}: {str(e)}"
                                file_status['errors'].append(msg)
                                operation_details.append(msg)
                                self.log_to_gui(msg, 'error')
                                device_success = False

                        if device_success:
                            success_count += 1
                            msg = f"Все файлы успешно загружены на {ip} ({name})"
                        else:
                            msg = f"Загрузка завершена с ошибками на {ip} ({name})"

                        operation_details.append(msg)
                        self.log_to_gui(msg, 'info' if device_success else 'warning')

                    except Exception as e:
                        error = str(e)
                        msg = f"Ошибка подключения к {ip}: {error}"
                        operation_details.append(msg)
                        self.log_to_gui(msg, 'error')
                        device_success = False
                    finally:
                        if sftp:
                            sftp.close()
                        if ssh:
                            ssh.close()
            except Exception as e:
                error = str(e)
                msg = f"Критическая ошибка при работе с {ip}: {error}"
                operation_details.append(msg)
                self.log_to_gui(msg, 'error')
                device_success = False
            finally:
                # Учет пропущенных операций в прогрессе
                remaining_steps = (total_files - uploaded_files) * 3
                progress_tracker.increment(remaining_steps)
                return ip, name, device_success, operation_details

        # Запуск загрузки на всех устройствах
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(upload_to_device, device) for device in active_devices]

            try:
                for future in as_completed(futures):
                    if not self.running or self.stop_event.is_set():
                        self.log_to_gui("Загрузка файлов прервана пользователем", 'warning')
                        break

                    ip, name, success, details = future.result()
                    if not success:
                        for detail in details[-5:]:  # Показываем последние 5 сообщений об ошибке
                            self.log_to_gui(f"{ip}: {detail}", 'error')
            except Exception as e:
                self.log_to_gui(f"Ошибка в пуле потоков: {str(e)}", 'error')

        # Создание сводного отчета
        report = [
            "=== Сводный отчет о загрузке файлов ===",
            f"Устройств обработано: {device_count}",
            f"Успешно завершено: {success_count}",
            f"Файлов для загрузки: {total_files}"
        ]

        for line in report:
            self.log_to_gui(line, 'upload')

        self.running = False
        self.current_operation.set("Готов к работе")

    def start_collect_info_thread(self):
        """Запуск потока сбора информации"""
        if self.running:
            messagebox.showwarning("Предупреждение", "Операция уже выполняется")
            return

        self.running = True
        self.stop_event.clear()
        self.current_operation.set("Сбор информации об устройствах")
        thread = threading.Thread(target=self.collect_device_info, daemon=True)
        thread.start()

    def collect_device_info(self):
        """Сбор информации об устройствах"""
        if not self.excel_path.get():
            messagebox.showwarning("Предупреждение", "Сначала выберите файл Excel")
            self.running = False
            return

        if not self.devices:
            messagebox.showwarning("Предупреждение", "Нет загруженных устройств")
            self.running = False
            return

        active_devices = self.get_selected_devices()
        if not active_devices:
            messagebox.showwarning("Предупреждение", "Не выбрано ни одного устройства")
            self.running = False
            return

        self.log_to_gui("=== Начало сбора информации об устройствах ===", 'message')
        self.update_progress(0)
        device_count = len(active_devices)

        progress_tracker = ProgressTracker(device_count, self.update_progress)
        device_info = []

        futures = []

        def collect_single_device(device):
            """Сбор информации с одного устройств"""
            try:
                ip = device['IP']
                name = device.get('NAME', 'N/A')
                self.log_to_gui(f"Сбор информации с {ip} ({name})", 'info')

                if not self.check_device_availability(ip):
                    self.log_to_gui(f"Устройство {ip} ({name}) недоступно", 'unavailable')
                    progress_tracker.increment()
                    return {
                        'Name': name,
                        'IP': ip,
                        'Status': 'Недоступно',
                        'OS Version': 'N/A',
                        'Firmware Version': 'N/A',
                        'Config Version': 'N/A',
                        'MAC Addresses': 'N/A',
                        'Architecture': device.get('ARH', 'N/A')
                    }

                os_version = 'N/A'
                firmware_version = 'N/A'
                config_version = 'N/A'
                mac_addresses = 'N/A'
                error = None

                for attempt in range(self.retry_attempts):
                    try:
                        ssh = paramiko.SSHClient()
                        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        ssh.connect(
                            ip,
                            username=device['Login'],
                            password=device['Password'],
                            timeout=self.base_timeout * (attempt + 1),
                            look_for_keys=False,
                            allow_agent=False
                        )

                        # Получение информации о системе
                        stdin, stdout, stderr = ssh.exec_command('uname -a')
                        os_version = stdout.read().decode().strip()

                        # Получение версии прошивки
                        stdin, stdout, stderr = ssh.exec_command('cat /rtsoft/release_version')
                        firmware_version = stdout.read().decode().strip()

                        # Получение описания конфигурации
                        stdin, stdout, stderr = ssh.exec_command(
                            'cat /rtsoft/newcfg/config.xml | head -n 2 | tail -n 1')
                        config_line = stdout.read().decode().strip()

                        # Получение MAC-адресов
                        stdin, stdout, stderr = ssh.exec_command(
                            'ip link show | grep link/ether | awk \'{print $2}\'')
                        mac_addresses = stdout.read().decode().strip().split('\n')

                        if '<Global description="' in config_line:
                            start = config_line.find('<Global description="') + len('<Global description="')
                            end = config_line.find('"', start)
                            config_version = config_line[start:end]

                        break
                    except Exception as e:
                        error = str(e)
                        if attempt == self.retry_attempts - 1:
                            raise
                    finally:
                        if 'ssh' in locals():
                            ssh.close()

                progress_tracker.increment()
                return {
                    'Name': name,
                    'IP': ip,
                    'Status': 'Доступно',
                    'OS Version': os_version,
                    'Firmware Version': firmware_version,
                    'Config Version': config_version,
                    'MAC Addresses': ', '.join(mac_addresses),
                    'Architecture': device.get('ARH', 'N/A')
                }

            except Exception as e:
                self.log_to_gui(f"Ошибка сбора информации с {device['IP']} ({device.get('NAME', 'N/A')}): {str(e)}",
                                'error')
                progress_tracker.increment()
                return {
                    'Name': device.get('NAME', 'N/A'),
                    'IP': device['IP'],
                    'Status': 'Ошибка',
                    'OS Version': 'N/A',
                    'Firmware Version': 'N/A',
                    'Config Version': 'N/A',
                    'MAC Addresses': 'N/A',
                    'Architecture': device.get('ARH', 'N/A')
                }

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(collect_single_device, device) for device in active_devices]

            for future in as_completed(futures):
                if not self.running or self.stop_event.is_set():
                    break

                info = future.result()
                device_info.append(info)
                if info['Status'] == 'Доступно':
                    self.log_to_gui(f"Информация успешно собрана с {info['IP']} ({info['Name']})", 'success')

        self.device_info = device_info

        try:
            # Сохранение собранной информации в файл Excel
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"device_info_{timestamp}.xlsx"

            df = pd.DataFrame(device_info)
            df.to_excel(output_file, index=False)

            self.log_to_gui(f"Информация об устройствах сохранена в {output_file}", 'success')
        except Exception as e:
            self.log_to_gui(f"Ошибка сохранения информации: {str(e)}", 'error')

        self.log_to_gui("=== Сбор информации об устройствах завершен ===", 'message')
        self.running = False
        self.current_operation.set("Готов к работе")

    def start_send_command_thread(self):
        """Запуск потока отправки команд"""
        if self.running:
            messagebox.showwarning("Предупреждение", "Операция уже выполняется")
            return

        if not self.command_text.get():
            messagebox.showwarning("Предупреждение", "Введите команду для отправки")
            return

        self.add_to_history(self.command_text.get(), self.command_history, self.command_cb)

        self.running = True
        self.stop_event.clear()
        self.current_operation.set("Отправка команд на устройства")
        thread = threading.Thread(target=self.send_command_to_devices, daemon=True)
        thread.start()

    def send_command_to_devices(self):
        """Отправка команд на устройства"""
        if not self.excel_path.get():
            messagebox.showwarning("Предупреждение", "Сначала выберите файл Excel")
            self.running = False
            return

        if not self.devices:
            messagebox.showwarning("Предупреждение", "Нет загруженных устройств")
            self.running = False
            return

        active_devices = self.get_selected_devices()
        if not active_devices:
            messagebox.showwarning("Предупреждение", "Не выбрано ни одного устройства")
            self.running = False
            return

        command = self.command_text.get().strip()
        if not command:
            messagebox.showwarning("Предупреждение", "Введите команду для отправки")
            self.running = False
            return

        self.log_to_gui(f"=== Начало отправки команды '{command}' на устройства ===", 'message')
        self.update_progress(0)
        device_count = len(active_devices)
        success_count = 0

        progress_tracker = ProgressTracker(device_count, self.update_progress)

        futures = []

        def send_to_device(device):
            """Отправка команды на одно устройство"""
            nonlocal success_count
            ip = device['IP']
            name = device.get('NAME', 'N/A')
            success = False
            output = None
            error = None

            try:
                if self.stop_event.is_set():
                    return None, None, None, "Operation stopped"
                self.log_to_gui(f"Отправка команды на {ip} ({name})", 'info')

                if not self.check_device_availability(ip):
                    self.log_to_gui(f"Устройство {ip} ({name}) недоступно", 'unavailable')
                    progress_tracker.increment()
                    return ip, name, False, "Device unavailable"

                if not self.dry_run.get():
                    for attempt in range(self.retry_attempts):
                        try:
                            ssh = paramiko.SSHClient()
                            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                            if self.is_alt_sp_device(device) and self.use_su.get():
                                try:
                                    ssh.connect(
                                        ip,
                                        username=device['Login'],
                                        password=device['Password'],
                                        timeout=self.ALT_SP_TIMEOUT,
                                        look_for_keys=False,
                                        allow_agent=False
                                    )
                                    chan = ssh.invoke_shell()
                                    time.sleep(0.5)

                                    # Проверка готовности shell
                                    if not chan.recv_ready():
                                        raise Exception("ALT_SP shell not ready")

                                    chan.send('su\n')
                                    time.sleep(0.5)

                                    # Проверка приглашения для ввода пароля
                                    if not chan.recv_ready():
                                        raise Exception("su prompt not received")

                                    chan.send(f"{self.su_password.get()}\n")
                                    time.sleep(0.5)

                                    # Проверка успешности входа
                                    if not chan.recv_ready():
                                        raise Exception("su login failed")

                                    chan.send(command + '\n')
                                    time.sleep(1)
                                    output = chan.recv(65535).decode().strip()
                                    chan.close()
                                    errors = ""
                                except Exception as e:
                                    raise Exception(f"ALT_SP connection failed: {str(e)}")
                            else:
                                ssh.connect(
                                    device['IP'],
                                    username=device['Login'],
                                    password=device['Password'],
                                    timeout=self.base_timeout * (attempt + 1),
                                    look_for_keys=False,
                                    allow_agent=False
                                )
                                stdin, stdout, stderr = ssh.exec_command(command)
                                output = stdout.read().decode().strip()
                                errors = stderr.read().decode().strip()

                            if output:
                                self.log_to_gui(f"Вывод команды с {ip} ({name}):\n{output}", 'command')
                            if errors:
                                self.log_to_gui(f"Ошибки выполнения команды на {ip} ({name}):\n{errors}", 'error')

                            success_count += 1
                            self.log_to_gui(f"Команда успешно выдана {ip} ({name})", 'info')
                            success = True
                            break
                        except Exception as e:
                            error = str(e)
                            if attempt == self.retry_attempts - 1:
                                raise
                            time.sleep(1)
                        finally:
                            if 'ssh' in locals():
                                ssh.close()

            except Exception as e:
                error = str(e)
                self.log_to_gui(f"Ошибка выполнения команды на {ip} ({name}): {error}", 'error')
            finally:
                progress_tracker.increment()
                return ip, name, success, error

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(send_to_device, device) for device in active_devices]

            for future in as_completed(futures):
                if not self.running or self.stop_event.is_set():
                    break

                ip, name, success, error = future.result()
                if error:
                    self.log_to_gui(f"Ошибка выполнения команды на {ip} ({name}): {error}", 'error')

        self.log_to_gui(f"=== Отправка команды выполнена на {success_count} из {device_count} устройств ===",
                        'message')
        self.running = False
        self.current_operation.set("Готов к работе")

    def add_to_history(self, value, history, combobox):
        """Добавление значения в историю"""
        if value and value not in history:
            history.appendleft(value)
            combobox['values'] = list(history)
            self.save_settings()

    def restart_ppo(self):
        """Запуск потока перезапуска ППО"""
        if self.running:
            messagebox.showwarning("Предупреждение", "Операция уже выполняется")
            return

        active_devices = self.get_selected_devices()
        if not active_devices:
            messagebox.showwarning("Предупреждение", "Не выбрано ни одного устройства")
            return

        self.running = True
        self.stop_event.clear()
        self.current_operation.set("Перезапуск ППО на устройствах")
        thread = threading.Thread(target=self.restart_ppo_operation, args=(active_devices,), daemon=True)
        thread.start()

    def restart_ppo_operation(self, devices):
        """Перезапуск ППО на указанных устройствах"""
        if not devices:
            self.log_to_gui("Нет устройств для перезапуска ППО", 'info')
            self.running = False
            return

        self.log_to_gui("=== Начало перезапуска ППО на устройствах ===", 'message')
        if self.dry_run.get():
            self.log_to_gui("ТЕСТОВЫЙ РЕЖИМ: команды не будут выполняться на устройствах", 'warning')

        self.update_progress(0)
        device_count = len(devices)
        success_count = 0

        progress_tracker = ProgressTracker(device_count, self.update_progress)

        futures = []

        def restart_single_device(device):
            """Перезапуск ППО на одном устройстве"""
            nonlocal success_count
            ip = device['IP']
            name = device.get('NAME', 'N/A')
            success = False
            error = None
            output = None
            errors = None

            try:
                if self.stop_event.is_set():
                    return None, None, None, "Operation stopped"
                msg = f"Перезапуск ППО на устройстве {ip} ({name})"
                self.log_to_gui(msg, 'info')

                if self.dry_run.get():
                    # В тестовом режиме просто сообщаем о намерении без реального выполнения
                    msg = f"ТЕСТОВЫЙ РЕЖИМ: Имитация перезапуска ППО на {ip} ({name})"
                    self.log_to_gui(msg, 'info')
                    progress_tracker.increment()
                    return ip, name, True, None

                for attempt in range(self.retry_attempts):
                    try:
                        ssh = paramiko.SSHClient()
                        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                        if self.is_alt_sp_device(device) and self.use_su.get():
                            try:
                                ssh.connect(
                                    ip,
                                    username=device['Login'],
                                    password=device['Password'],
                                    timeout=self.ALT_SP_TIMEOUT,
                                    look_for_keys=False,
                                    allow_agent=False
                                )
                                chan = ssh.invoke_shell()
                                time.sleep(1)

                                if not chan.recv_ready():
                                    raise Exception("ALT_SP shell not ready")

                                chan.send('su\n')
                                time.sleep(1)

                                if not chan.recv_ready():
                                    raise Exception("su prompt not received")

                                chan.send(f"{self.su_password.get()}\n")
                                time.sleep(1)

                                if not chan.recv_ready():
                                    raise Exception("su login failed")

                                chan.send('systemctl restart PrjA-tele\n')
                                time.sleep(1)
                                output = chan.recv(65535).decode().strip()

                                chan.send('systemctl restart PrjA\n')
                                time.sleep(1)
                                output = chan.recv(65535).decode().strip()

                                if "Failed" in output or "failed" in output or "error" in output.lower():
                                    raise Exception(f"Ошибка перезапуска ППО: {output}")

                                chan.close()
                            except Exception as e:
                                raise Exception(f"ALT_SP connection failed: {str(e)}")
                        else:
                            ssh.connect(
                                ip,
                                username=device['Login'],
                                password=device['Password'],
                                timeout=self.base_timeout * (attempt + 1),
                                look_for_keys=False,
                                allow_agent=False
                            )
                            stdin, stdout, stderr = ssh.exec_command('systemctl restart PrjA-tele')
                            output = stdout.read().decode().strip()
                            errors = stderr.read().decode().strip()

                            stdin, stdout, stderr = ssh.exec_command('systemctl restart PrjA')
                            output = stdout.read().decode().strip()
                            errors = stderr.read().decode().strip()

                            if errors or "Failed" in output or "failed" in output:
                                raise Exception(f"{errors or output}")

                        if output:
                            self.log_to_gui(f"Вывод команды с {ip} ({name}):\n{output}", 'info')
                        if errors:
                            self.log_to_gui(f"Ошибки выполнения команды на {ip} ({name}):\n{errors}", 'error')

                        success_count += 1
                        self.log_to_gui(f"ППО успешно перезапущено на {ip} ({name})", 'success')
                        success = True
                        break
                    except Exception as e:
                        error = str(e)
                        if attempt == self.retry_attempts - 1:
                            raise
                        time.sleep(1)
                    finally:
                        if 'ssh' in locals():
                            ssh.close()

            except Exception as e:
                error = str(e)
                self.log_to_gui(f"Ошибка перезапуска ППО на {ip} ({name}): {error}", 'error')
            finally:
                progress_tracker.increment()
                return ip, name, success, error

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(restart_single_device, device) for device in devices]

            for future in as_completed(futures):
                if not self.running or self.stop_event.is_set():
                    break

                ip, name, success, error = future.result()
                if not success and not self.dry_run.get():
                    self.log_to_gui(f"Не удалось перезапустить ППО на {ip} ({name})", 'error')

        self.log_to_gui(f"=== Перезапуск ППО завершен: успешно на {success_count} из {device_count} устройств ===",
                        'message')
        self.running = False
        self.current_operation.set("Готов к работе")

    def skip_device(self, device):
        """Пропуск устройства (может быть переопределен в подклассах)"""
        return False

    def stop_updates(self):
        """Остановка текущей операции"""
        self.running = False
        self.stop_event.set()

        # Останавливаем прогресс
        if hasattr(self, 'progress_tracker'):
            self.progress_tracker.stop()

        # Пытаемся закрыть все SSH-соединения
        try:
            for thread in threading.enumerate():
                if isinstance(thread, threading.Thread) and thread != threading.current_thread():
                    if hasattr(thread, '_Thread__target'):
                        # Для потоков с SSH
                        if hasattr(thread, 'ssh_connections'):
                            for ssh in thread.ssh_connections:
                                try:
                                    ssh.close()
                                except:
                                    pass
        except Exception as e:
            self.log_to_gui(f"Ошибка при остановке: {str(e)}", 'error')

        self.log_to_gui("Операция остановлена пользователем", 'warning')
        self.current_operation.set("Остановка операций, дождитесь готовности ")

        # Сброс прогресс-бара
        #self.progress['value'] = 0
        #self.progress_percent.set("0%")
        # Запускаем мигание
        self.blinking = True
        self.start_blinking()

    def check_duplicate_macs(self):
        """Проверка дублирующихся MAC-адресов"""
        if not hasattr(self, 'device_info') or not self.device_info:
            messagebox.showwarning("Предупреждение", "Сначала соберите информацию об устройствах")
            return

        mac_dict = {}

        # Сбор MAC-адресов в словарь
        for device in self.device_info:
            if device['MAC Addresses'] != 'N/A':
                macs = [mac.strip() for mac in device['MAC Addresses'].split(',')]
                for mac in macs:
                    if mac in mac_dict:
                        mac_dict[mac].append(f"{device['Name']} ({device['IP']})")
                    else:
                        mac_dict[mac] = [f"{device['Name']} ({device['IP']})"]

        # Поиск дубликатов
        duplicates = {mac: ips for mac, ips in mac_dict.items() if len(ips) > 1}

        if not duplicates:
            messagebox.showinfo("Информация", "Дублирующихся MAC-адресов не обнаружено")
            return

        # Формирование отчета
        report = []
        for mac, ips in duplicates.items():
            report.append({
                'MAC-адрес': mac,
                'Количество устройств': len(ips),
                'Устройства': ', '.join(ips)
            })

        try:
            # Сохранение отчета в файл Excel
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"mac_duplicates_report_{timestamp}.xlsx"

            df = pd.DataFrame(report)
            df.to_excel(output_file, index=False)

            message = f"Обнаружено {len(duplicates)} дублирующихся MAC-адресов.\nОтчет сохранен в {output_file}"
            messagebox.showinfo("Отчет о дубликатах MAC-адресов", message)
            self.log_to_gui(f"Отчет о дубликатах MAC-адресов сохранен в {output_file}", 'success')
        except Exception as e:
            error_msg = f"Ошибка при сохранении отчета: {str(e)}"
            messagebox.showerror("Ошибка", error_msg)
            self.log_to_gui(error_msg, 'error')

    def start_blinking(self):
        """Мигание всего прогресс-бара (и заливки, и фона) при остановке"""
        if not self.blinking:
            return  # Если мигание выключено, выходим

        # Чередуем два состояния: красный бар на сером фоне и серый бар на красном фоне
        if not hasattr(self, '_blink_state'):
            self._blink_state = True

        if self._blink_state:
            # Состояние 1: красный бар на золотом фоне
            #bar_color = '#ff0000'  # Красный
            trough_color = '#ffd700'  # Золотой
        else:
            # Состояние 2: золотой бар на красном фоне
            #bar_color = '#ffd700'  # Золотой
            trough_color = '#ff0000'  # Красный

        self._blink_state = not self._blink_state  # Переключаем состояние

        self.style.configure(
            "Green.Horizontal.TProgressbar",
            troughcolor=trough_color,  # Цвет фона
            background='#00c853'  # Зеленый цвет заполнения
        )

        # Повторяем через 500 мс (0.5 сек)
        self.root.after(500, self.start_blinking)

    def stop_blinking(self):
        """Остановка мигания и возврат к нормальному состоянию"""
        self.blinking = False
        if hasattr(self, '_blink_state'):
            del self._blink_state

        # Возвращаем стандартные цвета
        self.style.configure(
            "Green.Horizontal.TProgressbar",
            troughcolor=self.bg_color,  # Фоновый цвет как у основного интерфейса
            background='#00c853'  # Зеленый цвет заполнения
        )

    def update_test_mode_label(self):
        """Обновляет видимость метки тестового режима"""
        if self.dry_run.get():
            self.test_mode_label.pack(side=tk.LEFT, padx=5)
        else:
            self.test_mode_label.pack_forget()

if __name__ == "__main__":
    root = tk.Tk()
    app = UpdaterApp(root)
    root.mainloop()