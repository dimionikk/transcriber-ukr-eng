"""
Live lecture transcriber.

Captures whatever is playing on your speakers (Zoom / Meet / browser / anything),
transcribes it in near real time with faster-whisper on the GPU, and appends
timestamped lines to a text file you can keep open and scroll back through.

Each run gets its own folder, 'Записи/Запис DD.MM.YYYY/', holding that session's
transcript and audio copy together -- nothing loose to hunt for.

While it runs it also:
  * records a compact audio copy in the same folder as the transcript, so you can
    re-listen to any timestamp where Whisper garbled a term (disable --no-audio);
  * listens for global hotkeys that drop a marker line into the transcript at the
    current time -- Ctrl+Alt+M = "important", Ctrl+Alt+K = "did not get this"
    (change with --mark-key / --confused-key, empty string disables);
  * pops a Windows notification if the capture goes silent mid-lecture, e.g. the
    headset disconnected and Windows switched the default output (--no-toast off).

This file is both the command-line tool and the engine behind gui.py -- the
capture/transcribe pipeline lives in the `Session` class; `main()` is just a
console front-end for it.

Usage:
    python transcribe.py                  # transcribe the default speakers until Ctrl+C
    python transcribe.py --list-devices   # show capture devices and exit
    python transcribe.py --language uk    # force Ukrainian (default: auto-detect)
    python transcribe.py --model medium   # smaller / faster model
    python transcribe.py --duration 90    # stop automatically after 90 minutes
    python transcribe.py --prompt "..."   # hint rare terms (topic, names, jargon)
    python transcribe.py --no-audio       # transcript only, no audio copy

Stop anytime with Ctrl+C. The transcript is flushed to disk continuously.
"""

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

# Transcript text and file names are often Cyrillic; make sure a non-UTF-8
# console (or a redirected pipe, or pythonw with no console at all) can't crash
# us on an un-encodable character or a missing stream.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _register_cuda_dlls() -> None:
    """Make the pip-installed NVIDIA cuBLAS / cuDNN DLLs findable by CTranslate2."""
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
from scipy.signal import resample_poly

TARGET_RATE = 16000          # what Whisper expects
FRAME_MS = 30                # granularity for the silence detector
SILENCE_RMS = 260            # int16 RMS below this counts as "silence"
SMARTCUT_WINDOW = 1.2        # seconds to search for a quiet spot when force-cutting

# Audio-copy container / codec, tried in order (format, subtype, extension).
AUDIO_CANDIDATES = [
    ("OGG", "OPUS", ".ogg"),
    ("OGG", "VORBIS", ".ogg"),
    ("FLAC", "PCM_16", ".flac"),
]

RECORDS_DIRNAME = "Записи"


def log(msg: str) -> None:
    """Diagnostic line to stderr. Never raises -- under pythonw there is no
    stderr, and we must not take the pipeline down over a log call."""
    try:
        print(f"{dt.datetime.now():%H:%M:%S}  {msg}", file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001
        pass


def script_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def new_session_dir(folder: str) -> str:
    """Create and return a fresh per-session folder inside 'Записи':
    'Запис DD.MM.YYYY' (then ' (2)', ' (3)', ... for more runs the same day).
    This run's transcript and audio copy both go inside it, so a session is one
    self-contained folder instead of loose files to hunt for."""
    base = os.path.join(folder, RECORDS_DIRNAME, f"Запис {dt.date.today():%d.%m.%Y}")
    n = 1
    while True:
        path = base if n == 1 else f"{base} ({n})"
        try:
            os.makedirs(path)
            return path
        except FileExistsError:
            n += 1


def notify(title: str, message: str) -> None:
    """Best-effort Windows toast notification. Runs in a throwaway thread and
    never raises -- if it can't show a toast it just beeps."""
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
        except Exception:  # noqa: BLE001
            try:
                import ctypes
                ctypes.windll.user32.MessageBeep(0x30)
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=_run, daemon=True).start()


class Sink:
    """The transcript file, guarded by a lock so the transcription worker and the
    hotkey / silence-alert callbacks can all append without interleaving."""

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
        """Append a timestamped line; return it."""
        line = f"[{dt.datetime.now():%H:%M:%S}] {text}"
        self.write_line(line)
        return line


def pick_loopback(pa: pyaudio.PyAudio, device_index):
    """Return (loopback_device_info, render_device_info) to record from / keep alive."""
    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    render = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])

    if device_index is not None:
        return pa.get_device_info_by_index(device_index), render

    if render.get("isLoopbackDevice"):
        return render, render
    for lb in pa.get_loopback_device_info_generator():
        if render["name"] in lb["name"]:
            return lb, render
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
    """Plays inaudible silence to the render endpoint so WASAPI loopback keeps
    delivering frames even when nothing else is playing."""

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
        except Exception as exc:  # noqa: BLE001
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
    """Reads raw audio from the loopback device into a queue (daemon thread, so a
    silent / stuck device can never block shutdown)."""

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


def to_mono_16k(buf: np.ndarray, src_rate: int, channels: int) -> np.ndarray:
    """int16 interleaved -> float32 mono @ 16 kHz, range [-1, 1]."""
    if channels > 1:
        buf = buf.reshape(-1, channels).mean(axis=1)
    buf = buf.astype(np.float32) / 32768.0
    if src_rate != TARGET_RATE:
        buf = resample_poly(buf, TARGET_RATE, src_rate).astype(np.float32)
    return buf


class AudioWriter(threading.Thread):
    """Downmixes the captured stream to mono 16 kHz and streams it into a compact
    OGG/Opus file next to the transcript, so any timestamp can be re-listened to.
    Bounded queue: if encoding ever falls behind it drops audio rather than grow
    memory -- the transcript is the source of truth, the recording is a backup."""

    def __init__(self, base_path: str, src_rate: int, channels: int):
        super().__init__(daemon=True)
        self.src_rate = src_rate
        self.channels = max(1, channels)
        self.in_q: "queue.Queue" = queue.Queue(maxsize=4000)  # ~2 min buffer
        self.path = None
        self._subtype = None
        self._base = base_path
        self._enabled = True
        self._dropped = 0

    def _open(self):
        import soundfile as sf
        avail = set(sf.available_subtypes("OGG")) | set(sf.available_subtypes("FLAC"))
        for fmt, subtype, ext in AUDIO_CANDIDATES:
            if subtype not in avail:
                continue
            try:
                handle = sf.SoundFile(
                    self._base + ext, mode="w", samplerate=TARGET_RATE,
                    channels=1, format=fmt, subtype=subtype)
                self.path = self._base + ext
                self._subtype = f"{fmt}/{subtype}"
                return handle
            except Exception:  # noqa: BLE001
                continue
        raise RuntimeError("libsndfile can't write OGG or FLAC here")

    def feed(self, raw: bytes) -> None:
        if not self._enabled:
            return
        try:
            self.in_q.put_nowait(raw)
        except queue.Full:
            self._dropped += 1

    def run(self) -> None:
        try:
            handle = self._open()
        except Exception as exc:  # noqa: BLE001
            log(f"audio copy disabled ({exc})")
            self._enabled = False
            while self.in_q.get() is not None:  # drain until stop()
                pass
            return

        log(f"recording audio -> {self.path}  ({self._subtype})")
        buf = np.empty(0, dtype=np.int16)
        batch = self.src_rate * self.channels  # resample ~1 s at a time
        try:
            while True:
                item = self.in_q.get()
                if item is None:
                    break
                buf = np.concatenate((buf, np.frombuffer(item, dtype=np.int16)))
                if buf.size >= batch:
                    n = (buf.size // self.channels) * self.channels
                    chunk, buf = buf[:n], buf[n:].copy()
                    handle.write(to_mono_16k(chunk, self.src_rate, self.channels))
            if buf.size >= self.channels:
                n = (buf.size // self.channels) * self.channels
                handle.write(to_mono_16k(buf[:n], self.src_rate, self.channels))
        except Exception as exc:  # noqa: BLE001
            log(f"audio copy stopped early: {exc}")
        finally:
            handle.close()
        if self._dropped:
            log(f"audio copy: dropped {self._dropped} blocks (encoder fell behind)")

    def stop(self) -> None:
        self.in_q.put(None)


def load_model(preferred: str, compute_type: str):
    """Try the preferred setup, fall back to smaller / CPU configs."""
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
        except Exception as exc:  # noqa: BLE001
            log(f"  failed: {exc}")
    raise RuntimeError("Could not load any Whisper model.")


class Transcriber(threading.Thread):
    """Pulls (spoken_at, int16 samples) segments off a queue, transcribes them on
    the GPU, and appends timestamped lines. Runs independently so recording and
    segmentation never stall while the model is busy."""

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
        self.recent_text = ""  # rolling context fed back as a prompt

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
            except Exception as exc:  # noqa: BLE001
                log(f"transcription error: {exc}")

    def _process(self, spoken_at: dt.datetime, samples: np.ndarray) -> None:
        audio = to_mono_16k(samples, self.src_rate, self.channels)
        if float(np.sqrt(np.mean(audio ** 2))) < 0.004:
            return  # essentially silence -> skip (avoids model hallucinations)

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
            except Exception as exc:  # noqa: BLE001
                log(f"on_line callback failed: {exc}")


class Marker:
    """Global hotkeys that drop a marker line into the transcript at the moment
    you press them, so you can find the spots you flagged while reviewing."""

    def __init__(self, sink: Sink, specs, on_mark=None):
        # specs: list of (hotkey_string, label_written_to_transcript)
        self.sink = sink
        self.on_mark = on_mark
        self.specs = [(hk, lab) for hk, lab in specs if hk]
        self._active = False

    def start(self) -> None:
        if not self.specs:
            return
        try:
            import keyboard
        except Exception as exc:  # noqa: BLE001
            log(f"hotkey markers disabled ({exc}); 'pip install keyboard' to enable")
            return
        bound = []
        for hk, label in self.specs:
            try:
                keyboard.add_hotkey(hk, self._drop, args=(label,))
                bound.append((hk, label))
            except Exception as exc:  # noqa: BLE001
                log(f"could not bind hotkey '{hk}': {exc}")
        if bound:
            self._active = True
            log("markers: " + " | ".join(f"{hk} -> {lab}" for hk, lab in bound))

    def _drop(self, label: str) -> None:
        try:
            self.sink.stamp(label)
            if self.on_mark:
                self.on_mark(dt.datetime.now(), label)
        except Exception as exc:  # noqa: BLE001
            log(f"marker write failed: {exc}")

    def stop(self) -> None:
        if not self._active:
            return
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:  # noqa: BLE001
            pass


def quiet_cut_point(pending: np.ndarray, src_rate: int, channels: int) -> int:
    """Index in `pending` near the end where the audio is quietest, so a forced
    cut lands in a gap between words rather than mid-syllable."""
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
    """The whole capture -> transcribe -> write pipeline, running on its own
    thread. Feed events out through `on_event(kind, ts, text)`:
        kind = "info"    ts=None   text=status/diagnostic string
        kind = "line"    ts=dt     text=one transcribed sentence
        kind = "mark"    ts=dt     text=hotkey marker label
        kind = "alert"   ts=dt     text=capture went silent
        kind = "resume"  ts=dt     text=capture recovered
        kind = "ended"   ts=None   text=transcript file path
    Both transcribe.py (console) and gui.py drive it the same way:
        s = Session(args, on_event); s.start(); ...; s.stop()
    """

    def __init__(self, args, on_event=None):
        self.args = args
        self._on_event = on_event or (lambda *a: None)
        self._stop = threading.Event()
        self._thread = None
        self._sink = None
        # populated as the session comes up:
        self.outfile = None
        self.audio_path = None
        self.device_name = None
        self.started_at = None
        self.silent = False
        self.error = None

    # ----- control -----------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="session", daemon=True)
        self._thread.start()

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def stop(self, join_timeout: float = 45.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=join_timeout)

    def mark_now(self, label: str) -> None:
        """Write a marker line right now (used by an in-window button too)."""
        if self._sink:
            self._sink.stamp(label)
            self._emit("mark", dt.datetime.now(), label)

    # ----- internals -------------------------------------------------
    def _emit(self, kind: str, ts=None, text: str = "") -> None:
        try:
            self._on_event(kind, ts, text)
        except Exception as exc:  # noqa: BLE001
            log(f"on_event failed: {exc}")

    def _run(self) -> None:
        args = self.args
        pa = pyaudio.PyAudio()
        keepalive = rec = worker = marker = audio_writer = fout = sink = None
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

            if not args.no_audio:
                audio_writer = AudioWriter(os.path.splitext(outfile)[0], src_rate, channels)
                audio_writer.start()

            worker = Transcriber(model, args, src_rate, channels, sink,
                                 on_line=lambda ts, txt: self._emit("line", ts, txt))
            worker.start()

            marker = Marker(sink, [(args.mark_key, "⭐  ВАЖЛИВО  ⭐"),
                                   (args.confused_key, "❓  НЕ ЗРОЗУМІВ  ❓")],
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
                # --- silence watchdog: with keep-alive on, frames never stop
                #     during a real pause, so a byte count that stops moving
                #     means the capture broke (device changed / unplugged).
                now_m = time.monotonic()
                if rec.bytes_seen != last_rx_bytes:
                    last_rx_bytes = rec.bytes_seen
                    last_rx_time = now_m
                    if silent_alerted:
                        silent_alerted = False
                        self.silent = False
                        sink.stamp("✅ (звук відновлено)")
                        self._emit("resume", dt.datetime.now(), "✅ звук відновлено")
                        if not args.no_toast:
                            notify("Транскрипція лекції", "✅ Звук відновлено")
                elif (args.silence_alert and not silent_alerted
                      and now_m - last_rx_time > args.silence_alert
                      and now_m - self.started_at > startup_grace):
                    silent_alerted = True
                    self.silent = True
                    gap = now_m - last_rx_time
                    sink.stamp("⚠️ (звук зник — перевір пристрій виводу Windows)")
                    self._emit("alert", dt.datetime.now(),
                               "⚠️ звук зник — перевір пристрій виводу Windows")
                    log(f"WARNING: no audio for {gap:.0f}s from '{dev['name']}'.")
                    if not args.no_toast:
                        notify("Транскрипція лекції",
                               "⚠️ Не чую звук — перевір пристрій виводу")

                try:
                    data = raw_q.get(timeout=1.0)
                except queue.Empty:
                    if deadline and time.monotonic() >= deadline:
                        break
                    continue

                if audio_writer is not None:
                    audio_writer.feed(data)
                    if self.audio_path is None and audio_writer.path:
                        self.audio_path = audio_writer.path

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
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self._emit("info", None, f"ПОМИЛКА: {exc}")
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
                    except Exception:  # noqa: BLE001
                        pass
                    worker.in_q.put(None)
                    worker.join(timeout=30)
                if audio_writer:
                    audio_writer.stop()
                    audio_writer.join(timeout=30)
                    if audio_writer.path:
                        log(f"audio saved to {audio_writer.path}")
                if sink:
                    sink.write_raw(
                        f"===== session ended {dt.datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
            except Exception as exc:  # noqa: BLE001
                log(f"teardown: {exc}")
            finally:
                if fout and not fout.closed:
                    fout.close()
                pa.terminate()
            self._emit("ended", None, self.outfile or "")


def default_args():
    """A fresh argparse.Namespace with every option at its default -- the
    starting point the window tweaks before handing it to a Session."""
    return build_parser().parse_args([])


def loopback_devices():
    """[(index, label), ...] of loopback capture devices, for a picker. Best-effort."""
    out = []
    try:
        pa = pyaudio.PyAudio()
        try:
            for lb in pa.get_loopback_device_info_generator():
                out.append((int(lb["index"]),
                            f'{lb["name"]}  ({int(lb["defaultSampleRate"])} Hz)'))
        finally:
            pa.terminate()
    except Exception:  # noqa: BLE001
        pass
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--device-index", type=int, default=None)
    ap.add_argument("--model", default="large-v3",
                    help="Whisper model (default: large-v3)")
    ap.add_argument("--compute-type", default="float16",
                    help="GPU compute type: float16 = best quality (default), "
                         "int8_float16 = faster + lighter VRAM")
    ap.add_argument("--language", default=None,
                    help="force a language code, e.g. uk or en (default: auto)")
    ap.add_argument("--prompt", default=None,
                    help="context hint for rare words (topic, lecturer, jargon)")
    ap.add_argument("--beam-size", type=int, default=5,
                    help="beam search width: 5 = accurate (default), 1 = fastest")
    ap.add_argument("--outfile", default=None,
                    help="append to this exact file instead of creating a new "
                         "per-session folder 'Записи/Запис DD.MM.YYYY/' (audio "
                         "goes next to whatever file you name here)")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="stop automatically after this many minutes (0 = run until Ctrl+C)")
    ap.add_argument("--min-silence", type=float, default=0.6,
                    help="seconds of quiet that closes a segment (default: 0.6)")
    ap.add_argument("--max-segment", type=float, default=18.0,
                    help="force-flush a segment after this many seconds (default: 18)")
    ap.add_argument("--no-keepalive", action="store_true",
                    help="do not play silent keep-alive audio to the output device")
    ap.add_argument("--no-audio", action="store_true",
                    help="do not save the compact audio copy next to the transcript")
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
        except Exception:  # noqa: BLE001
            pass

    sess = Session(args, on_event)
    log("starting; press Ctrl+C to stop.")
    sess.start()
    try:
        while sess.alive():
            time.sleep(0.3)
    except KeyboardInterrupt:
        log("stopping …")
        sess.stop()


if __name__ == "__main__":
    main()
