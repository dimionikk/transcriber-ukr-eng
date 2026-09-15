import argparse
import datetime as dt
import glob
import os
import queue
import site
import subprocess
import sys
import threading
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _register_cuda_dlls() -> None:
    roots = list(site.getsitepackages())
    if hasattr(site, "getusersitepackages"):
        roots.append(site.getusersitepackages())
    for root in roots:
        for bindir in glob.glob(os.path.join(root, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(bindir)
            except OSError:
                pass
            os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")


_register_cuda_dlls()

import numpy as np
import pyaudiowpatch as pyaudio

try:
    from scipy.signal import resample_poly
except Exception as _exc:
    # scipy.signal transitively imports numpy.random; Windows Smart App Control
    # has been seen to block that specific compiled file outright (not just a
    # slow first-time scan), which would otherwise take down this whole module.
    # Fall back to a plain numpy resampler so recording can still start.
    resample_poly = None
    _scipy_import_error = _exc

TARGET_RATE = 16000
FRAME_MS = 30
SILENCE_RMS = 260
SMARTCUT_WINDOW = 1.2

RECORDS_DIRNAME = "Records"

UI_TEXT = {
    "uk": {
        "toast_title": "Транскрипція лекції",
        "mark_important": "⭐  ВАЖЛИВО  ⭐",
        "mark_confused": "❓  НЕ ЗРОЗУМІВ  ❓",
        "resumed_line": "✅ (звук відновлено)",
        "resumed_event": "✅ звук відновлено",
        "resumed_toast": "✅ Звук відновлено",
        "lost_line": "⚠️ (звук зник — перевір пристрій виводу Windows)",
        "lost_event": "⚠️ звук зник — перевір пристрій виводу Windows",
        "lost_toast": "⚠️ Не чую звук — перевір пристрій виводу",
        "error_prefix": "ПОМИЛКА",
    },
    "en": {
        "toast_title": "Lecture Transcriber",
        "mark_important": "⭐  IMPORTANT  ⭐",
        "mark_confused": "❓  DID NOT UNDERSTAND  ❓",
        "resumed_line": "✅ (sound resumed)",
        "resumed_event": "✅ sound resumed",
        "resumed_toast": "✅ Sound resumed",
        "lost_line": "⚠️ (sound lost — check the Windows output device)",
        "lost_event": "⚠️ sound lost — check the Windows output device",
        "lost_toast": "⚠️ No sound detected — check the output device",
        "error_prefix": "ERROR",
    },
}


def ui_text(ui_language: str) -> dict:
    return UI_TEXT.get(ui_language, UI_TEXT["uk"])


def log(msg: str) -> None:
    try:
        print(f"{dt.datetime.now():%H:%M:%S}  {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


def script_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def new_session_dir(folder: str) -> str:
    base = os.path.join(folder, RECORDS_DIRNAME, f"Session {dt.date.today():%d.%m.%Y}")
    n = 1
    while True:
        path = base if n == 1 else f"{base} ({n})"
        try:
            os.makedirs(path)
            return path
        except FileExistsError:
            n += 1


def notify(title: str, message: str) -> None:
    if sys.platform != "win32":
        return

    ps = (
        "$ErrorActionPreference='Stop';"
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]>$null;"
        "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime]>$null;"
        "$tpl=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$tx=$tpl.GetElementsByTagName('text');"
        "$tx.Item(0).AppendChild($tpl.CreateTextNode($env:TOAST_TITLE))>$null;"
        "$tx.Item(1).AppendChild($tpl.CreateTextNode($env:TOAST_MSG))>$null;"
        "$t=[Windows.UI.Notifications.ToastNotification]::new($tpl);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($t)"
    )

    def _run() -> None:
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                env={**os.environ, "TOAST_TITLE": title, "TOAST_MSG": message},
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                capture_output=True, timeout=15,
            )
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.MessageBeep(0x30)
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()


class Sink:
    def __init__(self, fout):
        self.fout = fout
        self._lock = threading.Lock()

    def write_raw(self, text: str) -> None:
        with self._lock:
            self.fout.write(text)
            self.fout.flush()

    def write_line(self, line: str) -> None:
        self.write_raw(line + "\n")

    def stamp(self, text: str) -> str:
        line = f"[{dt.datetime.now():%H:%M:%S}] {text}"
        self.write_line(line)
        return line


def pick_loopback(pa: pyaudio.PyAudio, device_index):
    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    render = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])

    if device_index is not None:
        return pa.get_device_info_by_index(device_index), render

    if render.get("isLoopbackDevice"):
        return render, render
    for lb in pa.get_loopback_device_info_generator():
        if render["name"] in lb["name"]:
            return lb, render
    fallback = next(iter(pa.get_loopback_device_info_generator()), None)
    if fallback is not None:
        log(f"no loopback match for '{render['name']}'; falling back to "
            f"'{fallback['name']}' so recording can still start")
        return fallback, render
    raise RuntimeError(
        "No loopback device found for the default speakers. "
        "Run with --list-devices and pass --device-index."
    )


def list_devices(pa: pyaudio.PyAudio) -> None:
    print("Loopback capture devices (use the index with --device-index):\n")
    for lb in pa.get_loopback_device_info_generator():
        print(f"  [{lb['index']:>2}] {lb['name']}  "
              f"{int(lb['defaultSampleRate'])} Hz  {lb['maxInputChannels']} ch")


class KeepAlive(threading.Thread):
    def __init__(self, pa, render_dev):
        super().__init__(daemon=True)
        self.pa = pa
        self.dev = render_dev
        self._stop = threading.Event()

    def run(self) -> None:
        rate = int(self.dev["defaultSampleRate"])
        ch = max(1, int(self.dev["maxOutputChannels"]))
        chunk = int(rate * 0.1)
        silence = (b"\x00\x00") * chunk * ch
        try:
            stream = self.pa.open(
                format=pyaudio.paInt16, channels=ch, rate=rate, output=True,
                output_device_index=self.dev["index"], frames_per_buffer=chunk,
            )
        except Exception as exc:
            log(f"keep-alive unavailable ({exc}); silence detection may lag during pauses")
            return
        try:
            while not self._stop.is_set():
                stream.write(silence)
        finally:
            stream.stop_stream()
            stream.close()

    def stop(self) -> None:
        self._stop.set()


class Recorder(threading.Thread):
    def __init__(self, pa, dev, out_q):
        super().__init__(daemon=True)
        self.pa = pa
        self.dev = dev
        self.out_q = out_q
        self.rate = int(dev["defaultSampleRate"])
        self.channels = int(dev["maxInputChannels"])
        self.bytes_seen = 0
        self._stop = threading.Event()
        self._chunk = int(self.rate * FRAME_MS / 1000)

    def _open(self):
        last = None
        for attempt in range(4):
            try:
                return self.pa.open(
                    format=pyaudio.paInt16,
                    channels=self.channels,
                    rate=self.rate,
                    input=True,
                    input_device_index=self.dev["index"],
                    frames_per_buffer=self._chunk,
                )
            except OSError as exc:
                last = exc
                log(f"  open attempt {attempt + 1} failed ({exc}); retrying ...")
                time.sleep(1.0)
        raise RuntimeError(
            f"Could not open capture device '{self.dev['name']}': {last}. "
            f"Pick another with --list-devices / --device-index."
        )

    def run(self) -> None:
        stream = self._open()
        log(f"capturing: {self.dev['name']}  ({self.rate} Hz, {self.channels} ch)")
        try:
            while not self._stop.is_set():
                data = stream.read(self._chunk, exception_on_overflow=False)
                self.bytes_seen += len(data)
                self.out_q.put(data)
        finally:
            stream.stop_stream()
            stream.close()

    def stop(self) -> None:
        self._stop.set()


def _resample_linear(buf: np.ndarray, target_rate: int, src_rate: int) -> np.ndarray:
    n_out = max(1, round(buf.size * target_rate / src_rate))
    src_x = np.arange(buf.size, dtype=np.float64)
    dst_x = np.linspace(0, buf.size - 1, n_out, dtype=np.float64)
    return np.interp(dst_x, src_x, buf).astype(np.float32)


def to_mono_16k(buf: np.ndarray, src_rate: int, channels: int) -> np.ndarray:
    if channels > 1:
        buf = buf.reshape(-1, channels).mean(axis=1)
    buf = buf.astype(np.float32) / 32768.0
    if src_rate != TARGET_RATE:
        if resample_poly is not None:
            buf = resample_poly(buf, TARGET_RATE, src_rate).astype(np.float32)
        else:
            buf = _resample_linear(buf, TARGET_RATE, src_rate)
    return buf


def load_model(preferred: str, compute_type: str):
    from faster_whisper import WhisperModel

    plan = [
        (preferred, "cuda", compute_type),
        (preferred, "cuda", "int8_float16"),
        ("medium", "cuda", "int8_float16"),
        ("small", "cpu", "int8"),
    ]
    seen = set()
    for name, device, compute in plan:
        if (name, device, compute) in seen:
            continue
        seen.add((name, device, compute))
        try:
            log(f"loading model '{name}' on {device} ({compute}) ...")
            model = WhisperModel(name, device=device, compute_type=compute)
            log(f"model ready: {name} / {device} / {compute}")
            return model
        except Exception as exc:
            log(f"  failed: {exc}")
    raise RuntimeError("Could not load any Whisper model.")


class Transcriber(threading.Thread):
    def __init__(self, model, args, src_rate, channels, sink: Sink, on_line=None):
        super().__init__(daemon=True)
        self.model = model
        self.args = args
        self.src_rate = src_rate
        self.channels = channels
        self.sink = sink
        self.on_line = on_line
        self.in_q: "queue.Queue" = queue.Queue()
        self.min_samples = int(1.0 * src_rate) * channels
        self.recent_text = ""

    def submit(self, spoken_at: dt.datetime, samples: np.ndarray) -> None:
        if samples.size >= self.min_samples:
            self.in_q.put((spoken_at, samples))

    def run(self) -> None:
        while True:
            item = self.in_q.get()
            if item is None:
                return
            spoken_at, samples = item
            try:
                self._process(spoken_at, samples)
            except Exception as exc:
                log(f"transcription error: {exc}")

    def _process(self, spoken_at: dt.datetime, samples: np.ndarray) -> None:
        audio = to_mono_16k(samples, self.src_rate, self.channels)
        if float(np.sqrt(np.mean(audio ** 2))) < 0.004:
            return

        hint = " ".join(p for p in (self.args.prompt, self.recent_text) if p).strip()
        segments, _ = self.model.transcribe(
            audio,
            language=self.args.language,
            beam_size=self.args.beam_size,
            vad_filter=True,
            no_speech_threshold=0.7,
            log_prob_threshold=-1.0,
            condition_on_previous_text=False,
            no_repeat_ngram_size=3,
            repetition_penalty=1.15,
            initial_prompt=hint or None,
        )
        parts = [s.text.strip() for s in segments if s.no_speech_prob < 0.75]
        text = " ".join(p for p in parts if p).strip()
        if not text:
            return
        self.recent_text = text[-240:]
        self.sink.write_line(f"[{spoken_at:%H:%M:%S}] {text}")
        if self.on_line:
            try:
                self.on_line(spoken_at, text)
            except Exception as exc:
                log(f"on_line callback failed: {exc}")


class Marker:
    def __init__(self, sink: Sink, specs, on_mark=None):
        self.sink = sink
        self.on_mark = on_mark
        self.specs = [(hk, lab) for hk, lab in specs if hk]
        self._active = False

    def start(self) -> None:
        if not self.specs:
            return
        try:
            import keyboard
        except Exception as exc:
            log(f"hotkey markers disabled ({exc}); 'pip install keyboard' to enable")
            return
        bound = []
        for hk, label in self.specs:
            try:
                keyboard.add_hotkey(hk, self._drop, args=(label,))
                bound.append((hk, label))
            except Exception as exc:
                log(f"could not bind hotkey '{hk}': {exc}")
        if bound:
            self._active = True
            log("markers: " + " | ".join(f"{hk} -> {lab}" for hk, lab in bound))

    def _drop(self, label: str) -> None:
        try:
            self.sink.stamp(label)
            if self.on_mark:
                self.on_mark(dt.datetime.now(), label)
        except Exception as exc:
            log(f"marker write failed: {exc}")

    def stop(self) -> None:
        if not self._active:
            return
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass


def quiet_cut_point(pending: np.ndarray, src_rate: int, channels: int) -> int:
    frame = max(1, int(src_rate * FRAME_MS / 1000)) * channels
    window = int(SMARTCUT_WINDOW * src_rate) * channels
    tail = pending[-window:]
    n = (tail.size // frame) * frame
    if n < frame * 2:
        return pending.size
    frames = tail[:n].reshape(-1, frame).astype(np.float32)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    best = int(np.argmin(rms))
    return pending.size - tail.size + best * frame


class Session:
    def __init__(self, args, on_event=None):
        self.args = args
        self._on_event = on_event or (lambda *a: None)
        self._stop = threading.Event()
        self._thread = None
        self._sink = None
        self.outfile = None
        self.device_name = None
        self.started_at = None
        self.silent = False
        self.error = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="session", daemon=True)
        self._thread.start()

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def stop(self, join_timeout: float = 300.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=join_timeout)

    def mark_now(self, label: str) -> None:
        if self._sink:
            self._sink.stamp(label)
            self._emit("mark", dt.datetime.now(), label)

    def _emit(self, kind: str, ts=None, text: str = "") -> None:
        try:
            self._on_event(kind, ts, text)
        except Exception as exc:
            log(f"on_event failed: {exc}")

    def _run(self) -> None:
        args = self.args
        ui = ui_text(getattr(args, "ui_language", "uk"))
        if resample_poly is None:
            self._emit("info", None,
                        f"scipy unavailable ({_scipy_import_error}); using a simpler resampler")
        pa = pyaudio.PyAudio()
        keepalive = rec = worker = marker = fout = sink = None
        pending = np.empty(0, dtype=np.int16)
        try:
            dev, render = pick_loopback(pa, args.device_index)
            self.device_name = dev["name"]
            raw_q: "queue.Queue[bytes]" = queue.Queue()
            rec = Recorder(pa, dev, raw_q)

            if args.outfile:
                outfile = args.outfile
            else:
                sess_dir = new_session_dir(script_dir())
                outfile = os.path.join(sess_dir, os.path.basename(sess_dir) + ".txt")
            self.outfile = outfile

            self._emit("info", None, f"loading model '{args.model}' …")
            model = load_model(args.model, args.compute_type)
            self._emit("info", None, "model ready")

            src_rate = rec.rate
            channels = rec.channels
            min_sil_frames = int(args.min_silence * 1000 / FRAME_MS)
            max_seg_samples = int(args.max_segment * src_rate) * channels
            min_seg_samples = int(1.0 * src_rate) * channels
            keep_tail = int(0.25 * src_rate) * channels
            quiet_run = 0
            deadline = time.monotonic() + args.duration * 60 if args.duration else None

            fresh = not os.path.exists(outfile) or os.path.getsize(outfile) == 0
            fout = open(outfile, "a", encoding="utf-8")
            sink = self._sink = Sink(fout)
            banner = f"===== session started {dt.datetime.now():%Y-%m-%d %H:%M:%S} =====\n"
            sink.write_raw(banner if fresh else "\n" + banner)
            self._emit("info", None, f"transcript → {outfile}")

            worker = Transcriber(model, args, src_rate, channels, sink,
                                 on_line=lambda ts, txt: self._emit("line", ts, txt))
            worker.start()

            marker = Marker(sink, [(args.mark_key, ui["mark_important"]),
                                   (args.confused_key, ui["mark_confused"])],
                            on_mark=lambda ts, txt: self._emit("mark", ts, txt))
            marker.start()

            if not args.no_keepalive:
                keepalive = KeepAlive(pa, render)
                keepalive.start()

            rec.start()
            self.started_at = time.monotonic()
            self._emit("info", None, f"capturing: {dev['name']}")

            last_rx_bytes = 0
            last_rx_time = time.monotonic()
            silent_alerted = False
            startup_grace = max(8.0, args.silence_alert)

            while not self._stop.is_set():
                now_m = time.monotonic()
                if rec.bytes_seen != last_rx_bytes:
                    last_rx_bytes = rec.bytes_seen
                    last_rx_time = now_m
                    if silent_alerted:
                        silent_alerted = False
                        self.silent = False
                        sink.stamp(ui["resumed_line"])
                        self._emit("resume", dt.datetime.now(), ui["resumed_event"])
                        if not args.no_toast:
                            notify(ui["toast_title"], ui["resumed_toast"])
                elif (args.silence_alert and not silent_alerted
                      and now_m - last_rx_time > args.silence_alert
                      and now_m - self.started_at > startup_grace):
                    silent_alerted = True
                    self.silent = True
                    gap = now_m - last_rx_time
                    sink.stamp(ui["lost_line"])
                    self._emit("alert", dt.datetime.now(), ui["lost_event"])
                    log(f"WARNING: no audio for {gap:.0f}s from '{dev['name']}'.")
                    if not args.no_toast:
                        notify(ui["toast_title"], ui["lost_toast"])

                try:
                    data = raw_q.get(timeout=1.0)
                except queue.Empty:
                    if deadline and time.monotonic() >= deadline:
                        break
                    continue

                block = np.frombuffer(data, dtype=np.int16)
                pending = np.concatenate((pending, block))

                mono = (block.reshape(-1, channels).mean(axis=1)
                        if channels > 1 else block.astype(np.float32))
                rms = float(np.sqrt(np.mean(mono.astype(np.float32) ** 2))) if mono.size else 0.0
                quiet_run = quiet_run + 1 if rms < SILENCE_RMS else 0

                hit_silence = quiet_run >= min_sil_frames and pending.size >= min_seg_samples
                too_long = pending.size >= max_seg_samples

                if hit_silence:
                    worker.submit(dt.datetime.now(), pending)
                    pending = np.empty(0, dtype=np.int16)
                    quiet_run = 0
                elif too_long:
                    cut = quiet_cut_point(pending, src_rate, channels)
                    cut = max(min_seg_samples, min(cut, pending.size - keep_tail))
                    worker.submit(dt.datetime.now(), pending[:cut].copy())
                    pending = pending[cut:].copy()
                    quiet_run = 0

                if deadline and time.monotonic() >= deadline:
                    break
        except Exception as exc:
            self.error = str(exc)
            self._emit("info", None, f"{ui['error_prefix']}: {exc}")
            log(f"session error: {exc}")
        finally:
            try:
                if rec:
                    rec.stop()
                if keepalive:
                    keepalive.stop()
                if marker:
                    marker.stop()
                time.sleep(0.3)
                if worker:
                    try:
                        worker.submit(dt.datetime.now(), pending)
                    except Exception:
                        pass
                    worker.in_q.put(None)
                    worker.join(timeout=30)
                if sink:
                    sink.write_raw(
                        f"===== session ended {dt.datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
            except Exception as exc:
                log(f"teardown: {exc}")
            finally:
                if fout and not fout.closed:
                    fout.close()
                pa.terminate()
            self._emit("ended", None, self.outfile or "")


def default_args():
    return build_parser().parse_args([])


def loopback_devices():
    out = []
    try:
        pa = pyaudio.PyAudio()
        try:
            for lb in pa.get_loopback_device_info_generator():
                out.append((int(lb["index"]),
                            f'{lb["name"]}  ({int(lb["defaultSampleRate"])} Hz)'))
        finally:
            pa.terminate()
    except Exception:
        pass
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Live lecture transcriber.")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--device-index", type=int, default=None)
    ap.add_argument("--model", default="large-v3",
                    help="Whisper model (default: large-v3)")
    ap.add_argument("--compute-type", default="float16",
                    help="GPU compute type: float16 = best quality (default), "
                         "int8_float16 = faster + lighter VRAM")
    ap.add_argument("--language", default=None,
                    help="force a language code, e.g. uk or en (default: auto)")
    ap.add_argument("--ui-language", default="uk", choices=["uk", "en"],
                    help="language for markers/toasts/alerts written by the app "
                         "itself (default: uk)")
    ap.add_argument("--prompt", default=None,
                    help="context hint for rare words (topic, lecturer, jargon)")
    ap.add_argument("--beam-size", type=int, default=5,
                    help="beam search width: 5 = accurate (default), 1 = fastest")
    ap.add_argument("--outfile", default=None,
                    help="append to this exact file instead of creating a new "
                         "per-session folder 'Records/Session DD.MM.YYYY/'")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="stop automatically after this many minutes (0 = run until Ctrl+C)")
    ap.add_argument("--min-silence", type=float, default=0.6,
                    help="seconds of quiet that closes a segment (default: 0.6)")
    ap.add_argument("--max-segment", type=float, default=18.0,
                    help="force-flush a segment after this many seconds (default: 18)")
    ap.add_argument("--no-keepalive", action="store_true",
                    help="do not play silent keep-alive audio to the output device")
    ap.add_argument("--mark-key", default="ctrl+alt+m",
                    help="global hotkey that writes an 'important' marker "
                         "(default: ctrl+alt+m; empty string disables)")
    ap.add_argument("--confused-key", default="ctrl+alt+k",
                    help="global hotkey that writes a 'did not get this' marker "
                         "(default: ctrl+alt+k; empty string disables)")
    ap.add_argument("--silence-alert", type=float, default=20.0,
                    help="notify if capture delivers no audio for this many "
                         "seconds (default: 20; 0 disables)")
    ap.add_argument("--no-toast", action="store_true",
                    help="log silence alerts to the console only, no Windows toast")
    return ap


def main() -> None:
    args = build_parser().parse_args()

    if args.list_devices:
        pa = pyaudio.PyAudio()
        try:
            list_devices(pa)
        finally:
            pa.terminate()
        return

    def on_event(kind, ts, text):
        try:
            if kind in ("line", "mark", "alert", "resume"):
                when = ts or dt.datetime.now()
                print(f"[{when:%H:%M:%S}] {text}", flush=True)
            elif kind == "info":
                log(text)
            elif kind == "ended":
                log(f"done. transcript saved to {text}")
        except Exception:
            pass

    sess = Session(args, on_event)
    log("starting; press Ctrl+C to stop.")
    sess.start()
    try:
        while sess.alive():
            time.sleep(0.3)
    except KeyboardInterrupt:
        log("stopping …")
        waited = 0.0
        while sess.alive():
            sess.stop(join_timeout=2.0)
            waited += 2.0
            if sess.alive() and waited % 10 == 0:
                log("  still stopping … (model load/cleanup can take a while)")
        sess.stop()


if __name__ == "__main__":
    main()
