"""
Live lecture transcriber.

Captures whatever is playing on your speakers (Zoom / Meet / browser / anything),
transcribes it in near real time with faster-whisper on the GPU, and appends
timestamped lines to a text file you can keep open and scroll back through.

Usage:
    python transcribe.py                  # transcribe the default speakers until Ctrl+C
    python transcribe.py --list-devices   # show capture devices and exit
    python transcribe.py --language uk    # force Ukrainian (default: auto-detect)
    python transcribe.py --model medium   # smaller / faster model
    python transcribe.py --duration 90    # stop automatically after 90 minutes
    python transcribe.py --prompt "..."   # hint rare terms (topic, names, jargon)

Stop anytime with Ctrl+C. The transcript is flushed to disk continuously.
"""

import argparse
import datetime as dt
import glob
import os
import queue
import site
import sys
import threading
import time


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


def log(msg: str) -> None:
    print(f"{dt.datetime.now():%H:%M:%S}  {msg}", file=sys.stderr, flush=True)


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
    raise SystemExit("Could not load any Whisper model.")


class Transcriber(threading.Thread):
    """Pulls (spoken_at, int16 samples) segments off a queue, transcribes them on
    the GPU, and appends timestamped lines. Runs independently so recording and
    segmentation never stall while the model is busy."""

    def __init__(self, model, args, src_rate, channels, fout):
        super().__init__(daemon=True)
        self.model = model
        self.args = args
        self.src_rate = src_rate
        self.channels = channels
        self.fout = fout
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
        line = f"[{spoken_at:%H:%M:%S}] {text}"
        print(line, flush=True)
        self.fout.write(line + "\n")
        self.fout.flush()


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


def main() -> None:
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
                    help="output file (default: lecture_YYYY-MM-DD.txt next to this script)")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="stop automatically after this many minutes (0 = run until Ctrl+C)")
    ap.add_argument("--min-silence", type=float, default=0.6,
                    help="seconds of quiet that closes a segment (default: 0.6)")
    ap.add_argument("--max-segment", type=float, default=18.0,
                    help="force-flush a segment after this many seconds (default: 18)")
    ap.add_argument("--no-keepalive", action="store_true",
                    help="do not play silent keep-alive audio to the output device")
    args = ap.parse_args()

    pa = pyaudio.PyAudio()
    keepalive = None
    rec = None
    worker = None
    fout = None
    try:
        if args.list_devices:
            list_devices(pa)
            return

        dev, render = pick_loopback(pa, args.device_index)
        raw_q: "queue.Queue[bytes]" = queue.Queue()
        rec = Recorder(pa, dev, raw_q)

        outfile = args.outfile or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"lecture_{dt.date.today():%Y-%m-%d}.txt",
        )
        model = load_model(args.model, args.compute_type)

        src_rate = rec.rate
        channels = rec.channels
        min_sil_frames = int(args.min_silence * 1000 / FRAME_MS)
        max_seg_samples = int(args.max_segment * src_rate) * channels
        min_seg_samples = int(1.0 * src_rate) * channels
        keep_tail = int(0.25 * src_rate) * channels  # carry-over so cut words survive

        pending = np.empty(0, dtype=np.int16)
        quiet_run = 0
        deadline = time.monotonic() + args.duration * 60 if args.duration else None

        fout = open(outfile, "a", encoding="utf-8")
        fout.write(f"\n===== session started {dt.datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
        fout.flush()
        log(f"writing transcript -> {outfile}")

        worker = Transcriber(model, args, src_rate, channels, fout)
        worker.start()

        if not args.no_keepalive:
            keepalive = KeepAlive(pa, render)
            keepalive.start()

        rec.start()
        log("listening. press Ctrl+C to stop.")
        started_at = time.monotonic()
        warned_silent = False

        try:
            while True:
                try:
                    data = raw_q.get(timeout=1.0)
                except queue.Empty:
                    if (not warned_silent and rec.bytes_seen == 0
                            and time.monotonic() - started_at > 8):
                        warned_silent = True
                        log(f"WARNING: no audio from '{dev['name']}' yet. "
                            f"Check that this is your active Windows output device, "
                            f"or restart with --list-devices / --device-index N.")
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
        except KeyboardInterrupt:
            log("stopping ...")

        rec.stop()
        if keepalive:
            keepalive.stop()
        time.sleep(0.3)
        worker.submit(dt.datetime.now(), pending)
        worker.in_q.put(None)
        worker.join(timeout=30)
        fout.write(f"===== session ended {dt.datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
        log(f"done. transcript saved to {outfile}")
    finally:
        if fout and not fout.closed:
            fout.close()
        pa.terminate()


if __name__ == "__main__":
    main()
