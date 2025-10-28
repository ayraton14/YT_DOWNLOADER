#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube Downloader GUI (Tkinter + yt-dlp)
- Выбор качества (480p..8K)
- Выбор видео кодека (AV1 / VP9 / H.264) и аудио кодека (Opus / AAC / Vorbis)
- Итоговый контейнер: Авто / MP4 / MKV / WEBM
- Поддержка age-restricted через cookies.txt (Netscape формат)
- Прогресс-бар, лог, отмена, выбор папки
- Очередь загрузок: добавить, старт, очистить; готовые задачи удаляются из очереди
- Загрузка .txt со ссылками в очередь по текущему пресету
- Автопереименование итогового файла в компактный вид:
  <vcodec>_<acodec>_<height>_<Title>.<ext>
- Сохранение настроек (~/.yt_gui_downloader_config.json)

Новые функции:
- ПКМ по строке в очереди: «Изменить…» и «Удалить»
- Двойной клик по строке — быстрое «Изменить…»
- Множественное выделение и удаление нескольких задач
- Во время выполнения очереди изменения блокируются
- Кнопка «Обновить yt-dlp» — обновление через pip в отдельном потоке + лог
- «Добавить в очередь» берёт ссылку из буфера (если это URL)
- ДВА ЛОГА: слева «Важные сообщения», справа «Подробный лог (yt-dlp)» (горизонтальный сплит)
- Защита от «очень длинных» названий: умное сокращение в UI и при переименовании
© 2025
"""

import os
import sys
import json
import threading
import time
import shutil
import subprocess
import importlib
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ---------- Настройки сокращений ----------
MAX_UI_TITLE = 80        # сколько символов показывать в статусе/таблице (умное многоточие по середине)
MAX_QUEUE_TITLE = 70     # для колонки "Название"
SAFE_MAX_PATH = 240      # безопасная длина полного пути (Windows без LongPaths)
MIN_BASE_LEN = 20        # минимальная длина видимой части Title при ужатии имени файла

# Путь к файлу конфигурации
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".yt_gui_downloader_config.json")

# Настройки доступных аудио-форматов для режима «только аудио»
AUDIO_FORMAT_OPTIONS = {
    "mp3": {
        "label": "MP3",
        "codec": "mp3",
        "extension": "mp3",
        "bitrate_values": ["128", "160", "192", "224", "256", "320"],
    },
    "m4a": {
        "label": "M4A / AAC",
        "codec": "m4a",
        "extension": "m4a",
        "bitrate_values": ["128", "192", "256", "320"],
    },
    "opus": {
        "label": "Opus",
        "codec": "opus",
        "extension": "opus",
        "bitrate_values": ["96", "128", "160", "192"],
    },
    "vorbis": {
        "label": "Vorbis",
        "codec": "vorbis",
        "extension": "ogg",
        "bitrate_values": ["128", "160", "192", "224"],
    },
    "wav": {
        "label": "WAV (без сжатия)",
        "codec": "wav",
        "extension": "wav",
        "bitrate_values": [],
    },
}

DEFAULT_AUDIO_FORMAT = "mp3"
DEFAULT_AUDIO_QUALITY = "192"

LIGHT_BG = "#f5f7fb"
LIGHT_PANEL_BG = "#ffffff"
TEXT_COLOR = "#0d1b2a"
MUTED_TEXT_COLOR = "#5a6b7d"
ACCENT_COLOR = "#3a7bd5"
ACCENT_COLOR_ACTIVE = "#4f8de3"

# Попытка импорта yt_dlp
try:
    import yt_dlp
    from yt_dlp import YoutubeDL
except Exception as e:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "yt-dlp не найден",
        "Библиотека 'yt-dlp' не установлена.\n\n"
        "Откройте терминал и выполните:\n"
        "    pip install yt-dlp\n\n"
        f"Подробности: {e}"
    )
    sys.exit(1)


def human_readable_size(nbytes: float) -> str:
    try:
        nbytes = float(nbytes)
    except Exception:
        return "?"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while nbytes >= 1024 and i < len(units) - 1:
        nbytes /= 1024.0
        i += 1
    return f"{nbytes:.2f} {units[i]}"


def seconds_to_hms(sec: Optional[int]) -> str:
    try:
        s = int(sec)
    except Exception:
        return "?"
    h = s // 3600
    m = (s % 3600) // 60
    s2 = s % 60
    return f"{h:02d}:{m:02d}:{s2:02d}" if h else f"{m:02d}:{s2:02d}"


def open_file_manager(path: str):
    try:
        if sys.platform.startswith("win"):
            os.startfile(os.path.abspath(path))
        elif sys.platform == "darwin":
            subprocess.run(["open", os.path.abspath(path)])
        else:
            subprocess.run(["xdg-open", os.path.abspath(path)])
    except Exception as e:
        messagebox.showerror("Ошибка", f"Не удалось открыть папку:\n{e}")


class TkLogger:
    """
    Логгер для интеграции с yt-dlp в два Text-виджета:
    - main_text: важные сообщения (info/warning/error)
    - raw_text: подробный поток (debug, прогресс)
    """
    def __init__(self, main_text: tk.Text, raw_text: tk.Text):
        self.main_text = main_text
        self.raw_text = raw_text

    def _append_to(self, widget: tk.Text, msg: str):
        def do_insert():
            try:
                widget.configure(state="normal")
                widget.insert("end", msg + "\n")
                widget.see("end")
                widget.configure(state="disabled")
            except Exception:
                pass
        widget.after(0, do_insert)

    def debug(self, msg):
        self._append_to(self.raw_text, str(msg))

    def info(self, msg):
        self._append_to(self.main_text, str(msg))

    def warning(self, msg):
        self._append_to(self.main_text, "[ВНИМАНИЕ] " + str(msg))

    def error(self, msg):
        self._append_to(self.main_text, "[ОШИБКА] " + str(msg))


@dataclass
class DownloadPreset:
    # Добавлено: выбор языка аудиодорожки
    height: int
    vcodec_choice: str  # "Авто", "AV1 (av01)", "VP9 (vp9)", "H.264 (avc1)"
    acodec_choice: str  # "Авто", "Opus (opus)", "AAC (mp4a)", "Vorbis (vorbis)"
    alang_choice: str  #  'orig' (оригинал) или ISO-код языка
    container_choice: str  # "Авто", "mp4", "mkv", "webm"
    outdir: str
    outtmpl_user: str
    cookies: Optional[str] = None
    audio_only: bool = False
    audio_format: str = "mp3"  # mp3 / m4a / opus / vorbis / wav
    audio_quality: str = "192"
    download_playlist: bool = False
    subtitle_langs: Tuple[str, ...] = field(default_factory=tuple)
    write_subtitles: bool = False
    embed_subtitles: bool = False


@dataclass
class QueueItem:
    url: str
    preset: DownloadPreset
    status: str = field(default="Ожидает")
    result_path: Optional[str] = None
    title: Optional[str] = None  # полное название для таблицы/статуса


class DownloaderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YouTube Видео Загрузчик (yt-dlp)")
        self.geometry("1020x900")
        self.minsize(1020, 900)
        self.configure(bg=LIGHT_BG)

        # Состояние
        self.cancel_event = threading.Event()
        self.download_thread = None
        self.queue_thread = None
        self.queue: List[QueueItem] = []
        self.queue_running = False
        self.last_output_path = None
        self._save_debounce_after = None
        self._extra_status_suffix = ""   # короткая подпись кодеков/контейнера в статусе
        self._current_title: Optional[str] = None
        self._updating = False

        # Для «подробного» лога
        self._last_raw_line_ts: float = 0.0
        self._last_raw_percent: float = -1.0

        # Доступные языки (динамически обновляемые)
        self.available_audio_languages: List[str] = ["orig", "ru", "en"]
        self.available_subtitle_languages: List[str] = []
        self.selected_subtitle_langs: List[str] = []
        self._metadata_fetching = False

        # UI
        self._build_ui()

        # Проверка ffmpeg
        self._check_ffmpeg()

        # Загрузка/подписка настроек
        self._load_settings()
        self._bind_setting_events()

        # Закрытие
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------ Вспомогательные сокращатели ------------------------

    def _ellipsize(self, s: str, maxlen: int) -> str:
        """Умное многоточие по середине: сохраняем начало и конец."""
        try:
            s = str(s)
        except Exception:
            return ""
        if maxlen <= 1 or len(s) <= maxlen:
            return s
        keep = maxlen - 1
        left = int(keep * 0.6)
        right = keep - left
        return f"{s[:left]}…{s[-right:]}" if right > 0 else s[:maxlen]

    # ------------------------ UI ------------------------

    def _build_ui(self):
        pad = 10

        main = ttk.Frame(self, padding=pad)
        main.pack(fill="both", expand=True, padx=pad, pady=pad)

        # URL
        url_frame = ttk.LabelFrame(main, text="Ссылка на видео (YouTube)", padding=pad)
        url_frame.pack(fill="x", expand=False, pady=(0, pad))
        self.url_var = tk.StringVar()
        url_entry = ttk.Entry(url_frame, textvariable=self.url_var)
        url_entry.pack(side="left", fill="x", expand=True, padx=(0, pad))
        url_entry.focus_set()

        self.analyze_btn = ttk.Button(
            url_frame,
            text="Анализировать",
            style="Accent.TButton",
            command=self._on_fetch_metadata_clicked,
        )
        self.analyze_btn.pack(side="left", padx=(0, pad))

        self.analyze_status_var = tk.StringVar(value="")
        self.analyze_status_label = ttk.Label(url_frame, textvariable=self.analyze_status_var, foreground=MUTED_TEXT_COLOR)
        self.analyze_status_label.pack(side="left")

        paste_btn = ttk.Button(url_frame, text="Вставить", command=self._paste_from_clipboard)
        paste_btn.pack(side="left", padx=(0, pad))

        clear_btn = ttk.Button(url_frame, text="Очистить", command=lambda: self.url_var.set(""))
        clear_btn.pack(side="left", padx=(0, pad))

        # Настройки
        settings = ttk.LabelFrame(main, text="Настройки", padding=pad)
        settings.pack(fill="x", expand=False, pady=(0, pad))

        # Качество
        ttk.Label(settings, text="Качество:").grid(row=0, column=0, padx=(0, 5), pady=(0, 5), sticky="w")
        self.quality_var = tk.StringVar(value="1080p")
        qualities = ["480p", "720p", "1080p", "1440p (2K)", "2160p (4K)", "4320p (8K)"]
        self.quality_cb = ttk.Combobox(settings, textvariable=self.quality_var, values=qualities, state="readonly", width=20)
        self.quality_cb.grid(row=0, column=1, padx=(0, 15), pady=(0, 5), sticky="w")

        # Видео кодек
        ttk.Label(settings, text="Видео кодек:").grid(row=0, column=2, padx=(0, 5), pady=(0, 5), sticky="w")
        self.vcodec_var = tk.StringVar(value="Авто")
        vcodec_values = ["Авто", "AV1 (av01)", "VP9 (vp9)", "H.264 (avc1)"]
        self.vcodec_cb = ttk.Combobox(settings, textvariable=self.vcodec_var, values=vcodec_values, state="readonly", width=18)
        self.vcodec_cb.grid(row=0, column=3, padx=(0, 15), pady=(0, 5), sticky="w")

        # Аудио кодек
        ttk.Label(settings, text="Аудио кодек:").grid(row=0, column=4, padx=(0, 5), pady=(0, 5), sticky="w")
        self.acodec_var = tk.StringVar(value="Авто")
        acodec_values = ["Авто", "Opus (opus)", "AAC (mp4a)", "Vorbis (vorbis)"]
        self.acodec_cb = ttk.Combobox(settings, textvariable=self.acodec_var, values=acodec_values, state="readonly", width=18)
        self.acodec_cb.grid(row=0, column=5, padx=(0, 15), pady=(0, 5), sticky="w")

        # Язык аудио
        ttk.Label(settings, text="Язык аудио:").grid(row=0, column=6, padx=(0, 5), pady=(0, 5), sticky="w")
        self.alang_var = tk.StringVar(value="orig")
        self.alang_cb = ttk.Combobox(settings, textvariable=self.alang_var, values=self.available_audio_languages, state="readonly", width=8)
        self.alang_cb.grid(row=0, column=7, padx=(0, 15), pady=(0, 5), sticky="w")

        self.playlist_var = tk.IntVar(value=0)
        self.playlist_cb = ttk.Checkbutton(settings, text="Скачать плейлист целиком", variable=self.playlist_var,
                                           command=self._save_settings_debounced)
        self.playlist_cb.grid(row=0, column=8, padx=(0, 0), pady=(0, 5), sticky="w")

        # Итоговый контейнер
        ttk.Label(settings, text="Контейнер:").grid(row=1, column=0, padx=(0, 5), pady=5, sticky="w")
        self.container_var = tk.StringVar(value="Авто")
        container_values = ["Авто", "mp4", "mkv", "webm"]
        self.container_cb = ttk.Combobox(settings, textvariable=self.container_var, values=container_values, state="readonly", width=20)
        self.container_cb.grid(row=1, column=1, padx=(0, 15), pady=5, sticky="w")

        # Папка сохранения
        ttk.Label(settings, text="Папка сохранения:").grid(row=1, column=2, padx=(0, 5), pady=5, sticky="w")
        self.outdir_var = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Downloads"))
        outdir_entry = ttk.Entry(settings, textvariable=self.outdir_var, width=40)
        outdir_entry.grid(row=1, column=3, padx=(0, 5), pady=5, sticky="w")
        outdir_btn = ttk.Button(settings, text="Выбрать...", command=self._choose_outdir)
        outdir_btn.grid(row=1, column=4, padx=(0, 15), pady=5, sticky="w")

        # Cookies
        ttk.Label(settings, text="Cookies (опционально):").grid(row=1, column=5, padx=(0, 5), pady=5, sticky="w")
        self.cookies_var = tk.StringVar()
        cookies_btn = ttk.Button(settings, text="Выбрать cookies.txt...", command=self._choose_cookies)
        cookies_btn.grid(row=1, column=6, columnspan=2, padx=(0, 15), pady=5, sticky="w")

        # Путь к cookies
        ttk.Label(settings, text="Путь cookies:").grid(row=2, column=0, padx=(0, 5), pady=5, sticky="w")
        self.cookies_path_entry = ttk.Entry(settings, textvariable=self.cookies_var, width=85)
        self.cookies_path_entry.grid(row=2, column=1, columnspan=7, padx=(0, 5), pady=5, sticky="w")

        # Имя файла (шаблон)
        ttk.Label(settings, text="Имя файла:").grid(row=3, column=0, padx=(0, 5), pady=5, sticky="w")
        self.outtmpl_var = tk.StringVar(value="%(title)s.%(ext)s")
        outtmpl_entry = ttk.Entry(settings, textvariable=self.outtmpl_var, width=85)
        outtmpl_entry.grid(row=3, column=1, columnspan=7, padx=(0, 5), pady=5, sticky="w")
        hint = ttk.Label(settings, text="Рекомендуем оставить %(title)s.%(ext)s — приложение переименует итоговый файл автоматически.", foreground=MUTED_TEXT_COLOR)
        hint.grid(row=4, column=1, columnspan=7, padx=(0, 5), pady=(0, 5), sticky="w")

        # Только аудио
        self.audio_only_var = tk.IntVar(value=0)
        audio_only_cb = ttk.Checkbutton(settings, text="Скачать только аудио", variable=self.audio_only_var,
                                        command=self._on_audio_only_toggle)
        audio_only_cb.grid(row=5, column=0, columnspan=2, sticky="w", pady=(5, 5))

        ttk.Label(settings, text="Формат:").grid(row=5, column=2, padx=(0, 5), pady=(5, 5), sticky="e")
        self.audio_format_var = tk.StringVar(value=DEFAULT_AUDIO_FORMAT)
        format_labels = [info['label'] for info in AUDIO_FORMAT_OPTIONS.values()]
        self.audio_format_map = {info["label"]: key for key, info in AUDIO_FORMAT_OPTIONS.items()}
        self.audio_format_label_var = tk.StringVar(value=AUDIO_FORMAT_OPTIONS[DEFAULT_AUDIO_FORMAT]["label"])
        self.audio_format_cb = ttk.Combobox(settings, state="readonly", values=format_labels,
                                           textvariable=self.audio_format_label_var)
        self.audio_format_cb.grid(row=5, column=3, padx=(0, 5), pady=(5, 5), sticky="w")
        self.audio_format_cb.bind("<<ComboboxSelected>>", lambda *_: self._on_audio_format_selected())

        ttk.Label(settings, text="Битрейт:").grid(row=5, column=4, padx=(0, 5), pady=(5, 5), sticky="e")
        self.audio_quality_var = tk.StringVar(value=DEFAULT_AUDIO_QUALITY)
        self.audio_quality_cb = ttk.Combobox(settings, textvariable=self.audio_quality_var, state="readonly", width=8)
        self.audio_quality_cb.grid(row=5, column=5, padx=(0, 15), pady=(5, 5), sticky="w")

        # Субтитры
        ttk.Label(settings, text="Субтитры:").grid(row=6, column=0, padx=(0, 5), pady=(5, 0), sticky="w")
        self.write_subs_var = tk.IntVar(value=0)
        write_cb = ttk.Checkbutton(settings, text="Скачать", variable=self.write_subs_var,
                                   command=self._on_subtitle_option_changed)
        write_cb.grid(row=6, column=1, sticky="w", pady=(5, 0))
        self.embed_subs_var = tk.IntVar(value=0)
        self.embed_subs_check = ttk.Checkbutton(settings, text="Встроить в видео", variable=self.embed_subs_var,
                                               command=self._on_subtitle_option_changed)
        self.embed_subs_check.grid(row=6, column=2, columnspan=2, sticky="w", pady=(5, 0))

        self.subtitle_display_var = tk.StringVar(value="Не выбрано")
        subtitle_entry = ttk.Entry(settings, textvariable=self.subtitle_display_var, state="readonly", width=40)
        subtitle_entry.grid(row=6, column=4, columnspan=3, sticky="w", padx=(0, 5), pady=(5, 0))
        self.subtitle_select_btn = ttk.Button(settings, text="Выбрать языки...", command=self._open_subtitle_selector)
        self.subtitle_select_btn.grid(row=6, column=7, columnspan=2, sticky="w", pady=(5, 0))

        for col in range(0, 9):
            settings.grid_columnconfigure(col, weight=1 if col in (1, 3, 5, 7) else 0)

        # Кнопки управления
        buttons = ttk.Frame(main, padding=(0, 0, 0, 0))
        buttons.pack(fill="x", expand=False, pady=(0, pad))
        self.download_btn = ttk.Button(buttons, text="Скачать сейчас", style="Accent.TButton", command=self._on_download_clicked)
        self.download_btn.pack(side="left", padx=(0, pad))
        self.cancel_btn = ttk.Button(buttons, text="Отмена", command=self._on_cancel_clicked, state="disabled")
        self.cancel_btn.pack(side="left", padx=(0, pad))
        self.open_btn = ttk.Button(buttons, text="Открыть папку", command=lambda: open_file_manager(self.outdir_var.get()))
        self.open_btn.pack(side="left", padx=(0, pad))
        self.update_yt_btn = ttk.Button(buttons, text="Обновить yt-dlp", command=self._on_update_yt_dlp)
        self.update_yt_btn.pack(side="left", padx=(0, pad))

        # Прогресс
        progress_frame = ttk.LabelFrame(main, text="Прогресс", padding=pad)
        progress_frame.pack(fill="x", expand=False, pady=(0, pad))
        self.progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=100)
        self.progress.pack(fill="x", padx=0, pady=(0, 5))
        self.status_var = tk.StringVar(value="Ожидание...")
        status_label = ttk.Label(progress_frame, textvariable=self.status_var)
        status_label.pack(fill="x", pady=(0, 0))

        # Очередь
        queue_frame = ttk.LabelFrame(main, text="Очередь загрузок", padding=pad)
        queue_frame.pack(fill="both", expand=True, pady=(0, pad))

        queue_buttons = ttk.Frame(queue_frame)
        queue_buttons.pack(fill="x", expand=False, pady=(0, pad))
        self.add_queue_btn = ttk.Button(queue_buttons, text="Добавить в очередь", command=self._on_add_to_queue)
        self.add_queue_btn.pack(side="left", padx=(0, 5))
        self.load_txt_btn = ttk.Button(queue_buttons, text="Загрузить .txt в очередь", command=self._on_load_txt_to_queue)
        self.load_txt_btn.pack(side="left", padx=(0, 5))
        self.start_queue_btn = ttk.Button(queue_buttons, text="Старт очереди", style="Accent.TButton", command=self._on_start_queue)
        self.start_queue_btn.pack(side="left", padx=(0, 5))
        self.clear_queue_btn = ttk.Button(queue_buttons, text="Очистить очередь", command=self._on_clear_queue)
        self.clear_queue_btn.pack(side="left", padx=(0, 5))

        columns = ("title", "quality", "vcodec", "acodec", "container", "status")
        self.queue_tv = ttk.Treeview(queue_frame, columns=columns, show="headings", height=10)
        headers = {
            "title": "Название",
            "quality": "Качество",
            "vcodec": "ВИДЕО",
            "acodec": "АУДИО",
            "container": "КОНТЕЙНЕР",
            "status": "СТАТУС",
        }
        for col, w in zip(columns, (420, 110, 120, 140, 120, 120)):
            self.queue_tv.heading(col, text=headers[col])
            self.queue_tv.column(col, width=w, anchor="w")
        self.queue_tv.pack(fill="both", expand=True)

        # Контекстное меню / события таблицы
        self._queue_menu = tk.Menu(self, tearoff=0)
        self._queue_menu.add_command(label="Изменить…", command=self._on_queue_edit_selected)
        self._queue_menu.add_command(label="Удалить", command=self._on_queue_delete_selected)
        self._MENU_IDX_EDIT = 0
        self._MENU_IDX_DELETE = 1
        self.queue_tv.bind("<Button-3>", self._on_queue_right_click)
        self.queue_tv.bind("<Control-Button-1>", self._on_queue_right_click)
        self.queue_tv.bind("<Double-1>", self._on_queue_double_click)
        self.queue_tv.configure(selectmode="extended")

        # --- ДВА ЛОГА: слева важный, справа подробный ---
        logs_group = ttk.LabelFrame(main, text="Журналы", padding=pad)
        logs_group.pack(fill="both", expand=True)

        # горизонтальное расположение панелей (левая/правая)
        paned = ttk.Panedwindow(logs_group, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # Важные сообщения (слева)
        imp_frame = ttk.LabelFrame(paned, text="Важные сообщения", padding=pad)
        self.log_main_text = tk.Text(imp_frame, wrap="word", height=10, state="disabled", bg=LIGHT_PANEL_BG, fg=TEXT_COLOR, insertbackground=ACCENT_COLOR)
        imp_vsb = ttk.Scrollbar(imp_frame, orient="vertical", command=self.log_main_text.yview)
        self.log_main_text.configure(yscrollcommand=imp_vsb.set)
        self.log_main_text.pack(side="left", fill="both", expand=True)
        imp_vsb.pack(side="right", fill="y")
        paned.add(imp_frame, weight=1)

        # Подробный лог (справа)
        raw_frame = ttk.LabelFrame(paned, text="Подробный лог (yt-dlp)", padding=pad)
        self.log_raw_text = tk.Text(raw_frame, wrap="none", height=10, state="disabled", bg=LIGHT_PANEL_BG, fg=TEXT_COLOR, insertbackground=ACCENT_COLOR)
        raw_vsb = ttk.Scrollbar(raw_frame, orient="vertical", command=self.log_raw_text.yview)
        self.log_raw_text.configure(yscrollcommand=raw_vsb.set)
        self.log_raw_text.pack(side="left", fill="both", expand=True)
        raw_vsb.pack(side="right", fill="y")
        paned.add(raw_frame, weight=1)

        # Стили
        try:
            self.style = ttk.Style(self)
            if sys.platform == "darwin":
                self.style.theme_use("aqua")
            else:
                self.style.theme_use("clam")
        except Exception:
            self.style = ttk.Style(self)

        self.style.configure("TFrame", background=LIGHT_BG)
        self.style.configure("TLabelframe", background=LIGHT_BG, foreground=TEXT_COLOR)
        self.style.configure("TLabelframe.Label", background=LIGHT_BG, foreground=TEXT_COLOR)
        self.style.configure("TLabel", background=LIGHT_BG, foreground=TEXT_COLOR)
        self.style.configure("TCheckbutton", background=LIGHT_BG, foreground=TEXT_COLOR)
        self.style.configure("TEntry", fieldbackground=LIGHT_PANEL_BG, foreground=TEXT_COLOR)
        self.style.configure("TCombobox", fieldbackground=LIGHT_PANEL_BG, foreground=TEXT_COLOR)
        self.style.configure("TButton", background="#dbe8ff", foreground=TEXT_COLOR)
        self.style.map("TButton", background=[("active", "#c9dcff")])
        self.style.configure("Accent.TButton", background=ACCENT_COLOR, foreground="#ffffff")
        self.style.map("Accent.TButton", background=[("active", ACCENT_COLOR_ACTIVE)])
        self.style.configure("Treeview", background=LIGHT_PANEL_BG, fieldbackground=LIGHT_PANEL_BG, foreground=TEXT_COLOR, rowheight=24)
        self.style.configure("Treeview.Heading", background=ACCENT_COLOR, foreground="#ffffff")
        self.style.configure("TProgressbar", troughcolor="#dce4f5", background=ACCENT_COLOR)

        # Нижняя панель
        footer = ttk.Frame(main, padding=(0, pad, 0, 0))
        footer.pack(fill="x", expand=False)
        note = ttk.Label(
            footer,
            text="Соблюдайте авторские права и условия YouTube. Загружайте только то, на что у вас есть права.",
            foreground=MUTED_TEXT_COLOR
        )
        note.pack(side="left")
        # Версия yt-dlp
        self.ydl_version_var = tk.StringVar(value=f"yt-dlp {getattr(yt_dlp, '__version__', '?')}")
        ver_lbl = ttk.Label(footer, textvariable=self.ydl_version_var, foreground=MUTED_TEXT_COLOR)
        ver_lbl.pack(side="right")

        self._refresh_audio_quality_values()
        self._on_audio_only_toggle(init=True)
        self._update_subtitle_display()
        self._on_subtitle_option_changed(init=True)

    # ------------------------ Доп. помощники UI ------------------------

    def _refresh_audio_quality_values(self):
        if not hasattr(self, "audio_quality_cb"):
            return
        fmt_key = self.audio_format_var.get() or DEFAULT_AUDIO_FORMAT
        fmt = AUDIO_FORMAT_OPTIONS.get(fmt_key, AUDIO_FORMAT_OPTIONS[DEFAULT_AUDIO_FORMAT])
        values = fmt.get("bitrate_values", [])
        if values:
            self.audio_quality_cb.configure(values=values, state="readonly")
            if self.audio_quality_var.get() not in values:
                self.audio_quality_var.set(values[0])
        else:
            self.audio_quality_cb.configure(values=[], state="disabled")
            self.audio_quality_var.set("0")

    def _on_audio_format_selected(self):
        label = self.audio_format_label_var.get()
        fmt = self.audio_format_map.get(label, DEFAULT_AUDIO_FORMAT)
        self.audio_format_var.set(fmt)
        self._refresh_audio_quality_values()
        self._save_settings_debounced()

    def _on_audio_only_toggle(self, init: bool = False):
        only_audio = bool(self.audio_only_var.get())
        state_main = "disabled" if only_audio else "readonly"
        for widget in [self.quality_cb, self.vcodec_cb, self.acodec_cb, self.container_cb]:
            try:
                widget.configure(state=state_main)
            except Exception:
                pass
        audio_state = "readonly" if only_audio else "disabled"
        try:
            self.audio_format_cb.configure(state=audio_state)
        except Exception:
            pass
        if only_audio:
            self._refresh_audio_quality_values()
        if self.audio_quality_cb:
            try:
                if audio_state == "disabled":
                    self.audio_quality_cb.configure(state="disabled")
                elif self.audio_quality_cb.cget("state") == "disabled":
                    self.audio_quality_cb.configure(state="readonly")
            except Exception:
                pass
        if not init:
            self._save_settings_debounced()

    def _on_subtitle_option_changed(self, init: bool = False):
        if not self.write_subs_var.get():
            self.embed_subs_var.set(0)
        state = "normal" if self.write_subs_var.get() else "disabled"
        if hasattr(self, 'embed_subs_check'):
            self.embed_subs_check.configure(state=state)
        if hasattr(self, 'subtitle_select_btn'):
            self.subtitle_select_btn.configure(state=state)
        if not init:
            self._save_settings_debounced()

    def _update_subtitle_display(self):
        if not hasattr(self, "subtitle_display_var"):
            return
        if self.selected_subtitle_langs:
            self.subtitle_display_var.set(", ".join(sorted(self.selected_subtitle_langs)))
        else:
            self.subtitle_display_var.set("Не выбрано")

    def _open_subtitle_selector(self):
        if not self.available_subtitle_languages:
            messagebox.showinfo(
                "Субтитры",
                "Список доступных субтитров пока неизвестен. Нажмите «Анализировать» для получения метаданных.",
            )
            return

        win = tk.Toplevel(self)
        win.title("Выбор субтитров")
        win.transient(self)
        win.grab_set()
        pad = 10

        frame = ttk.Frame(win, padding=pad)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Выберите языки субтитров (можно несколько):").pack(anchor="w", pady=(0, 5))

        listbox = tk.Listbox(frame, selectmode="multiple", height=10)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for idx, lang in enumerate(sorted(self.available_subtitle_languages)):
            listbox.insert("end", lang)
            if lang in self.selected_subtitle_langs:
                listbox.selection_set(idx)

        btns = ttk.Frame(frame)
        btns.pack(fill="x", expand=False, pady=(pad, 0))

        def on_ok():
            selections = [listbox.get(i) for i in listbox.curselection()]
            self.selected_subtitle_langs = selections
            self._update_subtitle_display()
            self._save_settings_debounced()
            win.destroy()

        ttk.Button(btns, text="Отмена", command=win.destroy).pack(side="right")
        ttk.Button(btns, text="Сохранить", style="Accent.TButton", command=on_ok).pack(side="right", padx=(0, 5))

    def _flatten_info_entries(self, info: dict) -> List[dict]:
        if not isinstance(info, dict):
            return []
        if info.get("_type") in ("playlist", "multi_video") and info.get("entries"):
            entries = []
            for entry in info.get("entries", []):
                if isinstance(entry, dict):
                    entries.append(entry)
            return entries or [info]
        return [info]

    def _update_languages_from_info(self, info: dict):
        entries = self._flatten_info_entries(info)
        audio_langs = set()
        subtitle_langs = set(self.available_subtitle_languages)

        for entry in entries:
            for fmt in entry.get("formats", []) or []:
                lang = fmt.get("language") or fmt.get("language_preference") or fmt.get("language_code")
                if lang:
                    audio_langs.add(lang.lower())
            subtitles = entry.get("subtitles") or {}
            for lang_code in subtitles.keys():
                if lang_code:
                    subtitle_langs.add(lang_code)

        if audio_langs:
            others = sorted({l for l in audio_langs if l.lower() not in ("", "und", "none", "orig")})
            normalized = ["orig"] + others
            self.available_audio_languages = normalized
            current = self.alang_var.get()
            self.alang_cb.configure(values=normalized)
            if current not in normalized:
                self.alang_var.set("orig")

        self.available_subtitle_languages = sorted(subtitle_langs)
        self.selected_subtitle_langs = [lang for lang in self.selected_subtitle_langs if lang in self.available_subtitle_languages]
        self._update_subtitle_display()

        langs_preview = ", ".join(self.available_audio_languages[:6])
        subs_preview = ", ".join(self.available_subtitle_languages[:6]) or "—"
        self._append_log(f"Анализ завершён. Аудиодорожки: {langs_preview or '—'} | Субтитры: {subs_preview}")
        self._set_analyze_state(
            "done",
            f"Найдено аудио: {len(self.available_audio_languages)} | субтитры: {len(self.available_subtitle_languages)}",
        )

    def _set_analyze_state(self, state: str, message: str = ""):
        if not hasattr(self, "analyze_btn"):
            return

        def apply():
            try:
                if state == "running":
                    self.analyze_btn.configure(state="disabled", text="Анализ…")
                    self.analyze_status_var.set(message or "Получаем данные…")
                elif state == "done":
                    self.analyze_btn.configure(state="normal", text="Анализировать")
                    self.analyze_status_var.set(message)
                elif state == "error":
                    self.analyze_btn.configure(state="normal", text="Анализировать")
                    self.analyze_status_var.set(message or "Не удалось получить метаданные")
                else:
                    self.analyze_btn.configure(state="normal", text="Анализировать")
                    self.analyze_status_var.set(message)
            except Exception:
                pass

        self.after(0, apply)

    def _on_fetch_metadata_clicked(self):
        url = (self.url_var.get() or "").strip()
        if not url:
            messagebox.showwarning("Введите ссылку", "Пожалуйста, вставьте ссылку для анализа.")
            return
        if self._metadata_fetching:
            return
        self._metadata_fetching = True
        self._set_analyze_state("running")
        self._append_log("Анализируем ссылку через yt-dlp...")
        threading.Thread(target=self._fetch_metadata_worker, args=(url,), daemon=True).start()

    def _fetch_metadata_worker(self, url: str):
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
        }
        if not bool(self.playlist_var.get()):
            opts["noplaylist"] = True
        cookies = (self.cookies_var.get() or "").strip()
        if cookies:
            opts["cookiefile"] = cookies
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            self.after(0, lambda info=info: self._update_languages_from_info(info))
        except Exception as e:
            self._append_log(f"Не удалось получить метаданные: {e}")
            self._set_analyze_state("error", "Ошибка при анализе")
            self.after(0, lambda: messagebox.showerror("Анализ", f"Не удалось получить метаданные: {e}"))
        finally:
            self.after(0, lambda: setattr(self, "_metadata_fetching", False))

    # ------------------------ Помощники UI ------------------------

    def _check_ffmpeg(self):
        if shutil.which("ffmpeg") is None:
            self._append_log("⚠ ffmpeg не найден. Для объединения видео и аудио его необходимо установить и добавить в PATH.")
            self._append_log("   Рекомендуем установить ffmpeg с официального сайта или через пакетный менеджер.")

    def _paste_from_clipboard(self):
        try:
            text = self.clipboard_get()
            self.url_var.set(text.strip())
        except Exception:
            pass

    def _choose_outdir(self):
        path = filedialog.askdirectory(title="Выберите папку для сохранения", initialdir=self.outdir_var.get() or os.path.expanduser("~"))
        if path:
            self.outdir_var.set(path)

    def _choose_cookies(self):
        path = filedialog.askopenfilename(
            title="Выберите cookies.txt (Netscape формат)",
            filetypes=[("Текстовые файлы", "*.txt"), ("Все файлы", "*.*")],
        )
        if path:
            self.cookies_var.set(path)

    def _append_log(self, msg: str):
        if not isinstance(msg, str):
            msg = str(msg)
        timestamp = time.strftime("%H:%M:%S")
        full = f"[{timestamp}] {msg}"

        def write():
            try:
                self.log_main_text.configure(state="normal")
                self.log_main_text.insert("end", full + "\n")
                self.log_main_text.see("end")
                self.log_main_text.configure(state="disabled")
            except Exception:
                pass

        self.log_main_text.after(0, write)

    def _append_raw(self, msg: str):
        if not isinstance(msg, str):
            msg = str(msg)

        def write():
            try:
                self.log_raw_text.configure(state="normal")
                self.log_raw_text.insert("end", msg + "\n")
                self.log_raw_text.see("end")
                self.log_raw_text.configure(state="disabled")
            except Exception:
                pass

        self.log_raw_text.after(0, write)

    def _append_raw_throttled(self, msg: str, percent: float):
        """Печатаем подробные строки не чаще 2 раз/с и не чаще, чем при изменении прогресса на 0.5%."""
        now = time.time()
        if (now - self._last_raw_line_ts) < 0.5 and (percent - self._last_raw_percent) < 0.5:
            return
        self._last_raw_line_ts = now
        self._last_raw_percent = percent
        self._append_raw(msg)

    # ---- нормализация коротких названий кодеков ----
    def _short_vcodec(self, s: Optional[str]) -> str:
        if not s:
            return "?"
        ss = s.lower()
        if ss.startswith("av01"):
            return "av01"
        if ss.startswith("vp09") or ss == "vp9":
            return "vp9"
        if ss.startswith("avc1") or ss.startswith("h264"):
            return "h264"
        return s

    def _short_acodec(self, s: Optional[str]) -> str:
        if not s:
            return "?"
        ss = s.lower()
        if ss.startswith("mp4a") or ss == "aac":
            return "aac"
        if ss.startswith("opus"):
            return "opus"
        if ss.startswith("vorbis") or ss == "vorbis":
            return "vorbis"
        return s

    def _set_status(self, text: str):
        base = text
        if self._current_title and not base.strip().startswith("«"):
            base = f"«{self._ellipsize(self._current_title, MAX_UI_TITLE)}» — {base}"
        if self._extra_status_suffix:
            self.status_var.set(f"{base}  |  {self._extra_status_suffix}")
        else:
            self.status_var.set(base)

    # ------------------------ Пост-именной санитайзер ------------------------

    def _sanitize_title(self, title: Optional[str]) -> str:
        s = (title or "").strip()
        for ch in '<>:"/\\|?*':
            s = s.replace(ch, " ")
        s = s.replace("\n", " ").replace("\r", " ")
        s = " ".join(s.split())
        s = s.strip(" .")
        return s  # длину больше НЕ режем здесь; режем дальше умно

    # ------------------------ Построение фильтров ------------------------

    def _desired_height(self) -> int:
        choice = self.quality_var.get()
        mapping = {
            "480p": 480,
            "720p": 720,
            "1080p": 1080,
            "1440p (2K)": 1440,
            "2160p (4K)": 2160,
            "4320p (8K)": 4320
        }
        return mapping.get(choice, 1080)

    def _norm_vcodec_choice(self, s: str) -> str:
        if s.startswith("AV1"):
            return "av1"
        if s.startswith("VP9"):
            return "vp9"
        if s.startswith("H.264"):
            return "h264"
        return "auto"

    def _norm_acodec_choice(self, s: str) -> str:
        if s.startswith("Opus"):
            return "opus"
        if s.startswith("AAC"):
            return "aac"
        if s.startswith("Vorbis"):
            return "vorbis"
        return "auto"

    def _norm_container_choice(self, s: str) -> str:
        return "auto" if s.lower().startswith("авто") else s.lower()

    def _resolve_codecs_for_container(self, vcodec: str, acodec: str, container: str):
        """
        Корректировка кодеков под контейнер.
        mp4: H.264 + AAC; webm: AV1/VP9 + Opus/Vorbis; mkv/auto: любые.
        """
        warn = None
        eff_v = vcodec
        eff_a = acodec

        if container == "mp4":
            if vcodec in ("av1", "vp9", "auto"):
                if vcodec in ("av1", "vp9"):
                    warn = "MP4: видео кодек скорректирован на H.264."
                eff_v = "h264"
            if acodec in ("opus", "vorbis", "auto"):
                if acodec in ("opus", "vorbis"):
                    warn = (warn + " " if warn else "") + "MP4: аудио кодек скорректирован на AAC."
                eff_a = "aac"

        elif container == "webm":
            if vcodec in ("h264",):
                warn = "WEBM: видео кодек скорректирован на VP9."
                eff_v = "vp9"
            if acodec in ("aac",):
                warn = (warn + " " if warn else "") + "WEBM: аудио кодек скорректирован на Opus."
                eff_a = "opus"

        return eff_v, eff_a, warn

    def _format_selector(self, height: int, vcodec: str, acodec: str, alang: str) -> str:
        vfilter = f"bestvideo[height<=?{height}]"
        if vcodec == "av1":
            vfilter += "[vcodec^=av01]"
        elif vcodec == "vp9":
            vfilter += "[vcodec=vp9]"
        elif vcodec == "h264":
            vfilter += "[vcodec^=avc1]"

        afilter = "bestaudio"
        if acodec == "aac":
            afilter += "[acodec^=mp4a]"
        elif acodec == "opus":
            afilter += "[acodec=opus]"
        elif acodec == "vorbis":
            afilter += "[acodec=vorbis]"
        # Фильтр по языку аудиодорожки
        if alang == "ru":
            afilter += "[language^=ru]"
        elif alang == "en":
            afilter += "[language^=en]"

        return f"{vfilter}+{afilter}/best[height<=?{height}]"

    def _build_outtmpl_simple(self, user_tmpl: str, outdir: str) -> str:
        """Без префиксов — дадим yt-dlp сохранить %(title)s.%(ext)s, а потом переименуем сами."""
        return os.path.join(outdir, user_tmpl)

    # ------------------------ События кнопок ------------------------

    def _url_from_clipboard_if_url(self) -> Optional[str]:
        try:
            clip = (self.clipboard_get() or "").strip()
        except Exception:
            return None
        if clip.lower().startswith(("http://", "https://")):
            return clip
        if "youtu" in clip and (clip.startswith("www.") or clip.startswith("youtu")):
            return "https://" + clip if not clip.startswith("http") else clip
        return None

    def _on_download_clicked(self):
        url = (self.url_var.get() or "").strip()
        if not url:
            clip_url = self._url_from_clipboard_if_url()
            if clip_url:
                self.url_var.set(clip_url)
                url = clip_url
                self._append_log("Ссылка взята из буфера обмена.")
        if not url:
            messagebox.showwarning("Введите ссылку", "Пожалуйста, вставьте ссылку на видео YouTube.")
            return

        preset = self._collect_preset()
        if not preset:
            return

        os.makedirs(preset.outdir, exist_ok=True)

        self.cancel_event.clear()
        self.progress["value"] = 0
        self._set_status("Подготовка...")
        self._append_log("Запуск загрузки (одиночная)...")
        self._toggle_controls(downloading=True, queue_mode=False)

        self.download_thread = threading.Thread(target=self._run_single_download_thread, args=(url, preset, None), daemon=True)
        self.download_thread.start()

    def _on_cancel_clicked(self):
        if (self.download_thread and self.download_thread.is_alive()) or (self.queue_thread and self.queue_thread.is_alive()):
            self.cancel_event.set()
            self._append_log("Запрошена отмена. Дождитесь завершения текущей операции...")

    def _on_add_to_queue(self):
        clip_url = self._url_from_clipboard_if_url()
        if clip_url:
            self.url_var.set(clip_url)
        url = (self.url_var.get() or "").strip()

        if not url:
            messagebox.showwarning("Введите ссылку", "Скопируйте ссылку в буфер обмена или вставьте её вручную.")
            return

        preset = self._collect_preset()
        if not preset:
            return
        os.makedirs(preset.outdir, exist_ok=True)

        item = QueueItem(url=url, preset=preset)
        self.queue.append(item)
        self._queue_insert_tv(item)
        self._append_log(f"Добавлено в очередь: {url}")
        self._save_settings_debounced()
        self._probe_title_async(item)

    def _on_load_txt_to_queue(self):
        path = filedialog.askopenfilename(
            title="Выберите .txt со ссылками (по одной на строку)",
            filetypes=[("Текстовые файлы", "*.txt"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        preset = self._collect_preset()
        if not preset:
            return
        count = 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except UnicodeDecodeError:
            with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
                lines = f.readlines()
        for line in lines:
            url = line.strip()
            if not url or url.startswith("#"):
                continue
            item = QueueItem(url=url, preset=preset)
            self.queue.append(item)
            self._queue_insert_tv(item)
            self._probe_title_async(item)
            count += 1
        self._append_log(f"Из файла добавлено ссылок: {count}")
        self._save_settings_debounced()

    def _on_start_queue(self):
        if self.queue_running:
            messagebox.showinfo("Очередь", "Очередь уже выполняется.")
            return
        if not self.queue:
            messagebox.showwarning("Очередь пуста", "Добавьте ссылки в очередь.")
            return

        self.cancel_event.clear()
        self.queue_running = True
        self._set_status("Старт очереди...")
        self._append_log("Старт очереди загрузок.")
        self._toggle_controls(downloading=True, queue_mode=True)

        self.queue_thread = threading.Thread(target=self._run_queue, daemon=True)
        self.queue_thread.start()

    def _on_clear_queue(self):
        if self.queue_running:
            messagebox.showwarning("Нельзя очистить", "Сначала остановите/дождитесь выполнения очереди.")
            return
        self.queue.clear()
        for row in self.queue_tv.get_children():
            self.queue_tv.delete(row)
        self._append_log("Очередь очищена.")

    # -------- Обновление yt-dlp --------

    def _on_update_yt_dlp(self):
        if self.queue_running or (self.download_thread and self.download_thread.is_alive()):
            messagebox.showinfo("Занято", "Сначала завершите текущую загрузку или очередь.")
            return
        if self._updating:
            return
        self._updating = True
        self._append_log("Проверка и обновление yt-dlp через pip…")
        self._set_status("Обновление yt-dlp...")
        self._toggle_controls(downloading=True, queue_mode=False)
        threading.Thread(target=self._update_yt_dlp_worker, daemon=True).start()

    def _run_pip_and_stream(self, cmd: list) -> int:
        self._append_log(f"→ Запуск: {' '.join(cmd)}")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except Exception as e:
            self._append_log(f"[pip] не удалось запустить: {e}")
            return -1
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                self._append_log(f"[pip] {line.rstrip()}")
        except Exception:
            pass
        return proc.wait()

    def _update_yt_dlp_worker(self):
        try:
            cmd1 = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"]
            rc = self._run_pip_and_stream(cmd1)

            if rc != 0:
                self._append_log("Обычное обновление не удалось. Пробуем с флагом --user…")
                cmd2 = [sys.executable, "-m", "pip", "install", "-U", "--user", "yt-dlp"]
                rc = self._run_pip_and_stream(cmd2)

            if rc == 0:
                try:
                    import yt_dlp as _ydl_mod
                    importlib.reload(_ydl_mod)
                    global YoutubeDL, yt_dlp
                    yt_dlp = _ydl_mod
                    YoutubeDL = yt_dlp.YoutubeDL
                    new_ver = getattr(yt_dlp, "__version__", "?")
                    self._append_log(f"✅ yt-dlp успешно обновлён до версии {new_ver}.")
                    self.after(0, lambda: self.ydl_version_var.set(f"yt-dlp {new_ver}"))
                except Exception as e:
                    self._append_log(f"yt-dlp обновлён, но не удалось перезагрузить модуль в памяти: {e}")
                    self._append_log("Совет: перезапустите приложение, чтобы использовать новую версию.")
                finally:
                    self._set_status("Обновление завершено ✅")
            else:
                self._append_log("❌ Не удалось обновить yt-dlp. Проверьте соединение и права.")
                self._set_status("Ошибка обновления yt-dlp.")
                self.after(0, lambda: messagebox.showerror("Обновление yt-dlp", "Не удалось обновить yt-dlp. Попробуйте вручную в терминале:\n\npip install -U yt-dlp\n\nили\n\npip install -U --user yt-dlp"))
        finally:
            self._updating = False
            self._toggle_controls(downloading=False, queue_mode=False)

    # ------------------------ Очередь ------------------------

    def _run_queue(self):
        try:
            for item in list(self.queue):
                if self.cancel_event.is_set():
                    self._append_log("Очередь прервана пользователем.")
                    break
                self._queue_update_status(item, "В процессе")
                try:
                    idx = self.queue.index(item) + 1
                except ValueError:
                    idx = 1
                self._set_status(f"Очередь: элемент {idx}/{len(self.queue)} — подготовка...")
                self.progress["value"] = 0
                result = self._run_single_download(url=item.url, preset=item.preset, queue_item=item)
                if result == "success":
                    self._queue_remove_item(item)
                    self._append_log("Задача выполнена и удалена из очереди.")
                else:
                    self._append_log(f"Задача завершилась со статусом: {item.status}")
                if self.cancel_event.is_set():
                    break
            else:
                self._append_log("Очередь завершена.")
                self._set_status("Очередь завершена ✅")
        finally:
            self.queue_running = False
            self._toggle_controls(downloading=False, queue_mode=True)

    # ------------------------ Загрузка ------------------------

    def _run_single_download_thread(self, url: str, preset: DownloadPreset, queue_item: Optional[QueueItem]):
        self._run_single_download(url, preset, queue_item)
        self._toggle_controls(downloading=False, queue_mode=False)

    def _extract_selected_formats(self, info: dict) -> Tuple[Optional[dict], Optional[dict]]:
        vfmt, afmt = None, None
        try:
            req = info.get("requested_formats") or []
            if req:
                for f in req:
                    vcodec = f.get("vcodec")
                    acodec = f.get("acodec")
                    if vcodec and vcodec != "none":
                        vfmt = f
                    if acodec and acodec != "none":
                        afmt = f if f is not vfmt else afmt
            else:
                if (info.get("vcodec") and info.get("vcodec") != "none") and (info.get("acodec") and info.get("acodec") != "none"):
                    vfmt, afmt = info, info
                elif info.get("vcodec") and info.get("vcodec") != "none":
                    vfmt = info
                elif info.get("acodec") and info.get("acodec") != "none":
                    afmt = info
        except Exception:
            pass
        return vfmt, afmt

    def _extract_final_codecs(self, info: dict) -> Tuple[Optional[str], Optional[str]]:
        v, a = None, None
        try:
            rd = info.get("requested_downloads") or []
            for f in rd:
                if not v and f.get("vcodec") and f.get("vcodec") != "none":
                    v = f.get("vcodec")
                if not a and f.get("acodec") and f.get("acodec") != "none":
                    a = f.get("acodec")
        except Exception:
            pass
        if not v or not a:
            vfmt, afmt = self._extract_selected_formats(info)
            if not v and vfmt:
                v = vfmt.get("vcodec")
            if not a and afmt:
                a = afmt.get("acodec")
        if not v:
            v = info.get("vcodec") or info.get("video_codec")
        if not a:
            a = info.get("acodec") or info.get("audio_codec")
        return v, a

    def _extract_final_height(self, info: dict) -> Optional[int]:
        try:
            rd = info.get("requested_downloads") or []
            for f in rd:
                if f.get("height"):
                    return int(f.get("height"))
            vfmt, _ = self._extract_selected_formats(info)
            if vfmt and vfmt.get("height"):
                return int(vfmt.get("height"))
            if info.get("height"):
                return int(info.get("height"))
        except Exception:
            pass
        return None

    def _guess_final_ext(self, vfmt: Optional[dict], afmt: Optional[dict], container_choice: str) -> str:
        if container_choice != "auto":
            return container_choice
        try:
            v_ext = (vfmt or {}).get("ext") or (vfmt or {}).get("container")
            a_ext = (afmt or {}).get("ext") or (afmt or {}).get("container")
            if v_ext and a_ext and v_ext == a_ext and v_ext in ("mp4", "webm", "mkv"):
                return v_ext
        except Exception:
            pass
        return "mkv"

    def _format_summary_line(self, f: dict, kind: str) -> str:
        try:
            fmt_id = f.get("format_id", "?")
            ext = f.get("ext") or f.get("container") or "?"
            vcodec = f.get("vcodec", "none")
            acodec = f.get("acodec", "none")
            tbr = f.get("tbr")
            abr = f.get("abr")
            fps = f.get("fps")
            height = f.get("height")
            width = f.get("width")
            approx = f.get("filesize_approx") or f.get("filesize")
            size_txt = f"~{human_readable_size(approx)}" if approx else (f"{int(tbr)} kbps" if tbr else "?")
            if kind == "video":
                res = f"{height}p" if height else (f"{width}x{height}" if width and height else "?")
                fps_txt = f"@{int(fps)}fps" if fps else ""
                return f"Видео: id={fmt_id} | {res}{fps_txt} | vcodec={vcodec} | контейнер={ext} | {size_txt}"
            else:
                abr_txt = f"{int(abr)} kbps" if abr else (f"{int(tbr)} kbps" if tbr else "?")
                return f"Аудио: id={fmt_id} | acodec={acodec} | контейнер={ext} | {abr_txt}"
        except Exception:
            return f"{kind.capitalize()}: ?"

    def _postprocessor_hook(self, d: dict):
        try:
            status = d.get("status")
            pp = d.get("postprocessor") or d.get("postprocessor_name") or "postprocessor"
            if status == "started":
                self._append_log(f"Пост-обработка: {pp} — старт.")
            elif status == "finished":
                info_dict = d.get("info_dict") or {}
                final_name = info_dict.get("__final_filename") or info_dict.get("filepath")
                ext = info_dict.get("ext")
                if final_name:
                    self._append_log(f"Пост-обработка завершена. Итоговый файл: {os.path.basename(final_name)}")
                if ext:
                    self._append_log(f"Итоговый контейнер: {str(ext).upper()}")
            elif status == "error":
                self._append_log(f"[ОШИБКА пост-обработки] {pp}")
        except Exception:
            pass

    def _run_single_download(self, url: str, preset: DownloadPreset, queue_item: Optional[QueueItem]) -> str:
        self._current_title = None
        self._last_raw_line_ts = 0.0
        self._last_raw_percent = -1.0

        height = preset.height
        vch_gui = preset.vcodec_choice
        ach_gui = preset.acodec_choice
        c_gui = preset.container_choice

        vch = self._norm_vcodec_choice(vch_gui)
        ach = self._norm_acodec_choice(ach_gui)
        container_choice = self._norm_container_choice(c_gui)
        # --- Режим: только аудио ---
        if getattr(preset, 'audio_only', False):
            fmt_candidates = []
            lang = (preset.alang_choice or '').lower()
            if lang and lang != 'orig':
                fmt_candidates.append(f"bestaudio[language^={lang}]")
            fmt_candidates.append('bestaudio')
            fmt = '/'.join(fmt_candidates)

            fmt_info = AUDIO_FORMAT_OPTIONS.get(preset.audio_format, AUDIO_FORMAT_OPTIONS[DEFAULT_AUDIO_FORMAT])
            codec = fmt_info['codec']
            extension = fmt_info.get('extension', codec)
            bitrate_values = fmt_info.get('bitrate_values') or []
            quality = preset.audio_quality or DEFAULT_AUDIO_QUALITY
            quality_txt = f"{quality} kbps" if bitrate_values else "без сжатия"

            self._append_log(
                f"Режим: только аудио {fmt_info['label']} | Язык аудио: {preset.alang_choice} | "
                f"Качество: {quality_txt}"
            )

            outtmpl = self._build_outtmpl_simple(preset.outtmpl_user, preset.outdir)
            logger = TkLogger(self.log_main_text, self.log_raw_text)
            postprocessors = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': codec,
            }]
            if bitrate_values:
                postprocessors[0]['preferredquality'] = str(quality)

            run_opts = {
                'format': fmt,
                'noplaylist': not getattr(preset, 'download_playlist', False),
                'outtmpl': outtmpl,
                'logger': logger,
                'concurrent_fragment_downloads': 5,
                'continuedl': True,
                'overwrites': False,
                'restrictfilenames': False,
                'windowsfilenames': True,
                'quiet': False,
                'no_warnings': False,
                'postprocessor_hooks': [self._postprocessor_hook],
                'postprocessors': postprocessors,
            }
            if preset.cookies:
                run_opts['cookiefile'] = preset.cookies
            try:
                self._set_status('Скачивание аудио...')
                with YoutubeDL(run_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    try:
                        if 'requested_downloads' in info and info['requested_downloads']:
                            self.last_output_path = info['requested_downloads'][0].get('filepath')
                            if queue_item:
                                queue_item.result_path = self.last_output_path
                    except Exception:
                        pass
                    title_final = info.get('title') or self._current_title
                    video_id = info.get('id')
                    a_short = codec
                    v_short = 'audio'
                    final_ext = extension
                    self._extra_status_suffix = f"A:{a_short.upper()} → {final_ext.upper()}"
                    is_playlist = info.get('_type') in ('playlist', 'multi_video') or bool(info.get('entries'))
                    if not is_playlist:
                        self._auto_rename_result(self.last_output_path, v_short, a_short, None, final_ext,
                                                 title_hint=title_final, video_id_hint=video_id)
                    else:
                        self._append_log("Плейлист: используется шаблон имён yt-dlp для каждого трека.")
                self.progress.after(0, lambda: self.progress.configure(value=100))
                self._set_status('Готово ✅')
                self._append_log('Загрузка аудио завершена.')
                if queue_item:
                    queue_item.status = 'Готово'
                    self._queue_update_status(queue_item, 'Готово')
                return 'success'
            except KeyboardInterrupt:
                self._set_status('Загрузка отменена.')
                self._append_log('Загрузка отменена пользователем.')
                if queue_item:
                    queue_item.status = 'Отменено'
                    self._queue_update_status(queue_item, 'Отменено')
                return 'cancel'
            except Exception as e:
                self._set_status('Ошибка.')
                self._append_log(f'Ошибка загрузки аудио: {e}')
                if queue_item:
                    queue_item.status = 'Ошибка'
                    self._queue_update_status(queue_item, 'Ошибка')
                self.after(0, lambda: messagebox.showerror('Ошибка загрузки', f"{e}"))
                return 'error'


        eff_v, eff_a, warn = self._resolve_codecs_for_container(vch, ach, container_choice)
        if warn:
            self._append_log(warn)


        fmt = self._format_selector(height, eff_v if eff_v != "auto" else "auto", eff_a if eff_a != "auto" else "auto", preset.alang_choice)
        self._append_log(
            f"Целевое качество: до {height}p | Видеокодек: {('Авто' if eff_v=='auto' else eff_v.upper())} | "
            f"Аудиокодек: {('Авто' if eff_a=='auto' else eff_a.upper())} | Аудиоязык: {preset.alang_choice} | "
            f"Контейнер: {('Авто' if container_choice=='auto' else container_choice)}"
        )
        self._append_log(f"Формат выбора (yt-dlp): {fmt}")
        if preset.cookies:
            self._append_log(f"Используется cookie-файл: {preset.cookies}")

        outtmpl = self._build_outtmpl_simple(preset.outtmpl_user, preset.outdir)

        logger = TkLogger(self.log_main_text, self.log_raw_text)

        base_opts = {
            "format": fmt,
            "noplaylist": not getattr(preset, 'download_playlist', False),
            "outtmpl": outtmpl,
            "logger": logger,
            "concurrent_fragment_downloads": 5,
            "continuedl": True,
            "overwrites": False,
            "restrictfilenames": False,
            "windowsfilenames": True,
            "quiet": False,
            "no_warnings": False,
            "postprocessor_hooks": [self._postprocessor_hook],
        }
        if preset.cookies:
            base_opts["cookiefile"] = preset.cookies

        if getattr(preset, 'write_subtitles', False):
            base_opts["writesubtitles"] = True
            if getattr(preset, 'subtitle_langs', None):
                base_opts["subtitleslangs"] = list(preset.subtitle_langs)
        if getattr(preset, 'embed_subtitles', False) and getattr(preset, 'write_subtitles', False):
            base_opts["embedsubtitles"] = True

        if container_choice != "auto":
            base_opts["merge_output_format"] = container_choice
            # remux для принудительного контейнера
            base_opts["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": container_choice}]

        # ---------- ПРОБА ----------
        try:
            probe_opts = dict(base_opts)
            with YoutubeDL(probe_opts) as ydl_probe:
                info_probe = ydl_probe.extract_info(url, download=False)

            title = info_probe.get("title") or "Без названия"
            ch = info_probe.get("channel") or info_probe.get("uploader") or "?"
            dur = seconds_to_hms(info_probe.get("duration"))
            vid = info_probe.get("id") or "?"
            self._current_title = title
            self._append_log(f"▶ Сейчас скачиваем: «{self._ellipsize(title, MAX_UI_TITLE)}» [{vid}] | канал: {ch} | длительность: {dur}")

            self.after(0, lambda info=info_probe: self._update_languages_from_info(info))

            if queue_item and not queue_item.title:
                queue_item.title = title
                try:
                    self._queue_set_title_cell(queue_item)
                except Exception:
                    pass

            vfmt, afmt = self._extract_selected_formats(info_probe)
            if vfmt:
                self._append_log(self._format_summary_line(vfmt, "video"))
            if afmt:
                self._append_log(self._format_summary_line(afmt, "audio"))
            if not vfmt and not afmt:
                self._append_log("Не удалось определить выбранные форматы заранее (yt-dlp). Продолжаем загрузку...")

            final_ext_guess = self._guess_final_ext(vfmt, afmt, container_choice)
            mode = "принудительно" if container_choice != "auto" else "авто"
            self._append_log(f"Итоговый контейнер (ожидаемо): {final_ext_guess.upper()} ({mode})")

            vshort = self._short_vcodec((vfmt or {}).get("vcodec"))
            ashort = self._short_acodec((afmt or {}).get("acodec"))
            self._extra_status_suffix = f"V:{vshort} A:{ashort} → {final_ext_guess.upper()}"
        except Exception as e_probe:
            self._extra_status_suffix = ""
            self._append_log(f"Не удалось заранее определить форматы: {e_probe}")

        hooks = [self._progress_hook_factory(queue_item=queue_item)]
        run_opts = dict(base_opts)
        run_opts["progress_hooks"] = hooks

        # ---------- Попытка №1 ----------
        try:
            self._set_status("Скачивание...")
            with YoutubeDL(run_opts) as ydl:
                info = ydl.extract_info(url, download=True)

                try:
                    if "requested_downloads" in info and info["requested_downloads"]:
                        self.last_output_path = info["requested_downloads"][0].get("filepath")
                        if queue_item:
                            queue_item.result_path = self.last_output_path
                except Exception:
                    pass

                vcodec_final, acodec_final = self._extract_final_codecs(info)
                v_short = self._short_vcodec(vcodec_final)
                a_short = self._short_acodec(acodec_final)
                height_final = self._extract_final_height(info)

                if run_opts.get("merge_output_format"):
                    final_ext = run_opts["merge_output_format"]
                else:
                    final_ext = (info.get("ext") or "").lower() or "mkv"

                self._extra_status_suffix = f"V:{v_short} A:{a_short} → {str(final_ext).upper()}"

                title_final = info.get("title") or self._current_title
                video_id = info.get("id")
                is_playlist = info.get('_type') in ('playlist', 'multi_video') or bool(info.get('entries'))
                if not is_playlist:
                    self._auto_rename_result(
                        self.last_output_path,
                        v_short,
                        a_short,
                        height_final,
                        final_ext,
                        title_hint=title_final,
                        video_id_hint=video_id,
                    )
                else:
                    self._append_log("Плейлист: автоматическое переименование отключено, используется шаблон yt-dlp.")

            self.progress.after(0, lambda: self.progress.configure(value=100))
            self._set_status("Готово ✅")
            self._append_log("Загрузка завершена.")
            if queue_item:
                queue_item.status = "Готово"
                self._queue_update_status(queue_item, "Готово")
            return "success"
        except KeyboardInterrupt:
            self._set_status("Загрузка отменена.")
            self._append_log("Загрузка отменена пользователем.")
            if queue_item:
                queue_item.status = "Отменено"
                self._queue_update_status(queue_item, "Отменено")
            return "cancel"
        except Exception as e1:
            self._append_log(f"Ошибка/не удалось собрать указанный контейнер: {e1}")
            if self.cancel_event.is_set():
                self._set_status("Загрузка отменена.")
                if queue_item:
                    queue_item.status = "Отменено"
                    self._queue_update_status(queue_item, "Отменено")
                return "cancel"

        # ---------- Попытка №2 — резерв MKV ----------
        try:
            fallback_container = "mkv"
            fallback_opts = dict(run_opts)
            fallback_opts["merge_output_format"] = fallback_container
            fallback_opts["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": fallback_container}]
            self._append_log("Пробуем собрать в MKV как резервный вариант...")
            with YoutubeDL(fallback_opts) as ydl2:
                info2 = ydl2.extract_info(url, download=True)
                try:
                    if "requested_downloads" in info2 and info2["requested_downloads"]:
                        self.last_output_path = info2["requested_downloads"][0].get("filepath")
                        if queue_item:
                            queue_item.result_path = self.last_output_path
                except Exception:
                    pass

                vcodec_final, acodec_final = self._extract_final_codecs(info2)
                v_short = self._short_vcodec(vcodec_final)
                a_short = self._short_acodec(acodec_final)
                height_final = self._extract_final_height(info2)
                self._extra_status_suffix = f"V:{v_short} A:{a_short} → MKV"

                title_final = info2.get("title") or self._current_title
                video_id = info2.get("id")
                is_playlist = info2.get('_type') in ('playlist', 'multi_video') or bool(info2.get('entries'))
                if not is_playlist:
                    self._auto_rename_result(
                        self.last_output_path,
                        v_short,
                        a_short,
                        height_final,
                        "mkv",
                        title_hint=title_final,
                        video_id_hint=video_id,
                    )
                else:
                    self._append_log("Плейлист: автоматическое переименование отключено, используется шаблон yt-dlp.")

            self.progress.after(0, lambda: self.progress.configure(value=100))
            self._set_status("Готово ✅ (MKV)")
            self._append_log("Загрузка завершена (mkv).")
            if queue_item:
                queue_item.status = "Готово (mkv)"
                self._queue_update_status(queue_item, "Готово (mkv)")
            return "success"
        except KeyboardInterrupt:
            self._set_status("Загрузка отменена.")
            self._append_log("Загрузка отменена пользователем.")
            if queue_item:
                queue_item.status = "Отменено"
                self._queue_update_status(queue_item, "Отменено")
            return "cancel"
        except Exception as e2:
            self._set_status("Ошибка.")
            self._append_log(f"Ошибка загрузки: {e2}")
            if queue_item:
                queue_item.status = "Ошибка"
                self._queue_update_status(queue_item, "Ошибка")
            self.after(0, lambda: messagebox.showerror("Ошибка загрузки", f"{e2}"))
            return "error"
        finally:
            self._current_title = None

    # ---- Асинхронная «проба» для названия в очереди ----
    def _probe_title_async(self, item: QueueItem):
        def worker():
            try:
                opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "noplaylist": not getattr(item.preset, 'download_playlist', False),
                }
                if item.preset.cookies:
                    opts["cookiefile"] = item.preset.cookies
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(item.url, download=False)
                title = info.get("title") or "Без названия"
                item.title = title
                self.after(0, lambda: self._queue_set_title_cell(item))
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _queue_set_title_cell(self, item: QueueItem):
        iid = str(id(item))
        try:
            display = self._ellipsize(item.title or "Без названия", MAX_QUEUE_TITLE)
            self.queue_tv.set(iid, "title", display)
        except Exception:
            pass

    def _auto_rename_result(
        self,
        path: Optional[str],
        v_short: str,
        a_short: str,
        height: Optional[int],
        ext_from_info: Optional[str],
        title_hint: Optional[str] = None,
        video_id_hint: Optional[str] = None,
    ):
        """Переименовать итоговый файл в <v>_<a>_<h>_<Title>.<ext> с защитой по длине пути."""
        try:
            if not path or not os.path.isfile(path):
                return

            folder = os.path.dirname(path)
            orig_base, old_ext = os.path.splitext(os.path.basename(path))
            ext = (ext_from_info or old_ext.lstrip(".") or "mkv").lower()

            # 1) Берём нормальное название из info_dict
            base = self._sanitize_title(title_hint)
            if not base:
                base = self._sanitize_title(orig_base)

            # 2) Убираем дублирующееся расширение внутри base (например, "...webm" в заголовке)
            if base.lower().endswith(f".{ext}"):
                base = base[: -(len(ext) + 1)]

            # 3) Крайний fallback — id/время
            if not base:
                vid = (video_id_hint or "").strip()
                base = f"video_{vid}" if vid else f"video_{int(time.time())}"

            h_part = f"{height}" if height else ""
            # Сначала строим базу без учёта ограничения пути
            new_base = f"{v_short}_{a_short}_{h_part}_{base}".replace("__", "_").strip("_")
            new_name = f"{new_base}.{ext}"
            candidate = os.path.join(folder, new_name)

            # 4) Если путь длинный — ужмём только Title (часть после префикса кодеков/высоты)
            if len(candidate) > SAFE_MAX_PATH:
                prefix = f"{v_short}_{a_short}_{h_part}_".replace("__", "_").strip("_")
                if prefix:
                    prefix += "_"
                # сколько максимум можем оставить для Title
                extra = len(candidate) - SAFE_MAX_PATH
                # допустимая длина Title
                allowed = max(MIN_BASE_LEN, len(base) - extra)
                base = self._ellipsize(base, allowed)
                new_base = f"{prefix}{base}".strip("_")
                new_name = f"{new_base}.{ext}"
                candidate = os.path.join(folder, new_name)

            # 5) Защита от коллизий имён
            if os.path.abspath(candidate) != os.path.abspath(path):
                cnt = 1
                unique_candidate = candidate
                while os.path.exists(unique_candidate):
                    unique_candidate = os.path.join(folder, f"{os.path.splitext(new_name)[0]}({cnt}).{ext}")
                    cnt += 1
                os.replace(path, unique_candidate)
                self._append_log(f"Переименовано: {os.path.basename(path)} → {os.path.basename(unique_candidate)}")
                self.last_output_path = unique_candidate
        except Exception as e:
            self._append_log(f"Не удалось переименовать файл: {e}")

    def _progress_hook_factory(self, queue_item: Optional[QueueItem] = None):
        def hook(d):
            if self.cancel_event.is_set():
                raise KeyboardInterrupt("Загрузка отменена пользователем")
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                percent = 0.0
                if total:
                    percent = downloaded / total * 100.0
                speed = d.get("speed")
                eta = d.get("eta")
                self.progress.after(0, lambda p=percent: self.progress.configure(value=p))
                speed_txt = human_readable_size(speed) + "/s" if speed else "Unknown B/s"
                eta_txt = seconds_to_hms(int(eta)) if eta is not None else "Unknown"
                size_txt = f"{human_readable_size(downloaded)} of {human_readable_size(total)}" if total else f"{human_readable_size(downloaded)} of Unknown"
                prefix = ""
                if queue_item:
                    try:
                        idx = self.queue.index(queue_item) + 1
                        prefix = f"[{idx}/{len(self.queue)}] "
                    except Exception:
                        prefix = ""
                self._set_status(f"{prefix}Скачивание: {percent:.1f}%  |  {size_txt}  |  Скорость: {speed_txt}  |  Осталось: {eta_txt}")

                raw_line = f"[download] {percent:5.1f}% of {size_txt} at {speed_txt} ETA {eta_txt}"
                self._append_raw_throttled(raw_line, percent)

            elif status == "finished":
                self.progress.after(0, lambda: self.progress.configure(value=100))
                self._set_status("Файл загружен, идет пост-обработка (объединение/конвертация)...")
                self._append_raw("[download] 100.0% — файл загружен, пост-обработка…")
            elif status == "error":
                self._set_status("Ошибка загрузки.")
                self._append_raw("[download] ERROR")
        return hook

    # ------------------------ Переключение доступности ------------------------

    def _toggle_controls(self, downloading: bool, queue_mode: bool):
        state_main = "disabled" if downloading else "normal"

        for w in [self.quality_cb, self.vcodec_cb, self.acodec_cb, self.container_cb]:
            w.configure(state=state_main)

        for w in [self.cookies_path_entry]:
            w.configure(state=state_main)

        self.download_btn.configure(state=("disabled" if downloading else "normal"))
        self.add_queue_btn.configure(state=("disabled" if downloading else "normal"))
        self.load_txt_btn.configure(state=("disabled" if downloading else "normal"))
        self.update_yt_btn.configure(state=("disabled" if downloading else "normal"))
        self.open_btn.configure(state=("disabled" if downloading else "normal"))

        if downloading and queue_mode:
            self.start_queue_btn.configure(state="disabled")
            self.clear_queue_btn.configure(state="disabled")
        else:
            self.start_queue_btn.configure(state="normal")
            self.clear_queue_btn.configure(state="normal")

        self.cancel_btn.configure(state=("normal" if downloading else "disabled"))

    # ------------------------ Очередь: UI обновления ------------------------

    def _queue_insert_tv(self, item: QueueItem):
        v = self._norm_vcodec_choice(item.preset.vcodec_choice)
        a = self._norm_acodec_choice(item.preset.acodec_choice)
        c = self._norm_container_choice(item.preset.container_choice)
        title_display = self._ellipsize(item.title or "Получаю название…", MAX_QUEUE_TITLE)
        if getattr(item.preset, 'audio_only', False):
            fmt_info = AUDIO_FORMAT_OPTIONS.get(item.preset.audio_format, AUDIO_FORMAT_OPTIONS[DEFAULT_AUDIO_FORMAT])
            values = (
                title_display,
                f"Аудио {fmt_info['label']}",
                '—',
                fmt_info['codec'].upper(),
                fmt_info.get('extension', fmt_info['codec']).upper(),
                item.status,
            )
        else:
            values = (
                title_display,
                f"{item.preset.height}p",
                (v.upper() if v != "auto" else "AUTO"),
                (a.upper() if a != "auto" else "AUTO"),
                (c.upper() if c != "auto" else "AUTO"),
                item.status,
            )
        self.queue_tv.insert("", "end", iid=str(id(item)), values=values)

    def _queue_update_status(self, item: QueueItem, status: str):
        item.status = status
        try:
            self.queue_tv.set(str(id(item)), "status", status)
        except Exception:
            pass

    def _queue_remove_item(self, item: QueueItem):
        try:
            self.queue.remove(item)
        except ValueError:
            pass
        try:
            self.queue_tv.delete(str(id(item)))
        except Exception:
            pass

    # ------------------------ Контекстное меню и редактирование ------------------------

    def _on_queue_right_click(self, event):
        iid = self.queue_tv.identify_row(event.y)
        if iid:
            if iid not in self.queue_tv.selection():
                self.queue_tv.selection_set(iid)
                self.queue_tv.focus(iid)
            state = "disabled" if self.queue_running else "normal"
            try:
                self._queue_menu.entryconfig(self._MENU_IDX_EDIT, state=state)
                self._queue_menu.entryconfig(self._MENU_IDX_DELETE, state=state)
            except Exception:
                pass
            try:
                self._queue_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self._queue_menu.grab_release()
        else:
            self.queue_tv.selection_remove(self.queue_tv.selection())

    def _on_queue_double_click(self, event):
        if self.queue_running:
            return
        sel = self.queue_tv.selection()
        if not sel:
            return
        item = self._queue_item_by_iid(sel[0])
        if item:
            self._edit_queue_item(item)

    def _on_queue_delete_selected(self):
        if self.queue_running:
            messagebox.showwarning("Очередь выполняется", "Остановите очередь перед изменениями.")
            return
        sel = list(self.queue_tv.selection())
        if not sel:
            return
        confirm = messagebox.askyesno(
            "Удалить",
            f"Удалить {('выбранную задачу' if len(sel)==1 else f'{len(sel)} задач(и)')} из очереди?"
        )
        if not confirm:
            return
        removed = 0
        for iid in sel:
            item = self._queue_item_by_iid(iid)
            if item:
                self._queue_remove_item(item)
                removed += 1
        if removed:
            self._append_log(f"Удалено из очереди: {removed}")

    def _on_queue_edit_selected(self):
        if self.queue_running:
            messagebox.showwarning("Очередь выполняется", "Остановите очередь перед изменениями.")
            return
        sel = self.queue_tv.selection()
        if not sel:
            return
        item = self._queue_item_by_iid(sel[0])
        if item:
            self._edit_queue_item(item)

    def _queue_item_by_iid(self, iid: str) -> Optional[QueueItem]:
        for it in self.queue:
            if str(id(it)) == iid:
                return it
        return None

    def _update_queue_tv_row(self, item: QueueItem):
        iid = str(id(item))
        v = self._norm_vcodec_choice(item.preset.vcodec_choice)
        a = self._norm_acodec_choice(item.preset.acodec_choice)
        c = self._norm_container_choice(item.preset.container_choice)
        try:
            if getattr(item.preset, 'audio_only', False):
                fmt_info = AUDIO_FORMAT_OPTIONS.get(item.preset.audio_format, AUDIO_FORMAT_OPTIONS[DEFAULT_AUDIO_FORMAT])
                quality = f"Аудио {fmt_info['label']}"
                self.queue_tv.set(iid, "quality", quality)
                self.queue_tv.set(iid, "vcodec", '—')
                self.queue_tv.set(iid, "acodec", fmt_info['codec'].upper())
                self.queue_tv.set(iid, "container", fmt_info.get('extension', fmt_info['codec']).upper())
            else:
                self.queue_tv.set(iid, "quality", f"{item.preset.height}p")
                self.queue_tv.set(iid, "vcodec", (v.upper() if v != "auto" else "AUTO"))
                self.queue_tv.set(iid, "acodec", (a.upper() if a != "auto" else "AUTO"))
                self.queue_tv.set(iid, "container", (c.upper() if c != "auto" else "AUTO"))
            self._queue_set_title_cell(item)
        except Exception:
            pass

    def _edit_queue_item(self, item: QueueItem):
        win = tk.Toplevel(self)
        win.title("Изменить задачу")
        win.transient(self)
        win.grab_set()
        pad = 10

        def _height_to_label(h: int) -> str:
            mapping = {
                480: "480p", 720: "720p", 1080: "1080p",
                1440: "1440p (2K)", 2160: "2160p (4K)", 4320: "4320p (8K)"
            }
            return mapping.get(h, "1080p")

        q_var = tk.StringVar(value=_height_to_label(item.preset.height))
        v_var = tk.StringVar(value=item.preset.vcodec_choice)
        a_var = tk.StringVar(value=item.preset.acodec_choice)
        c_var = tk.StringVar(value=(item.preset.container_choice if item.preset.container_choice != "auto" else "Авто"))
        outdir_var = tk.StringVar(value=item.preset.outdir)
        outtmpl_var = tk.StringVar(value=item.preset.outtmpl_user)
        cookies_var = tk.StringVar(value=item.preset.cookies or "")
        playlist_var = tk.IntVar(value=1 if getattr(item.preset, 'download_playlist', False) else 0)
        audio_only_var = tk.IntVar(value=1 if getattr(item.preset, 'audio_only', False) else 0)
        audio_format_key = getattr(item.preset, 'audio_format', DEFAULT_AUDIO_FORMAT)
        audio_quality_value = getattr(item.preset, 'audio_quality', DEFAULT_AUDIO_QUALITY)
        write_subs_var = tk.IntVar(value=1 if getattr(item.preset, 'write_subtitles', False) else 0)
        embed_subs_var = tk.IntVar(value=1 if getattr(item.preset, 'embed_subtitles', False) else 0)
        subtitle_var = tk.StringVar(value=",".join(getattr(item.preset, 'subtitle_langs', ()) or []))

        frm = ttk.Frame(win)
        frm.pack(fill="both", expand=True, padx=pad, pady=pad)

        ttk.Label(frm, text="Качество:").grid(row=0, column=0, sticky="w", padx=(0, 5), pady=(0, 5))
        q_cb = ttk.Combobox(frm, textvariable=q_var, state="readonly",
                            values=["480p", "720p", "1080p", "1440p (2K)", "2160p (4K)", "4320p (8K)"], width=18)
        q_cb.grid(row=0, column=1, sticky="w", pady=(0, 5))

        ttk.Label(frm, text="Видео кодек:").grid(row=0, column=2, sticky="w", padx=(15, 5), pady=(0, 5))
        v_cb = ttk.Combobox(frm, textvariable=v_var, state="readonly",
                            values=["Авто", "AV1 (av01)", "VP9 (vp9)", "H.264 (avc1)"], width=18)
        v_cb.grid(row=0, column=3, sticky="w", pady=(0, 5))

        ttk.Label(frm, text="Аудио кодек:").grid(row=1, column=0, sticky="w", padx=(0, 5), pady=5)
        a_cb = ttk.Combobox(frm, textvariable=a_var, state="readonly",
                            values=["Авто", "Opus (opus)", "AAC (mp4a)", "Vorbis (vorbis)"], width=18)
        a_cb.grid(row=1, column=1, sticky="w")

        available_alangs = sorted(set(self.available_audio_languages + [item.preset.alang_choice, "orig"]))
        ttk.Label(frm, text="Язык аудио:").grid(row=1, column=2, sticky="w", padx=(15, 5), pady=5)
        alang_var = tk.StringVar(value=item.preset.alang_choice if hasattr(item.preset, "alang_choice") else "orig")
        alang_cb = ttk.Combobox(frm, textvariable=alang_var, state="readonly", values=available_alangs, width=10)
        alang_cb.grid(row=1, column=3, sticky="w")

        ttk.Label(frm, text="Контейнер:").grid(row=2, column=0, sticky="w", padx=(0, 5), pady=5)
        c_cb = ttk.Combobox(frm, textvariable=c_var, state="readonly",
                            values=["Авто", "mp4", "mkv", "webm"], width=18)
        c_cb.grid(row=2, column=1, sticky="w")
        playlist_cb = ttk.Checkbutton(frm, text="Скачать плейлист целиком", variable=playlist_var)
        playlist_cb.grid(row=2, column=2, columnspan=2, sticky="w")

        audio_format_var = tk.StringVar(value=audio_format_key)
        audio_format_label_var = tk.StringVar(value=AUDIO_FORMAT_OPTIONS.get(audio_format_key, AUDIO_FORMAT_OPTIONS[DEFAULT_AUDIO_FORMAT])["label"])
        audio_quality_var = tk.StringVar(value=audio_quality_value)

        audio_only_cb = ttk.Checkbutton(frm, text='Скачать только аудио', variable=audio_only_var)
        audio_only_cb.grid(row=3, column=0, columnspan=2, sticky='w', pady=5)

        ttk.Label(frm, text='Формат аудио:').grid(row=3, column=2, sticky='e', padx=(15, 5))
        format_labels = [info['label'] for info in AUDIO_FORMAT_OPTIONS.values()]
        format_map = {info['label']: key for key, info in AUDIO_FORMAT_OPTIONS.items()}
        audio_format_cb = ttk.Combobox(frm, textvariable=audio_format_label_var, state='readonly', values=format_labels, width=20)
        audio_format_cb.grid(row=3, column=3, sticky='w')

        ttk.Label(frm, text='Битрейт:').grid(row=4, column=2, sticky='e', padx=(15, 5))
        audio_quality_cb = ttk.Combobox(frm, textvariable=audio_quality_var, state='readonly', width=10)
        audio_quality_cb.grid(row=4, column=3, sticky='w', pady=(0, 5))

        ttk.Label(frm, text="Папка:").grid(row=5, column=0, sticky="w", padx=(0, 5), pady=5)
        outdir_e = ttk.Entry(frm, textvariable=outdir_var, width=40)
        outdir_e.grid(row=5, column=1, columnspan=2, sticky="we")
        ttk.Button(frm, text="Выбрать...", command=lambda: outdir_var.set(
            filedialog.askdirectory(initialdir=outdir_var.get()) or outdir_var.get())
        ).grid(row=5, column=3, sticky="w")

        ttk.Label(frm, text="Имя файла:").grid(row=6, column=0, sticky="w", padx=(0, 5), pady=5)
        outtmpl_e = ttk.Entry(frm, textvariable=outtmpl_var, width=48)
        outtmpl_e.grid(row=6, column=1, columnspan=3, sticky="we")

        ttk.Label(frm, text="cookies.txt:").grid(row=7, column=0, sticky="w", padx=(0, 5), pady=5)
        cookies_e = ttk.Entry(frm, textvariable=cookies_var, width=40)
        cookies_e.grid(row=7, column=1, columnspan=2, sticky="we")
        ttk.Button(frm, text="Выбрать...", command=lambda: cookies_var.set(
            filedialog.askopenfilename(filetypes=[("Текстовые файлы", "*.txt"), ("Все файлы", "*.*")]) or cookies_var.get())
        ).grid(row=7, column=3, sticky="w")

        write_cb = ttk.Checkbutton(frm, text='Скачать субтитры', variable=write_subs_var)
        write_cb.grid(row=8, column=0, sticky='w', pady=(5, 0))
        embed_cb = ttk.Checkbutton(frm, text='Встроить в видео', variable=embed_subs_var)
        embed_cb.grid(row=8, column=1, sticky='w', pady=(5, 0))
        ttk.Label(frm, text='Языки (через запятую):').grid(row=8, column=2, sticky='e', padx=(15, 5))
        subtitle_entry = ttk.Entry(frm, textvariable=subtitle_var, width=30)
        subtitle_entry.grid(row=8, column=3, sticky='we', pady=(5, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=9, column=0, columnspan=4, sticky="e", pady=(10, 0))

        def refresh_audio_quality():
            fmt_key = audio_format_var.get()
            opts = AUDIO_FORMAT_OPTIONS.get(fmt_key, AUDIO_FORMAT_OPTIONS[DEFAULT_AUDIO_FORMAT])
            values = opts.get('bitrate_values') or []
            if values:
                audio_quality_cb.configure(values=values, state='readonly')
                if audio_quality_var.get() not in values:
                    audio_quality_var.set(values[0])
            else:
                audio_quality_cb.configure(values=[], state='disabled')
                audio_quality_var.set('0')

        def on_audio_format_change(*_):
            fmt = format_map.get(audio_format_label_var.get(), DEFAULT_AUDIO_FORMAT)
            audio_format_var.set(fmt)
            refresh_audio_quality()

        def apply_audio_only_state(*_):
            only_audio = bool(audio_only_var.get())
            state = 'disabled' if only_audio else 'readonly'
            for widget in [q_cb, v_cb, a_cb, c_cb]:
                widget.configure(state=state)
            audio_format_cb.configure(state='readonly' if only_audio else 'disabled')
            if only_audio:
                refresh_audio_quality()
            else:
                audio_quality_cb.configure(state='disabled')

        def on_write_subs_change(*_):
            if not write_subs_var.get():
                embed_subs_var.set(0)

        audio_format_cb.bind('<<ComboboxSelected>>', on_audio_format_change)
        audio_only_var.trace_add('write', apply_audio_only_state)
        write_subs_var.trace_add('write', on_write_subs_change)

        refresh_audio_quality()
        apply_audio_only_state()
        if not audio_only_var.get():
            audio_format_cb.configure(state='disabled')

        def on_ok():
            height_map = {
                "480p": 480, "720p": 720, "1080p": 1080,
                "1440p (2K)": 1440, "2160p (4K)": 2160, "4320p (8K)": 4320
            }
            h = height_map.get(q_var.get(), 1080)
            subtitles_list = tuple(lang.strip() for lang in subtitle_var.get().split(',') if lang.strip())
            new_preset = DownloadPreset(
                height=h,
                vcodec_choice=v_var.get(),
                acodec_choice=a_var.get(),
                container_choice=c_var.get(),
                alang_choice=alang_var.get(),
                outdir=(outdir_var.get().strip() or item.preset.outdir),
                outtmpl_user=(outtmpl_var.get().strip() or item.preset.outtmpl_user),
                cookies=(cookies_var.get().strip() or None),
                audio_only=bool(audio_only_var.get()),
                audio_format=audio_format_var.get(),
                audio_quality=audio_quality_var.get(),
                download_playlist=bool(playlist_var.get()),
                subtitle_langs=subtitles_list,
                write_subtitles=bool(write_subs_var.get()),
                embed_subtitles=bool(embed_subs_var.get()),
            )
            item.preset = new_preset
            self._update_queue_tv_row(item)
            self._append_log("Пресет задачи обновлён.")
            win.destroy()

        ttk.Button(btns, text="Отмена", command=win.destroy).pack(side="right")
        ttk.Button(btns, text="Сохранить", style="Accent.TButton", command=on_ok).pack(side="right", padx=(0, 10))

        for i in range(4):
            frm.grid_columnconfigure(i, weight=1)

    # ------------------------ Сбор пресета ------------------------

    def _collect_preset(self) -> Optional[DownloadPreset]:
        outdir = (self.outdir_var.get() or "").strip()
        if not outdir:
            messagebox.showwarning("Выберите папку", "Пожалуйста, выберите папку для сохранения.")
            return None
        height = self._desired_height()
        vcodec = self.vcodec_var.get()
        acodec = self.acodec_var.get()
        container = self.container_var.get()

        preset = DownloadPreset(
            height=height,
            vcodec_choice=vcodec,
            acodec_choice=acodec,
            alang_choice=self.alang_var.get().strip() or 'ru',
            container_choice=container,
            outdir=outdir,
            outtmpl_user=self.outtmpl_var.get().strip() or "%(title)s.%(ext)s",
            cookies=(self.cookies_var.get().strip() or None),
            audio_only=bool(self.audio_only_var.get()) if hasattr(self, 'audio_only_var') else False,
            audio_format=self.audio_format_var.get() if hasattr(self, 'audio_format_var') else DEFAULT_AUDIO_FORMAT,
            audio_quality=self.audio_quality_var.get() if hasattr(self, 'audio_quality_var') else DEFAULT_AUDIO_QUALITY,
            download_playlist=bool(self.playlist_var.get()) if hasattr(self, 'playlist_var') else False,
            subtitle_langs=tuple(self.selected_subtitle_langs),
            write_subtitles=bool(self.write_subs_var.get()) if hasattr(self, 'write_subs_var') else False,
            embed_subtitles=bool(self.embed_subs_var.get()) if hasattr(self, 'embed_subs_var') else False,
        )
        return preset

    # ------------------------ Сохранение/загрузка настроек ------------------------

    
    def _bind_setting_events(self):
        for cb in [
            self.quality_cb,
            self.vcodec_cb,
            self.acodec_cb,
            self.container_cb,
            self.alang_cb,
            getattr(self, 'audio_format_cb', None),
            getattr(self, 'audio_quality_cb', None),
        ]:
            if cb:
                cb.bind("<<ComboboxSelected>>", lambda e: self._save_settings_debounced())

        for var in [self.outdir_var, self.outtmpl_var, self.cookies_var, self.url_var]:
            var.trace_add("write", lambda *args: self._save_settings_debounced())

        for ivar in [
            getattr(self, 'audio_only_var', None),
            getattr(self, 'playlist_var', None),
            getattr(self, 'write_subs_var', None),
            getattr(self, 'embed_subs_var', None),
        ]:
            if ivar is not None:
                ivar.trace_add("write", lambda *args: self._save_settings_debounced())

    def _load_settings(self):
        if not os.path.isfile(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            self._append_log(f"Не удалось прочитать конфиг: {e}")
            return

        self.quality_var.set(cfg.get("quality", self.quality_var.get()))
        self.vcodec_var.set(cfg.get("vcodec", self.vcodec_var.get()))
        self.acodec_var.set(cfg.get("acodec", self.acodec_var.get()))
        self.container_var.set(cfg.get("container", self.container_var.get()))
        self.alang_var.set(cfg.get("audio_lang", "orig"))
        if hasattr(self, 'playlist_var'):
            self.playlist_var.set(1 if cfg.get('download_playlist', False) else 0)
        legacy_audio_only = cfg.get('audio_only_mp3')
        if hasattr(self, 'audio_only_var'):
            self.audio_only_var.set(1 if cfg.get('audio_only', legacy_audio_only or False) else 0)
        audio_format = cfg.get('audio_format', DEFAULT_AUDIO_FORMAT)
        if audio_format not in AUDIO_FORMAT_OPTIONS:
            audio_format = DEFAULT_AUDIO_FORMAT
        if hasattr(self, 'audio_format_var'):
            self.audio_format_var.set(audio_format)
        if hasattr(self, 'audio_format_label_var'):
            self.audio_format_label_var.set(AUDIO_FORMAT_OPTIONS[audio_format]['label'])
        if hasattr(self, 'audio_quality_var'):
            aq = str(cfg.get('audio_quality', cfg.get('mp3_kbps', DEFAULT_AUDIO_QUALITY)))
            self.audio_quality_var.set(aq)
        self.selected_subtitle_langs = list(cfg.get('subtitle_langs', [])) if isinstance(cfg.get('subtitle_langs', []), (list, tuple)) else []
        if hasattr(self, 'write_subs_var'):
            self.write_subs_var.set(1 if cfg.get('write_subtitles', False) else 0)
        if hasattr(self, 'embed_subs_var'):
            self.embed_subs_var.set(1 if cfg.get('embed_subtitles', False) else 0)
            if hasattr(self, 'write_subs_var') and not self.write_subs_var.get():
                self.embed_subs_var.set(0)
        self.outdir_var.set(cfg.get("outdir", self.outdir_var.get()))
        self.outtmpl_var.set(cfg.get("outtmpl", self.outtmpl_var.get()))
        self.cookies_var.set(cfg.get("cookies", self.cookies_var.get()))
        self.url_var.set(cfg.get("last_url", self.url_var.get()))

        self._refresh_audio_quality_values()
        self._on_audio_only_toggle(init=True)
        self._update_subtitle_display()
        self._on_subtitle_option_changed(init=True)

        self._append_log("Настройки загружены.")

    def _save_settings_debounced(self):
        if self._save_debounce_after is not None:
            try:
                self.after_cancel(self._save_debounce_after)
            except Exception:
                pass
        self._save_debounce_after = self.after(500, self._save_settings)

    def _save_settings(self):
        cfg = {
            "quality": self.quality_var.get(),
            "vcodec": self.vcodec_var.get(),
            "acodec": self.acodec_var.get(),
            "audio_lang": self.alang_var.get(),
            "audio_only": bool(self.audio_only_var.get()) if hasattr(self,'audio_only_var') else False,
            "audio_format": self.audio_format_var.get() if hasattr(self, 'audio_format_var') else DEFAULT_AUDIO_FORMAT,
            "audio_quality": self.audio_quality_var.get() if hasattr(self, 'audio_quality_var') else DEFAULT_AUDIO_QUALITY,
            "download_playlist": bool(self.playlist_var.get()) if hasattr(self, 'playlist_var') else False,
            "subtitle_langs": list(self.selected_subtitle_langs),
            "write_subtitles": bool(self.write_subs_var.get()) if hasattr(self, 'write_subs_var') else False,
            "embed_subtitles": bool(self.embed_subs_var.get()) if hasattr(self, 'embed_subs_var') else False,
            "container": self.container_var.get(),
            "outdir": self.outdir_var.get(),
            "outtmpl": self.outtmpl_var.get(),
            "cookies": self.cookies_var.get(),
            "last_url": self.url_var.get(),
        }
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._append_log(f"Не удалось сохранить настройки: {e}")

    def _on_close(self):
        try:
            self._save_settings()
        except Exception:
            pass
        self.destroy()

    # ------------------------ Запуск приложения ------------------------

    def run(self):
        self.mainloop()


if __name__ == "__main__":
    app = DownloaderApp()
    app.run()