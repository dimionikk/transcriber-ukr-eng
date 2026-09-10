"""
Lecture transcriber - window version.

A single window that shows the transcription as it comes in.  No console.

  * Click the star in the left margin of any line to flag it as important.
    Flagged lines are highlighted and also collected into a sidecar file
    'Запис ....важливо.txt' next to the transcript.
  * Menu bar:
      Файл   - open the recording folder / the audio, save the transcript as...
      Правка - select all, copy selection, copy the whole transcript,
               copy only the important lines
  * The global marker hotkeys (Ctrl+Alt+M / Ctrl+Alt+K) still work even when the
    window is not focused; inside the window Ctrl+M toggles the last line.

Run with the same options as transcribe.py, e.g.:
    pythonw gui.py --language uk --prompt "тема, лектор, GMRES"
"""

import datetime as dt
import os
import queue
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _message_box(text: str, title: str = "Транскрипція лекції") -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror(title, text)
        r.destroy()
    except Exception:  # noqa: BLE001
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
        except Exception:  # noqa: BLE001
            pass


# --- dependencies -------------------------------------------------------
try:
    import tkinter as tk
    from tkinter import ttk, filedialog
except Exception:  # noqa: BLE001
    _message_box("Не вдалося завантажити tkinter.\n\nПоставте Python з python.org "
                 "з увімкненим компонентом 'tcl/tk and IDLE'.")
    sys.exit(1)

try:
    import transcribe as T
    import soundfile  # noqa: F401  (checked here so the message is friendly)
    import faster_whisper  # noqa: F401
except Exception as exc:  # noqa: BLE001
    setup = os.path.join(HERE, "setup.bat")
    _message_box("Залежності ще не встановлені:\n  " + str(exc) +
                 "\n\nЗараз відкриється setup.bat — дочекайтесь кінця встановлення "
                 "і запустіть це вікно знову.")
    try:
        os.startfile(setup)  # noqa: S606
    except Exception:  # noqa: BLE001
        pass
    sys.exit(1)


STAR_ON = "★ "
STAR_OFF = "☆ "


class App:
    def __init__(self, root: "tk.Tk", args):
        self.root = root
        self.args = args
        self.q: "queue.Queue" = queue.Queue()
        self.rows = []          # {tag, ts, text, marked, lineno}
        self.by_line = {}       # transcript lineno -> index in self.rows
        self.count = 0
        self.ended = False
        self.closing = False
        self.status_extra = "запуск…"

        root.title("Транскрипція лекції")
        root.geometry("940x660")
        root.minsize(560, 360)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_menu()

        self.status = ttk.Label(root, anchor="w", padding=(8, 4))
        self.status.pack(side="top", fill="x")

        body = ttk.Frame(root)
        body.pack(side="top", fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", font=("Segoe UI", 11),
                            spacing1=3, spacing3=3, padx=8, pady=6,
                            cursor="arrow", undo=False)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set, state="disabled")
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.text.tag_configure("star", foreground="#c99a00")
        self.text.tag_configure("ts", foreground="#8a8a8a")
        self.text.tag_configure("imp", background="#fff4c2")
        self.text.tag_configure("alertrow", background="#ffe1e1")
        self.text.tag_configure("markrow", background="#e3f1ff")

        self.text.bind("<Button-1>", self._on_click)
        self.text.bind("<Button-3>", self._on_rclick)
        root.bind("<Control-a>", self._select_all)
        root.bind("<Control-A>", self._select_all)
        root.bind("<Control-m>", lambda e: self._toggle_last())
        root.bind("<Control-M>", lambda e: self._toggle_last())

        bar = ttk.Frame(root, padding=(8, 6))
        bar.pack(side="bottom", fill="x")
        ttk.Button(bar, text="★  Позначити останній рядок  (Ctrl+M)",
                   command=self._toggle_last).pack(side="left")
        self.autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Стежити за низом", variable=self.autoscroll
                        ).pack(side="right")

        # engine
        self.session = T.Session(args, on_event=self._engine_event)
        self.session.start()
        self.root.after(150, self._drain)
        self.root.after(1000, self._tick_status)

    # ---- menu -----------------------------------------------------
    def _build_menu(self):
        m = tk.Menu(self.root)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Відкрити папку запису", command=self._open_folder)
        f.add_command(label="Відкрити аудіозапис", command=self._open_audio)
        f.add_separator()
        f.add_command(label="Зберегти транскрипт як…", command=self._save_as)
        f.add_command(label="Зберегти важливі рядки як…", command=self._save_important_as)
        f.add_separator()
        f.add_command(label="Вийти", command=self.on_close)
        m.add_cascade(label="Файл", menu=f)

        e = tk.Menu(m, tearoff=0)
        e.add_command(label="Виділити весь текст\tCtrl+A", command=self._select_all)
        e.add_command(label="Копіювати виділене\tCtrl+C", command=self._copy_selection)
        e.add_separator()
        e.add_command(label="Скопіювати весь транскрипт", command=self._copy_all)
        e.add_command(label="Скопіювати лише важливі рядки", command=self._copy_important)
        m.add_cascade(label="Правка", menu=e)

        h = tk.Menu(m, tearoff=0)
        h.add_command(label="Як користуватися", command=self._help)
        m.add_cascade(label="Довідка", menu=h)
        self.root.config(menu=m)

    # ---- engine plumbing ---------------------------------------------
    def _engine_event(self, kind, ts, text):
        # called from the Session thread -> just queue it
        self.q.put((kind, ts, text))

    def _drain(self):
        try:
            while True:
                kind, ts, text = self.q.get_nowait()
                if kind == "line":
                    self._add_row(ts or dt.datetime.now(), text)
                    self.count += 1
                elif kind == "mark":
                    self._add_special(ts or dt.datetime.now(), text, "markrow")
                elif kind == "alert":
                    self._add_special(ts or dt.datetime.now(), text, "alertrow")
                elif kind == "resume":
                    self._add_special(ts or dt.datetime.now(), text, "markrow")
                elif kind == "info":
                    self.status_extra = text
                elif kind == "ended":
                    self.ended = True
                    self.status_extra = "запис завершено"
        except queue.Empty:
            pass
        self._refresh_status()
        if not (self.ended and self.closing):
            self.root.after(150, self._drain)

    # ---- rendering ----------------------------------------------------
    def _at_bottom(self):
        return self.text.yview()[1] > 0.999

    def _add_row(self, ts, body):
        stick = self.autoscroll.get() and self._at_bottom()
        self.text.configure(state="normal")
        lineno = int(self.text.index("end-1c").split(".")[0])
        tag = f"L{len(self.rows)}"
        self.text.insert("end", STAR_OFF, ("star", tag))
        self.text.insert("end", f"{ts:%H:%M:%S}  ", ("ts", tag))
        self.text.insert("end", body + "\n", (tag,))
        self.text.configure(state="disabled")
        self.rows.append({"tag": tag, "ts": ts, "text": body,
                          "marked": False, "lineno": lineno})
        self.by_line[lineno] = len(self.rows) - 1
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

    def _toggle(self, i):
        r = self.rows[i]
        r["marked"] = not r["marked"]
        ln = r["lineno"]
        self.text.configure(state="normal")
        self.text.delete(f"{ln}.0", f"{ln}.2")
        self.text.insert(f"{ln}.0", STAR_ON if r["marked"] else STAR_OFF,
                         ("star", r["tag"]))
        if r["marked"]:
            self.text.tag_add("imp", f"{ln}.0", f"{ln}.0 lineend")
        else:
            self.text.tag_remove("imp", f"{ln}.0", f"{ln}.0 lineend")
        self.text.configure(state="disabled")
        self._write_sidecar()
        self._refresh_status()

    def _toggle_last(self):
        if self.rows:
            self._toggle(len(self.rows) - 1)

    def _row_at_event(self, event):
        try:
            ln = int(self.text.index(f"@{event.x},{event.y}").split(".")[0])
        except tk.TclError:
            return None
        return self.by_line.get(ln)

    def _on_click(self, event):
        # only the star column toggles, so text stays selectable
        try:
            col = int(self.text.index(f"@{event.x},{event.y}").split(".")[1])
        except tk.TclError:
            return
        if col <= 1:
            i = self._row_at_event(event)
            if i is not None:
                self._toggle(i)
                return "break"

    def _on_rclick(self, event):
        i = self._row_at_event(event)
        if i is None:
            return
        menu = tk.Menu(self.root, tearoff=0)
        lbl = "Зняти позначку" if self.rows[i]["marked"] else "Позначити як важливе"
        menu.add_command(label=lbl, command=lambda: self._toggle(i))
        menu.add_command(label="Скопіювати цей рядок",
                         command=lambda: self._to_clipboard(
                             f"[{self.rows[i]['ts']:%H:%M:%S}] {self.rows[i]['text']}"))
        menu.tk_popup(event.x_root, event.y_root)

    # ---- clipboard / files -----------------------------------------
    def _to_clipboard(self, s):
        self.root.clipboard_clear()
        self.root.clipboard_append(s)

    def _select_all(self, *_):
        self.text.tag_remove("sel", "1.0", "end")
        self.text.tag_add("sel", "1.0", "end-1c")
        self.text.focus_set()
        return "break"

    def _copy_selection(self, *_):
        try:
            self._to_clipboard(self.text.get("sel.first", "sel.last"))
        except tk.TclError:
            pass

    def _copy_all(self):
        self._to_clipboard(self.text.get("1.0", "end-1c"))

    def _important_lines(self):
        return [f"[{r['ts']:%H:%M:%S}] {r['text']}" for r in self.rows if r["marked"]]

    def _copy_important(self):
        self._to_clipboard("\n".join(self._important_lines()))

    def _write_sidecar(self):
        out = self.session.outfile
        if not out:
            return
        path = os.path.splitext(out)[0] + ".важливо.txt"
        lines = self._important_lines()
        try:
            if not lines:
                if os.path.exists(path):
                    os.remove(path)
                return
            with open(path, "w", encoding="utf-8") as f:
                f.write("Важливі рядки — " + os.path.basename(out) + "\n\n")
                f.write("\n".join(lines) + "\n")
        except OSError:
            pass

    def _open_folder(self):
        out = self.session.outfile
        if out and os.path.isdir(os.path.dirname(out)):
            os.startfile(os.path.dirname(out))  # noqa: S606

    def _open_audio(self):
        p = self.session.audio_path
        if p and os.path.exists(p):
            os.startfile(p)  # noqa: S606

    def _save_as(self):
        p = filedialog.asksaveasfilename(
            defaultextension=".txt", filetypes=[("Текст", "*.txt")],
            initialfile="транскрипт.txt")
        if p:
            try:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(self.text.get("1.0", "end-1c"))
            except OSError as exc:
                _message_box(f"Не вдалося зберегти: {exc}")

    def _save_important_as(self):
        p = filedialog.asksaveasfilename(
            defaultextension=".txt", filetypes=[("Текст", "*.txt")],
            initialfile="важливе.txt")
        if p:
            try:
                with open(p, "w", encoding="utf-8") as f:
                    f.write("\n".join(self._important_lines()) + "\n")
            except OSError as exc:
                _message_box(f"Не вдалося зберегти: {exc}")

    def _help(self):
        from tkinter import messagebox
        messagebox.showinfo(
            "Як користуватися",
            "• Текст лекції зʼявляється сам, поки грає звук.\n"
            "• Клац по зірці ☆ ліворуч від рядка — позначити його важливим (★).\n"
            "  Права кнопка — те саме через меню + копіювати рядок.\n"
            "• Ctrl+M — позначити останній рядок.\n"
            "• Правка → Скопіювати весь транскрипт / лише важливі рядки.\n"
            "• Важливі рядки також пишуться у файл «…важливо.txt» поряд із записом.\n"
            "• Глобальні гарячі клавіші Ctrl+Alt+M / Ctrl+Alt+K працюють навіть\n"
            "  коли вікно згорнуте.\n"
            "• Червоний рядок = пропав звук (перевір пристрій виводу Windows).")

    # ---- status ----------------------------------------------------
    def _tick_status(self):
        self._refresh_status()
        if not self.closing:
            self.root.after(1000, self._tick_status)

    def _refresh_status(self):
        dev = self.session.device_name or "…"
        started = self.session.started_at
        elapsed = "--:--:--"
        if started:
            s = int(time.monotonic() - started)
            elapsed = f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"
        marked = sum(1 for r in self.rows if r["marked"])
        dot = "🔴 НЕМАЄ ЗВУКУ" if self.session.silent else "🟢"
        parts = [dot, dev, elapsed, f"{self.count} рядків", f"{marked} важл."]
        if self.status_extra:
            parts.append(self.status_extra)
        self.status.config(text="   ·   ".join(parts))

    # ---- shutdown ------------------------------------------------
    def on_close(self):
        if self.closing:
            return
        self.closing = True
        self.status.config(text="Зупиняю запис і зберігаю файли…")
        self.root.update_idletasks()
        threading.Thread(target=self._finalize, daemon=True).start()

    def _finalize(self):
        try:
            self.session.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.root.after(0, self.root.destroy)
        except Exception:  # noqa: BLE001
            pass


def main():
    args = T.build_parser().parse_args()
    if args.list_devices:
        import pyaudiowpatch as pa_mod
        pa = pa_mod.PyAudio()
        try:
            T.list_devices(pa)
        finally:
            pa.terminate()
        return

    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass

    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:  # noqa: BLE001
        pass
    App(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()
