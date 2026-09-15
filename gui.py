import datetime as dt
import json
import os
import queue
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(HERE, "gui_settings.json")


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(d):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


TR = {
    "uk": {
        "app_title": "Транскрипція лекції",
        "tkinter_missing": "Не вдалося завантажити tkinter.\n\nПоставте Python з "
                           "python.org з увімкненим компонентом 'tcl/tk and IDLE'.",
        "subtitle": "Записує звук з динаміків і показує текст у цьому вікні.",
        "components_box": "Компоненти",
        "fix_btn": "Встановити / полагодити",
        "comp_engine": "Двигун розпізнавання (faster-whisper)",
        "comp_audio": "Захоплення звуку (PyAudioWPatch)",
        "comp_signal": "Обробка сигналу (numpy / scipy)",
        "comp_hotkeys": "Гарячі клавіші-мітки (keyboard)",
        "comp_gpu": "Відеокарта",
        "not_installed_warn": "не встановлено — функція вимкнеться",
        "gpu_pending": "перевіриться під час запуску",
        "gpu_none": "GPU не знайдено — працюватиме на CPU (повільніше)",
        "checking_placeholder": "перевірка…",
        "checking_components": "Перевірка компонентів…",
        "checking_slow": "Перевірка триває довше, ніж зазвичай ({elapsed:.0f} с) — "
                         "схоже, Windows уперше перевіряє нові файли (Smart App "
                         "Control) або мережа зараз повільна. Це одноразово, "
                         "зачекайте — вікно не зависло.",
        "need_fix_hint": "Бракує компонентів. Натисніть «Встановити / полагодити».",
        "start_anyway_hint": "Можна спробувати «Почати» і так.",
        "ready_hint": "Готово. Оберіть режим і натисніть «Почати».",
        "running_setup_log": "Запускаю setup.bat …  (це може зайняти кілька хвилин)\n",
        "setup_done_log": "\nГотово.\n",
        "setup_failed_log": "\nЗавершилось з кодом {code}. Дивіться повідомлення вище.\n",
        "engine_not_ready": "Двигун ще не готовий.",
        "ui_lang_label": "Мова інтерфейсу",
        "lecture_lang_label": "Мова лекції",
        "notify_silence": "Сповіщати, якщо зник звук",
        "hotkey_markers_chk": "Гарячі клавіші-мітки",
        "advanced": "Додатково",
        "device_label": "Пристрій виводу",
        "default_device": "За замовчуванням",
        "autostop_label": "Автостоп через, хв (0 = вимк.)",
        "section_recognition": "Розпізнавання",
        "prompt_label": "Підказка (тема, імена, терміни)",
        "model_label": "Модель",
        "speed_quality_label": "Швидкість / якість",
        "compute_best": "Найкраща якість (float16)",
        "compute_fast": "Швидше, менше пам'яті (int8_float16)",
        "beam_label": "Ширина променевого пошуку (beam)",
        "min_silence_label": "Пауза, що завершує рядок, с",
        "max_segment_label": "Макс. довжина сегмента, с",
        "section_hotkeys": "Гарячі клавіші",
        "mark_important_label": 'Мітка "важливо"',
        "mark_confused_label": 'Мітка "не зрозумів"',
        "section_silence": "Сповіщення про тишу",
        "silence_secs_label": "Попереджати через, с",
        "no_keepalive_chk": "Без тихого сигналу підтримки з'єднання",
        "start_btn": "▶   Почати запис",
        "stop_btn": "⏹  Зупинити",
        "copy_btn": "📋  Скопіювати транскрипт",
        "follow_bottom_chk": "Стежити за низом",
        "starting_status": "запуск…",
        "finished_status": "запис завершено",
        "start_failed_msg_prefix": "Не вдалося почати запис:\n\n",
        "no_sound": "🔴 НЕМАЄ ЗВУКУ",
        "recording": "🟢 запис",
        "ended_status": "⏹ завершено",
        "lines_count": "{n} рядків",
        "stopping_status": "Зупиняю запис і зберігаю файли…",
        "stopping_slow": "Зупиняю запис… ({elapsed:.0f} с) — можливо, ще "
                        "завершується завантаження моделі, зачекайте.",
        "setup_first_msg": "Спершу встановіть залежності (запустіть setup.bat), "
                          "потім використовуйте параметри командного рядка.",
    },
    "en": {
        "app_title": "Lecture Transcriber",
        "tkinter_missing": "Could not load tkinter.\n\nInstall Python from "
                           "python.org with the 'tcl/tk and IDLE' component enabled.",
        "subtitle": "Records the sound from your speakers and shows the text in this window.",
        "components_box": "Components",
        "fix_btn": "Install / repair",
        "comp_engine": "Recognition engine (faster-whisper)",
        "comp_audio": "Audio capture (PyAudioWPatch)",
        "comp_signal": "Signal processing (numpy / scipy)",
        "comp_hotkeys": "Hotkey markers (keyboard)",
        "comp_gpu": "Graphics card",
        "not_installed_warn": "not installed — this feature will be off",
        "gpu_pending": "checked at startup",
        "gpu_none": "No GPU found — will run on CPU (slower)",
        "checking_placeholder": "checking…",
        "checking_components": "Checking components…",
        "checking_slow": "This is taking longer than usual ({elapsed:.0f}s) — "
                         "Windows is likely scanning new files for the first time "
                         "(Smart App Control) or the network is slow right now. "
                         "This is one-time, please wait — the window is not frozen.",
        "need_fix_hint": "Some components are missing. Click “Install / repair”.",
        "start_anyway_hint": "You can still try “Start” anyway.",
        "ready_hint": "Ready. Choose the options and click “Start”.",
        "running_setup_log": "Running setup.bat …  (this can take a few minutes)\n",
        "setup_done_log": "\nDone.\n",
        "setup_failed_log": "\nFinished with code {code}. See the messages above.\n",
        "engine_not_ready": "The engine is not ready yet.",
        "ui_lang_label": "Interface language",
        "lecture_lang_label": "Lecture language",
        "notify_silence": "Notify if the sound is lost",
        "hotkey_markers_chk": "Hotkey markers",
        "advanced": "Advanced",
        "device_label": "Output device",
        "default_device": "Default",
        "autostop_label": "Auto-stop after, min (0 = off)",
        "section_recognition": "Recognition",
        "prompt_label": "Hint (topic, names, terms)",
        "model_label": "Model",
        "speed_quality_label": "Speed / quality",
        "compute_best": "Best quality (float16)",
        "compute_fast": "Faster, less memory (int8_float16)",
        "beam_label": "Beam search width",
        "min_silence_label": "Pause that ends a line, s",
        "max_segment_label": "Max segment length, s",
        "section_hotkeys": "Hotkeys",
        "mark_important_label": 'Marker "important"',
        "mark_confused_label": 'Marker "did not understand"',
        "section_silence": "Silence alerts",
        "silence_secs_label": "Warn after, s",
        "no_keepalive_chk": "Without the silent keep-alive signal",
        "start_btn": "▶   Start recording",
        "stop_btn": "⏹  Stop",
        "copy_btn": "📋  Copy transcript",
        "follow_bottom_chk": "Follow the bottom",
        "starting_status": "starting…",
        "finished_status": "recording finished",
        "start_failed_msg_prefix": "Could not start recording:\n\n",
        "no_sound": "🔴 NO SOUND",
        "recording": "🟢 recording",
        "ended_status": "⏹ finished",
        "lines_count": "{n} lines",
        "stopping_status": "Stopping the recording and saving files…",
        "stopping_slow": "Stopping… ({elapsed:.0f}s) — the model might still be "
                        "finishing loading, please wait.",
        "setup_first_msg": "Install the dependencies first (run setup.bat), then "
                          "use command-line options.",
    },
}

CUR_LANG = load_settings().get("ui_language") or "uk"
if CUR_LANG not in TR:
    CUR_LANG = "uk"


def t(key, **kw):
    s = TR.get(CUR_LANG, TR["uk"]).get(key, key)
    return s.format(**kw) if kw else s


def _message_box(text: str, title: str = None) -> None:
    title = title or t("app_title")
    try:
        import tkinter as _tk
        from tkinter import messagebox
        r = _tk.Tk()
        r.withdraw()
        messagebox.showerror(title, text)
        r.destroy()
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
        except Exception:
            pass


try:
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext
except Exception:
    _message_box(t("tkinter_missing"))
    sys.exit(1)


COMPONENTS = [
    ("comp_engine", "faster_whisper", True),
    ("comp_audio", "pyaudiowpatch", True),
    ("comp_signal", "scipy", True),
    ("comp_hotkeys", "keyboard", False),
]

T = None


def check_components():
    rows, ok = [], True
    for label_key, mod, required in COMPONENTS:
        try:
            __import__(mod)
            rows.append((t(label_key), "ok", ""))
        except Exception as exc:
            if required:
                ok = False
                rows.append((t(label_key), "bad", str(exc)[:120]))
            else:
                rows.append((t(label_key), "warn", t("not_installed_warn")))
    global T
    if ok:
        try:
            import transcribe as _t
            T = _t
        except Exception as exc:
            ok = False
            rows.append(("transcribe.py", "bad", str(exc)[:120]))
    gpu = t("gpu_pending")
    try:
        import ctranslate2
        n = ctranslate2.get_cuda_device_count()
        gpu = f"CUDA GPU: {n}" if n else t("gpu_none")
    except Exception:
        pass
    rows.append((t("comp_gpu"), "ok" if "CUDA GPU" in gpu else "warn", gpu))
    return rows, ok


LANG_CHOICES = [("Українська", "uk"), ("English", "en")]
MODELS = ["large-v3", "medium", "small"]


def compute_types():
    return [(t("compute_best"), "float16"), (t("compute_fast"), "int8_float16")]


def compute_label(value, types):
    for lbl, val in types:
        if val == value:
            return lbl
    return types[0][0]


class StartPanel(ttk.Frame):
    def __init__(self, master, on_start, on_relaunch=None):
        super().__init__(master, padding=16)
        self.on_start = on_start
        self.on_relaunch = on_relaunch
        self.q = queue.Queue()
        self.checking = False
        self.compute_types = compute_types()
        self._compute_by_label = {lbl: val for lbl, val in self.compute_types}
        s = load_settings()

        ttk.Label(self, text=t("app_title"), font=("Segoe UI", 16, "bold")
                  ).pack(anchor="w")
        ttk.Label(self, text=t("subtitle"),
                  foreground="#666").pack(anchor="w", pady=(0, 12))

        uif = ttk.Frame(self)
        uif.pack(fill="x", pady=(0, 8))
        ttk.Label(uif, text=t("ui_lang_label"), font=("Segoe UI", 9, "bold")
                  ).pack(side="left", padx=(0, 8))
        self.ui_lang = tk.StringVar(value=CUR_LANG)
        for label, code in LANG_CHOICES:
            ttk.Radiobutton(uif, text=label, value=code, variable=self.ui_lang,
                            style="Toolbutton", command=self._change_lang
                            ).pack(side="left", padx=(0, 4))

        box = ttk.LabelFrame(self, text=t("components_box"), padding=10)
        box.pack(fill="x")
        self.rows_frame = ttk.Frame(box)
        self.rows_frame.pack(fill="x")
        self.fix_btn = ttk.Button(box, text=t("fix_btn"), command=self._fix)
        self.setup_log = scrolledtext.ScrolledText(box, height=7, font=("Consolas", 9),
                                                   state="disabled")

        opt = ttk.Frame(self)
        opt.pack(fill="x", pady=12)

        ttk.Label(opt, text=t("lecture_lang_label"), font=("Segoe UI", 10, "bold")
                  ).grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.lang = tk.StringVar(value=s.get("language") or "uk")
        lf = ttk.Frame(opt)
        lf.grid(row=1, column=0, sticky="w", pady=(0, 10))
        for label, code in LANG_CHOICES:
            ttk.Radiobutton(lf, text=label, value=code, variable=self.lang,
                            style="Toolbutton").pack(side="left", padx=(0, 4))
        opt.columnconfigure(0, weight=1)

        self.silence = tk.BooleanVar(value=s.get("silence", True))
        self.hotkeys = tk.BooleanVar(value=s.get("hotkeys", True))
        cf = ttk.Frame(opt)
        cf.grid(row=2, column=0, sticky="w")
        ttk.Checkbutton(cf, text=t("notify_silence"), variable=self.silence
                        ).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(cf, text=t("hotkey_markers_chk"), variable=self.hotkeys
                        ).pack(side="left")

        self.adv_open = tk.BooleanVar(value=False)
        self.adv_btn = ttk.Checkbutton(opt, text=t("advanced"), style="Toolbutton",
                                       variable=self.adv_open, command=self._toggle_adv)
        self.adv_btn.grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.adv = ttk.Frame(opt)

        def _section(row, text):
            ttk.Label(self.adv, text=text, font=("Segoe UI", 9, "bold")).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(8, 2))

        ttk.Label(self.adv, text=t("device_label")).grid(row=1, column=0, sticky="w")
        self.device = tk.StringVar(value=t("default_device"))
        self._devs = [("", t("default_device"))]
        self.dev_combo = ttk.Combobox(self.adv, textvariable=self.device, width=44,
                                      state="readonly", values=[t("default_device")])
        self.dev_combo.grid(row=1, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(self.adv, text=t("autostop_label")).grid(
            row=2, column=0, sticky="w")
        self.duration = tk.StringVar(value=str(s.get("duration", 0) or 0))
        ttk.Entry(self.adv, textvariable=self.duration, width=10).grid(
            row=2, column=1, sticky="w", padx=6, pady=2)

        _section(3, t("section_recognition"))
        ttk.Label(self.adv, text=t("prompt_label")).grid(
            row=4, column=0, sticky="w")
        self.prompt = tk.StringVar(value=s.get("prompt", ""))
        ttk.Entry(self.adv, textvariable=self.prompt, width=40).grid(
            row=4, column=1, sticky="we", padx=6, pady=2)
        ttk.Label(self.adv, text=t("model_label")).grid(row=5, column=0, sticky="w")
        self.model = tk.StringVar(value=s.get("model") or "large-v3")
        ttk.Combobox(self.adv, textvariable=self.model, width=16, state="readonly",
                     values=MODELS).grid(row=5, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(self.adv, text=t("speed_quality_label")).grid(row=6, column=0, sticky="w")
        self.compute_label = tk.StringVar(
            value=compute_label(s.get("compute_type", "float16"), self.compute_types))
        ttk.Combobox(self.adv, textvariable=self.compute_label, width=34, state="readonly",
                     values=[lbl for lbl, _ in self.compute_types]).grid(
            row=6, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(self.adv, text=t("beam_label")).grid(
            row=7, column=0, sticky="w")
        self.beam_size = tk.StringVar(value=str(s.get("beam_size", 5)))
        ttk.Spinbox(self.adv, from_=1, to=5, textvariable=self.beam_size, width=6
                    ).grid(row=7, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(self.adv, text=t("min_silence_label")).grid(
            row=8, column=0, sticky="w")
        self.min_silence = tk.StringVar(value=str(s.get("min_silence", 0.6)))
        ttk.Entry(self.adv, textvariable=self.min_silence, width=10).grid(
            row=8, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(self.adv, text=t("max_segment_label")).grid(
            row=9, column=0, sticky="w")
        self.max_segment = tk.StringVar(value=str(s.get("max_segment", 18)))
        ttk.Entry(self.adv, textvariable=self.max_segment, width=10).grid(
            row=9, column=1, sticky="w", padx=6, pady=2)

        _section(10, t("section_hotkeys"))
        ttk.Label(self.adv, text=t("mark_important_label")).grid(row=11, column=0, sticky="w")
        self.mark_key = tk.StringVar(value=s.get("mark_key") or "ctrl+alt+m")
        ttk.Entry(self.adv, textvariable=self.mark_key, width=16).grid(
            row=11, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(self.adv, text=t("mark_confused_label")).grid(row=12, column=0, sticky="w")
        self.confused_key = tk.StringVar(value=s.get("confused_key") or "ctrl+alt+k")
        ttk.Entry(self.adv, textvariable=self.confused_key, width=16).grid(
            row=12, column=1, sticky="w", padx=6, pady=2)

        _section(13, t("section_silence"))
        ttk.Label(self.adv, text=t("silence_secs_label")).grid(row=14, column=0, sticky="w")
        self.silence_secs = tk.StringVar(value=str(s.get("silence_secs", 20)))
        ttk.Entry(self.adv, textvariable=self.silence_secs, width=10).grid(
            row=14, column=1, sticky="w", padx=6, pady=2)
        self.no_keepalive = tk.BooleanVar(value=s.get("no_keepalive", False))
        ttk.Checkbutton(self.adv, text=t("no_keepalive_chk"),
                        variable=self.no_keepalive).grid(
            row=15, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.go = ttk.Button(self, text=t("start_btn"), command=self._go)
        self.go.pack(fill="x", ipady=6, pady=(6, 0))
        self.hint = ttk.Label(self, text="", foreground="#666")
        self.hint.pack(anchor="w", pady=(6, 0))

        self._render_rows([(t(k), "warn", t("checking_placeholder")) for k, _, _ in COMPONENTS])
        self.go.config(state="disabled")
        self.after(80, self._drain)
        self._recheck()

    def _change_lang(self):
        global CUR_LANG
        CUR_LANG = self.ui_lang.get()
        s = load_settings()
        s["ui_language"] = CUR_LANG
        save_settings(s)
        if self.on_relaunch:
            self.on_relaunch()

    def _recheck(self):
        if self.checking:
            return
        self.checking = True
        self._check_started = time.monotonic()
        self.hint.config(text=t("checking_components"))
        threading.Thread(target=self._do_check, daemon=True).start()
        self.after(4000, self._check_watchdog)

    def _check_watchdog(self):
        if not self.checking or not self.winfo_exists():
            return
        elapsed = time.monotonic() - self._check_started
        if elapsed > 8:
            self.hint.config(text=t("checking_slow", elapsed=elapsed))
        self.after(4000, self._check_watchdog)

    def _do_check(self):
        rows, ok = check_components()
        # A required import can transiently fail while Smart App Control is still
        # evaluating a compiled DLL (numpy/scipy/...) - that check can itself take
        # up to ~100s, so keep retrying for a while instead of giving up after one try.
        delay = 2.0
        waited = 0.0
        while not ok and waited < 100.0:
            time.sleep(delay)
            waited += delay
            rows, ok = check_components()
            delay = min(delay * 1.5, 10.0)
        self.q.put(("check", (rows, ok)))

    def _render_rows(self, rows):
        for w in self.rows_frame.winfo_children():
            w.destroy()
        icon = {"ok": ("✓", "#1a7f37"), "warn": ("!", "#9a6700"), "bad": ("✕", "#cf222e")}
        for label, state, detail in rows:
            sym, col = icon[state]
            r = ttk.Frame(self.rows_frame)
            r.pack(fill="x", anchor="w")
            tk.Label(r, text=sym, fg=col, width=2, font=("Segoe UI", 10, "bold")
                     ).pack(side="left")
            tk.Label(r, text=label).pack(side="left")
            if detail:
                tk.Label(r, text=" — " + detail, fg="#888").pack(side="left")

    def _apply_check(self, rows, ok):
        self.checking = False
        self._render_rows(rows)
        need_fix = any(st == "bad" for _, st, _ in rows)
        if need_fix:
            self.fix_btn.pack(pady=(8, 0))
            self.hint.config(text=t("need_fix_hint") + "  " + t("start_anyway_hint"))
        else:
            self.fix_btn.pack_forget()
            self.setup_log.pack_forget()
            self.hint.config(text=t("ready_hint"))
        self._load_devices()
        self.go.config(state="normal")

    def _fix(self):
        self.fix_btn.config(state="disabled")
        self.go.config(state="disabled")
        self.setup_log.pack(fill="x", pady=(8, 0))
        self._append_log(t("running_setup_log"))
        threading.Thread(target=self._run_setup, daemon=True).start()

    def _run_setup(self):
        try:
            p = subprocess.Popen(
                ["cmd", "/c", os.path.join(HERE, "setup.bat"), "--auto"],
                cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for line in p.stdout:
                self.q.put(("log", line.rstrip("\n")))
            p.wait()
            self.q.put(("setupdone", p.returncode))
        except Exception as exc:
            self.q.put(("log", f"[X] {exc}"))
            self.q.put(("setupdone", 1))

    def _append_log(self, text):
        self.setup_log.config(state="normal")
        self.setup_log.insert("end", text if text.endswith("\n") else text + "\n")
        self.setup_log.see("end")
        self.setup_log.config(state="disabled")

    def _load_devices(self):
        if T is None:
            return
        try:
            devs = T.loopback_devices()
        except Exception:
            devs = []
        self._devs = [("", t("default_device"))] + [(str(i), lbl) for i, lbl in devs]
        self.dev_combo.config(values=[lbl for _, lbl in self._devs])

    def _toggle_adv(self):
        top = self.winfo_toplevel()
        if self.adv_open.get():
            self.adv.grid(row=4, column=0, sticky="we", pady=(4, 0))
            top.geometry("860x860")
        else:
            self.adv.grid_forget()
            top.geometry("800x700")

    def _drain(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "check":
                    self._apply_check(*payload)
                elif kind == "log":
                    self._append_log(payload)
                elif kind == "setupdone":
                    self.fix_btn.config(state="normal")
                    self._append_log(
                        t("setup_done_log") if payload == 0 else
                        t("setup_failed_log", code=payload))
                    if payload == 0:
                        self._recheck()
        except queue.Empty:
            pass
        self.after(120, self._drain)

    def _go(self):
        global T
        if T is None:
            try:
                import transcribe as _t
                T = _t
            except Exception:
                pass
        if T is None:
            messagebox.showerror(t("app_title"), t("engine_not_ready"))
            return

        def _float(var, default):
            try:
                return max(0.0, float(var.get().replace(",", ".")))
            except ValueError:
                return default

        args = T.default_args()
        args.language = self.lang.get() or "uk"
        args.ui_language = CUR_LANG
        args.prompt = self.prompt.get().strip() or None
        args.model = self.model.get() or "large-v3"
        args.compute_type = self._compute_by_label.get(self.compute_label.get(), "float16")
        try:
            args.beam_size = max(1, int(self.beam_size.get()))
        except ValueError:
            args.beam_size = 5
        args.min_silence = _float(self.min_silence, 0.6)
        args.max_segment = _float(self.max_segment, 18.0) or 18.0
        args.no_keepalive = self.no_keepalive.get()

        if self.hotkeys.get():
            args.mark_key = self.mark_key.get().strip()
            args.confused_key = self.confused_key.get().strip()
        else:
            args.mark_key = ""
            args.confused_key = ""

        silence_secs = _float(self.silence_secs, 20.0)
        args.silence_alert = silence_secs if self.silence.get() else 0.0
        args.no_toast = not self.silence.get()

        idx = None
        for val, lbl in self._devs:
            if lbl == self.device.get() and val:
                idx = int(val)
        args.device_index = idx
        args.duration = _float(self.duration, 0.0)

        save_settings({
            "ui_language": CUR_LANG,
            "language": self.lang.get(), "silence": self.silence.get(),
            "hotkeys": self.hotkeys.get(), "duration": args.duration,
            "prompt": self.prompt.get(), "model": args.model,
            "compute_type": args.compute_type, "beam_size": args.beam_size,
            "min_silence": args.min_silence, "max_segment": args.max_segment,
            "mark_key": self.mark_key.get(), "confused_key": self.confused_key.get(),
            "silence_secs": silence_secs, "no_keepalive": args.no_keepalive,
        })
        self.on_start(args)


class TranscriptView(ttk.Frame):
    def __init__(self, master, args, on_back):
        super().__init__(master)
        self.args = args
        self.on_back = on_back
        self.q = queue.Queue()
        self.count = 0
        self.ended = False
        self.status_extra = t("starting_status")

        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill="x")
        ttk.Button(top, text=t("stop_btn"), command=self._stop).pack(side="left")
        ttk.Button(top, text=t("copy_btn"), command=self.copy_all
                   ).pack(side="left", padx=(8, 0))
        self.autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text=t("follow_bottom_chk"), variable=self.autoscroll
                        ).pack(side="right")

        self.status = ttk.Label(self, anchor="w", padding=(8, 3))
        self.status.pack(fill="x")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", font=("Segoe UI", 11),
                            spacing1=3, spacing3=3, padx=8, pady=6,
                            undo=False, state="disabled")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.text.tag_configure("ts", foreground="#8a8a8a")
        self.text.tag_configure("alertrow", background="#ffe1e1")
        self.text.tag_configure("markrow", background="#e3f1ff")

        self.session = T.Session(args, on_event=lambda k, ts, tx: self.q.put((k, ts, tx)))
        self.session.start()
        self.after(150, self._drain)
        self.after(1000, self._tick)

    def _at_bottom(self):
        return self.text.yview()[1] > 0.999

    def _add_row(self, ts, body):
        stick = self.autoscroll.get() and self._at_bottom()
        self.text.configure(state="normal")
        self.text.insert("end", f"{ts:%H:%M:%S}  ", ("ts",))
        self.text.insert("end", body + "\n")
        self.text.configure(state="disabled")
        if stick:
            self.text.see("end")

    def _add_special(self, ts, body, rowtag):
        stick = self.autoscroll.get() and self._at_bottom()
        self.text.configure(state="normal")
        lineno = int(self.text.index("end-1c").split(".")[0])
        self.text.insert("end", f"    {ts:%H:%M:%S}  {body}\n", (rowtag,))
        self.text.tag_add(rowtag, f"{lineno}.0", f"{lineno}.0 lineend+1c")
        self.text.configure(state="disabled")
        if stick:
            self.text.see("end")

    def copy_all(self):
        self.winfo_toplevel().clipboard_clear()
        self.winfo_toplevel().clipboard_append(self.text.get("1.0", "end-1c"))

    def _drain(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                kind, ts, text = self.q.get_nowait()
                now = ts or dt.datetime.now()
                if kind == "line":
                    self._add_row(now, text)
                    self.count += 1
                elif kind == "mark":
                    self._add_special(now, text, "markrow")
                elif kind == "alert":
                    self._add_special(now, text, "alertrow")
                elif kind == "resume":
                    self._add_special(now, text, "markrow")
                elif kind == "info":
                    self.status_extra = text
                elif kind == "ended":
                    self.ended = True
                    self.status_extra = t("finished_status")
                    if self.count == 0 and self.session.error:
                        messagebox.showerror(
                            t("app_title"),
                            t("start_failed_msg_prefix") + self.session.error)
                        self.on_back()
                        return
        except queue.Empty:
            pass
        self._refresh()
        self.after(180, self._drain)

    def _tick(self):
        self._refresh()
        if self.winfo_exists():
            self.after(1000, self._tick)

    def _refresh(self):
        dev = self.session.device_name or "…"
        started = self.session.started_at
        el = "--:--:--"
        if started:
            s = int(time.monotonic() - started)
            el = f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
        head = t("no_sound") if self.session.silent else t("recording")
        if self.ended:
            head = t("ended_status")
        self.status.config(text="   ·   ".join(
            [head, dev, el, t("lines_count", n=self.count), self.status_extra]))

    def _stop(self):
        self.on_back()

    def stop_session(self):
        try:
            self.session.stop()
        except Exception:
            pass


class MainWindow:
    def __init__(self, root, direct_args=None):
        self.root = root
        self.view = None
        root.geometry("800x700")
        root.minsize(620, 460)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            style = ttk.Style()
            if "vista" in style.theme_names():
                style.theme_use("vista")
        except Exception:
            pass
        if direct_args is not None:
            self._start_session(direct_args)
        else:
            self._show_panel()

    def _show_panel(self):
        if self.view is not None:
            self.view.destroy()
        self.root.title(t("app_title"))
        self.root.geometry("800x700")
        self.view = StartPanel(self.root, on_start=self._start_session,
                               on_relaunch=self._show_panel)
        self.view.pack(fill="both", expand=True)

    def _start_session(self, args):
        if self.view is not None:
            self.view.destroy()
        self.root.title(t("app_title"))
        self.root.geometry("1040x740")
        self.view = TranscriptView(self.root, args, on_back=self._back_to_panel)
        self.view.pack(fill="both", expand=True)

    def _back_to_panel(self):
        self._teardown_then(self._show_panel)

    def _on_close(self):
        self._teardown_then(self.root.destroy)

    def _teardown_then(self, done_cb):
        v = self.view
        if not isinstance(v, TranscriptView):
            done_cb()
            return
        started = time.monotonic()

        def set_status(text):
            try:
                v.status.config(text=text)
            except Exception:
                pass

        set_status(t("stopping_status"))
        done = threading.Event()
        threading.Thread(target=lambda: (v.stop_session(), done.set()),
                         daemon=True).start()

        def poll():
            if done.is_set():
                done_cb()
                return
            elapsed = time.monotonic() - started
            if elapsed > 5:
                set_status(t("stopping_slow", elapsed=elapsed))
            self.root.after(300, poll)
        self.root.after(300, poll)


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    direct = None
    if sys.argv[1:]:
        _, ok = check_components()
        if not (ok and T is not None):
            _message_box(t("setup_first_msg"))
            return
        direct = T.build_parser().parse_args(sys.argv[1:])
        if direct.list_devices:
            import pyaudiowpatch as _pa
            pa = _pa.PyAudio()
            try:
                T.list_devices(pa)
            finally:
                pa.terminate()
            return
        if not getattr(direct, "ui_language", None):
            direct.ui_language = CUR_LANG

    root = tk.Tk()
    MainWindow(root, direct_args=direct)
    root.mainloop()


if __name__ == "__main__":
    main()
