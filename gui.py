"""
Lecture transcriber - one window.

On launch it checks the components; when they are all present it shows a small
control panel where you pick the language (Ukrainian / English) and a few quick
options, press "Почати", and the transcription starts right there in the same
window. It always runs the best model (large-v3).

No console. Run with pythonw (see Транскрипція.vbs) or:  pythonw gui.py
"""

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


def _message_box(text: str, title: str = "Транскрипція лекції") -> None:
    try:
        import tkinter as _tk
        from tkinter import messagebox
        r = _tk.Tk()
        r.withdraw()
        messagebox.showerror(title, text)
        r.destroy()
    except Exception:  # noqa: BLE001
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
        except Exception:  # noqa: BLE001
            pass


try:
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext
except Exception:  # noqa: BLE001
    _message_box("Не вдалося завантажити tkinter.\n\nПоставте Python з python.org "
                 "з увімкненим компонентом 'tcl/tk and IDLE'.")
    sys.exit(1)


# ---------------------------------------------------------------- components
# (label, import name, required?)
COMPONENTS = [
    ("Двигун розпізнавання (faster-whisper)", "faster_whisper", True),
    ("Захоплення звуку (PyAudioWPatch)", "pyaudiowpatch", True),
    ("Обробка сигналу (numpy / scipy)", "scipy", True),
    ("Гарячі клавіші-мітки (keyboard)", "keyboard", False),
]

T = None  # the transcribe.py engine module, imported once components are OK


def check_components():
    """-> (rows, ok). rows = [(label, state, detail)] state in ok/warn/bad."""
    rows, ok = [], True
    for label, mod, required in COMPONENTS:
        try:
            __import__(mod)
            rows.append((label, "ok", ""))
        except Exception as exc:  # noqa: BLE001
            if required:
                ok = False
                rows.append((label, "bad", str(exc)[:120]))
            else:
                rows.append((label, "warn", "не встановлено — функція вимкнеться"))
    # engine module (pulls the heavy imports together)
    global T
    if ok:
        try:
            import transcribe as _t
            T = _t
        except Exception as exc:  # noqa: BLE001
            ok = False
            rows.append(("transcribe.py", "bad", str(exc)[:120]))
    # GPU is informational only
    gpu = "перевіриться під час запуску"
    try:
        import ctranslate2
        n = ctranslate2.get_cuda_device_count()
        gpu = f"CUDA GPU: {n}" if n else "GPU не знайдено — працюватиме на CPU (повільніше)"
    except Exception:  # noqa: BLE001
        pass
    rows.append(("Відеокарта", "ok" if "CUDA GPU" in gpu else "warn", gpu))
    return rows, ok


LANGS = [("Українська", "uk"), ("English", "en")]
MODELS = ["large-v3", "medium", "small"]
COMPUTE_TYPES = [
    ("Найкраща якість (float16)", "float16"),
    ("Швидше, менше пам'яті (int8_float16)", "int8_float16"),
]
_COMPUTE_BY_LABEL = {lbl: val for lbl, val in COMPUTE_TYPES}


def _compute_label(value):
    for lbl, val in COMPUTE_TYPES:
        if val == value:
            return lbl
    return COMPUTE_TYPES[0][0]


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_settings(d):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        pass


# =====================================================================
class StartPanel(ttk.Frame):
    """Component check + mode / options chooser + the Почати button."""

    def __init__(self, master, on_start):
        super().__init__(master, padding=16)
        self.on_start = on_start
        self.q = queue.Queue()
        self.checking = False
        s = load_settings()

        ttk.Label(self, text="Транскрипція лекції", font=("Segoe UI", 16, "bold")
                  ).pack(anchor="w")
        ttk.Label(self, text="Записує звук з динаміків і показує текст у цьому вікні.",
                  foreground="#666").pack(anchor="w", pady=(0, 12))

        # --- components -------------------------------------------
        box = ttk.LabelFrame(self, text="Компоненти", padding=10)
        box.pack(fill="x")
        self.rows_frame = ttk.Frame(box)
        self.rows_frame.pack(fill="x")
        self.fix_btn = ttk.Button(box, text="Встановити / полагодити",
                                  command=self._fix)
        self.setup_log = scrolledtext.ScrolledText(box, height=7, font=("Consolas", 9),
                                                   state="disabled")

        # --- options ---------------------------------------------
        opt = ttk.Frame(self)
        opt.pack(fill="x", pady=12)

        ttk.Label(opt, text="Мова лекції", font=("Segoe UI", 10, "bold")
                  ).grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.lang = tk.StringVar(value=s.get("language") or "uk")
        lf = ttk.Frame(opt)
        lf.grid(row=1, column=0, sticky="w", pady=(0, 10))
        for label, code in LANGS:
            ttk.Radiobutton(lf, text=label, value=code, variable=self.lang,
                            style="Toolbutton").pack(side="left", padx=(0, 4))
        opt.columnconfigure(0, weight=1)

        self.silence = tk.BooleanVar(value=s.get("silence", True))
        self.hotkeys = tk.BooleanVar(value=s.get("hotkeys", True))
        cf = ttk.Frame(opt)
        cf.grid(row=2, column=0, sticky="w")
        ttk.Checkbutton(cf, text="Сповіщати, якщо зник звук", variable=self.silence
                        ).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(cf, text="Гарячі клавіші-мітки", variable=self.hotkeys
                        ).pack(side="left")

        # advanced (collapsible) -- every knob that used to require editing a
        # .bat file or passing CLI flags lives here now.
        self.adv_open = tk.BooleanVar(value=False)
        self.adv_btn = ttk.Checkbutton(opt, text="Додатково", style="Toolbutton",
                                       variable=self.adv_open, command=self._toggle_adv)
        self.adv_btn.grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.adv = ttk.Frame(opt)

        def _section(row, text):
            ttk.Label(self.adv, text=text, font=("Segoe UI", 9, "bold")).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(8, 2))

        ttk.Label(self.adv, text="Пристрій виводу").grid(row=1, column=0, sticky="w")
        self.device = tk.StringVar(value="За замовчуванням")
        self._devs = [("", "За замовчуванням")]
        self.dev_combo = ttk.Combobox(self.adv, textvariable=self.device, width=44,
                                      state="readonly", values=["За замовчуванням"])
        self.dev_combo.grid(row=1, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(self.adv, text="Автостоп через, хв (0 = вимк.)").grid(
            row=2, column=0, sticky="w")
        self.duration = tk.StringVar(value=str(s.get("duration", 0) or 0))
        ttk.Entry(self.adv, textvariable=self.duration, width=10).grid(
            row=2, column=1, sticky="w", padx=6, pady=2)

        _section(3, "Розпізнавання")
        ttk.Label(self.adv, text="Підказка (тема, імена, терміни)").grid(
            row=4, column=0, sticky="w")
        self.prompt = tk.StringVar(value=s.get("prompt", ""))
        ttk.Entry(self.adv, textvariable=self.prompt, width=40).grid(
            row=4, column=1, sticky="we", padx=6, pady=2)
        ttk.Label(self.adv, text="Модель").grid(row=5, column=0, sticky="w")
        self.model = tk.StringVar(value=s.get("model") or "large-v3")
        ttk.Combobox(self.adv, textvariable=self.model, width=16, state="readonly",
                     values=MODELS).grid(row=5, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(self.adv, text="Швидкість / якість").grid(row=6, column=0, sticky="w")
        self.compute_label = tk.StringVar(
            value=_compute_label(s.get("compute_type", "float16")))
        ttk.Combobox(self.adv, textvariable=self.compute_label, width=34, state="readonly",
                     values=[lbl for lbl, _ in COMPUTE_TYPES]).grid(
            row=6, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(self.adv, text="Ширина променевого пошуку (beam)").grid(
            row=7, column=0, sticky="w")
        self.beam_size = tk.StringVar(value=str(s.get("beam_size", 5)))
        ttk.Spinbox(self.adv, from_=1, to=5, textvariable=self.beam_size, width=6
                    ).grid(row=7, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(self.adv, text="Пауза, що завершує рядок, с").grid(
            row=8, column=0, sticky="w")
        self.min_silence = tk.StringVar(value=str(s.get("min_silence", 0.6)))
        ttk.Entry(self.adv, textvariable=self.min_silence, width=10).grid(
            row=8, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(self.adv, text="Макс. довжина сегмента, с").grid(
            row=9, column=0, sticky="w")
        self.max_segment = tk.StringVar(value=str(s.get("max_segment", 18)))
        ttk.Entry(self.adv, textvariable=self.max_segment, width=10).grid(
            row=9, column=1, sticky="w", padx=6, pady=2)

        _section(10, "Гарячі клавіші")
        ttk.Label(self.adv, text='Мітка "важливо"').grid(row=11, column=0, sticky="w")
        self.mark_key = tk.StringVar(value=s.get("mark_key") or "ctrl+alt+m")
        ttk.Entry(self.adv, textvariable=self.mark_key, width=16).grid(
            row=11, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(self.adv, text='Мітка "не зрозумів"').grid(row=12, column=0, sticky="w")
        self.confused_key = tk.StringVar(value=s.get("confused_key") or "ctrl+alt+k")
        ttk.Entry(self.adv, textvariable=self.confused_key, width=16).grid(
            row=12, column=1, sticky="w", padx=6, pady=2)

        _section(13, "Сповіщення про тишу")
        ttk.Label(self.adv, text="Попереджати через, с").grid(row=14, column=0, sticky="w")
        self.silence_secs = tk.StringVar(value=str(s.get("silence_secs", 20)))
        ttk.Entry(self.adv, textvariable=self.silence_secs, width=10).grid(
            row=14, column=1, sticky="w", padx=6, pady=2)
        self.no_keepalive = tk.BooleanVar(value=s.get("no_keepalive", False))
        ttk.Checkbutton(self.adv, text="Без тихого сигналу підтримки з'єднання",
                        variable=self.no_keepalive).grid(
            row=15, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # --- go --------------------------------------------------
        self.go = ttk.Button(self, text="▶   Почати запис", command=self._go)
        self.go.pack(fill="x", ipady=6, pady=(6, 0))
        self.hint = ttk.Label(self, text="", foreground="#666")
        self.hint.pack(anchor="w", pady=(6, 0))

        self._render_rows([(l, "warn", "перевірка…") for l, _, _ in COMPONENTS])
        self.go.config(state="disabled")
        self.after(80, self._drain)
        self._recheck()

    # ---- components check ----------------------------------------
    def _recheck(self):
        if self.checking:
            return
        self.checking = True
        self._check_started = time.monotonic()
        self.hint.config(text="Перевірка компонентів…")
        threading.Thread(target=self._do_check, daemon=True).start()
        self.after(4000, self._check_watchdog)

    def _check_watchdog(self):
        # check_components() can genuinely take a while the very first time --
        # Windows (Smart App Control) or an antivirus scans a freshly-installed
        # .pyd it hasn't seen before, which can itself wait on a network call.
        # Say so instead of leaving the panel looking frozen.
        if not self.checking or not self.winfo_exists():
            return
        elapsed = time.monotonic() - self._check_started
        if elapsed > 8:
            self.hint.config(
                text=f"Перевірка триває довше, ніж зазвичай ({elapsed:.0f} с) — "
                     "схоже, Windows уперше перевіряє нові файли (Smart App "
                     "Control) або мережа зараз повільна. Це одноразово, "
                     "зачекайте — вікно не зависло.")
        self.after(4000, self._check_watchdog)

    def _do_check(self):
        rows, ok = check_components()
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
            self.hint.config(
                text="Бракує компонентів. Натисніть «Встановити / полагодити».")
        else:
            self.fix_btn.pack_forget()
            self.setup_log.pack_forget()
            self.hint.config(text="Готово. Оберіть режим і натисніть «Почати».")
            self._load_devices()
        self.go.config(state=("disabled" if need_fix else "normal"))

    # ---- fix / setup.bat ---------------------------------------
    def _fix(self):
        self.fix_btn.config(state="disabled")
        self.go.config(state="disabled")
        self.setup_log.pack(fill="x", pady=(8, 0))
        self._append_log("Запускаю setup.bat …  (це може зайняти кілька хвилин)\n")
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
        except Exception as exc:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001
            devs = []
        self._devs = [("", "За замовчуванням")] + [(str(i), lbl) for i, lbl in devs]
        self.dev_combo.config(values=[lbl for _, lbl in self._devs])

    # ---- advanced toggle -------------------------------------
    def _toggle_adv(self):
        top = self.winfo_toplevel()
        if self.adv_open.get():
            self.adv.grid(row=4, column=0, sticky="we", pady=(4, 0))
            top.geometry("760x920")
        else:
            self.adv.grid_forget()
            top.geometry("720x640")

    # ---- queue pump -----------------------------------------
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
                        "\nГотово.\n" if payload == 0 else
                        f"\nЗавершилось з кодом {payload}. Дивіться повідомлення вище.\n")
                    if payload == 0:
                        self._recheck()
        except queue.Empty:
            pass
        self.after(120, self._drain)

    # ---- build args & start -------------------------------
    def _go(self):
        if T is None:
            messagebox.showerror("Транскрипція лекції", "Двигун ще не готовий.")
            return

        def _float(var, default):
            try:
                return max(0.0, float(var.get().replace(",", ".")))
            except ValueError:
                return default

        args = T.default_args()
        args.language = self.lang.get() or "uk"
        args.prompt = self.prompt.get().strip() or None
        args.model = self.model.get() or "large-v3"
        args.compute_type = _COMPUTE_BY_LABEL.get(self.compute_label.get(), "float16")
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

        # advanced
        idx = None
        for val, lbl in self._devs:
            if lbl == self.device.get() and val:
                idx = int(val)
        args.device_index = idx
        args.duration = _float(self.duration, 0.0)

        save_settings({
            "language": self.lang.get(), "silence": self.silence.get(),
            "hotkeys": self.hotkeys.get(), "duration": args.duration,
            "prompt": self.prompt.get(), "model": args.model,
            "compute_type": args.compute_type, "beam_size": args.beam_size,
            "min_silence": args.min_silence, "max_segment": args.max_segment,
            "mark_key": self.mark_key.get(), "confused_key": self.confused_key.get(),
            "silence_secs": silence_secs, "no_keepalive": args.no_keepalive,
        })
        self.on_start(args)


# =====================================================================
class TranscriptView(ttk.Frame):
    """The live transcript, once a session is running."""

    def __init__(self, master, args, on_back):
        super().__init__(master)
        self.args = args
        self.on_back = on_back
        self.q = queue.Queue()
        self.count = 0
        self.ended = False
        self.status_extra = "запуск…"

        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill="x")
        ttk.Button(top, text="⏹  Зупинити", command=self._stop).pack(side="left")
        ttk.Button(top, text="📋  Скопіювати транскрипт", command=self.copy_all
                   ).pack(side="left", padx=(8, 0))
        self.autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Стежити за низом", variable=self.autoscroll
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

    # ---- rendering ---------------------------------------------------
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

    # ---- clipboard ---------------------------------------------------
    def copy_all(self):
        self.winfo_toplevel().clipboard_clear()
        self.winfo_toplevel().clipboard_append(self.text.get("1.0", "end-1c"))

    # ---- status / queue ------------------------------------
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
                    self.status_extra = "запис завершено"
                    if self.count == 0 and self.session.error:
                        messagebox.showerror(
                            "Транскрипція лекції",
                            "Не вдалося почати запис:\n\n" + self.session.error)
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
        head = "🔴 НЕМАЄ ЗВУКУ" if self.session.silent else "🟢 запис"
        if self.ended:
            head = "⏹ завершено"
        self.status.config(text="   ·   ".join(
            [head, dev, el, f"{self.count} рядків", self.status_extra]))

    # ---- stop ---------------------------------------------
    def _stop(self):
        self.on_back()

    def stop_session(self):
        try:
            self.session.stop()
        except Exception:  # noqa: BLE001
            pass


# =====================================================================
class MainWindow:
    def __init__(self, root, direct_args=None):
        self.root = root
        self.view = None
        root.title("Транскрипція лекції")
        root.geometry("720x640")
        root.minsize(560, 420)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            style = ttk.Style()
            if "vista" in style.theme_names():
                style.theme_use("vista")
        except Exception:  # noqa: BLE001
            pass
        if direct_args is not None:
            self._start_session(direct_args)
        else:
            self._show_panel()

    # ---- view switching --------------------------------------
    def _show_panel(self):
        if self.view is not None:
            self.view.destroy()
        self.root.geometry("720x640")
        self.view = StartPanel(self.root, on_start=self._start_session)
        self.view.pack(fill="both", expand=True)

    def _start_session(self, args):
        if self.view is not None:
            self.view.destroy()
        self.root.geometry("960x700")
        self.view = TranscriptView(self.root, args, on_back=self._back_to_panel)
        self.view.pack(fill="both", expand=True)

    def _back_to_panel(self):
        self._teardown_then(self._show_panel)

    def _on_close(self):
        self._teardown_then(self.root.destroy)

    def _teardown_then(self, done_cb):
        """Stop a running session off the UI thread, then run done_cb on it.
        Polls a plain Event via after() -- never touches Tk from the worker."""
        v = self.view
        if not isinstance(v, TranscriptView):
            done_cb()
            return
        started = time.monotonic()

        def set_status(text):
            try:
                v.status.config(text=text)
            except Exception:  # noqa: BLE001
                pass

        set_status("Зупиняю запис і зберігаю файли…")
        done = threading.Event()
        threading.Thread(target=lambda: (v.stop_session(), done.set()),
                         daemon=True).start()

        def poll():
            if done.is_set():
                done_cb()
                return
            elapsed = time.monotonic() - started
            if elapsed > 5:
                # e.g. the model was still (down)loading when Stop was pressed --
                # that step can't be interrupted, so say so instead of looking frozen.
                set_status(f"Зупиняю запис… ({elapsed:.0f} с) — можливо, ще "
                           "завершується завантаження моделі, зачекайте.")
            self.root.after(300, poll)
        self.root.after(300, poll)


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass

    # With options on the command line (gui.bat --language uk ...) skip the panel
    # and start straight away; otherwise show the start panel.
    direct = None
    if sys.argv[1:]:
        _, ok = check_components()
        if not (ok and T is not None):
            _message_box("Спершу встановіть залежності (запустіть setup.bat), "
                         "потім використовуйте параметри командного рядка.")
            return
        # SystemExit from --help / bad flags is intentional: let it through.
        direct = T.build_parser().parse_args(sys.argv[1:])
        if direct.list_devices:
            import pyaudiowpatch as _pa
            pa = _pa.PyAudio()
            try:
                T.list_devices(pa)
            finally:
                pa.terminate()
            return

    root = tk.Tk()
    MainWindow(root, direct_args=direct)
    root.mainloop()


if __name__ == "__main__":
    main()
