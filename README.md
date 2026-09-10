# Live lecture transcriber

Records everything coming out of your speakers (Zoom / Meet / browser / any app),
transcribes it on your RTX 3050 with Whisper `large-v3`, and writes timestamped
lines you can scroll back through whenever you zone out.

**Every run gets its own folder** under `Записи\`:

    Записи\
      Запис 10.09.2026\
        Запис 10.09.2026.txt      <- the transcript
        Запис 10.09.2026.ogg      <- the lecture audio, same timestamps
      Запис 10.09.2026 (2)\        <- a second run the same day
        ...

So one lecture = one folder with the text and the sound together, nothing loose
to hunt for. Keep the `.txt` open in an editor that auto-reloads (VS Code,
Notepad++).

- The **`.ogg`** is a compact (~13 MB/hour) copy of the lecture audio. When
  Whisper garbles a term or formula, open it and jump to the timestamp from the
  transcript to hear what was actually said. Turn off with `--no-audio`.

And while it runs:

- **Hotkeys** drop a marker into the transcript at the moment you press them, so
  the spots you flagged are easy to find later:
  `Ctrl+Alt+M` -> `⭐  ВАЖЛИВО  ⭐`, `Ctrl+Alt+K` -> `❓  НЕ ЗРОЗУМІВ  ❓`.
  They work even when the console is minimised. Change with `--mark-key` /
  `--confused-key` (pass `""` to disable one).
- If the capture goes silent mid-lecture (headset disconnected and Windows moved
  the default output), you get a **Windows notification** within ~20 s and a
  `⚠️` line in the transcript; a `✅` line when audio comes back. Tune with
  `--silence-alert SECONDS` (0 = off), `--no-toast` = console/transcript only.

## First run (new PC)

1. Install **Python 3.11+** (64-bit) - tick *"Add python.exe to PATH"* in the
   installer, or run `winget install -e --id Python.Python.3.12`.
2. Copy this folder anywhere and double-click **`Транскрипція.vbs`**.

That's it. The window opens, checks its components, and if anything is missing
it shows a **"Встановити / полагодити"** button that installs everything
(~1.5 GB, a few minutes) and re-checks. An NVIDIA GPU is optional; without one
it runs on CPU (slower). `setup.bat` still exists if you prefer to run it by
hand.

## The window

Double-click **`Транскрипція.vbs`** - a normal window, no console among your
other terminals.

**Start panel.** Pick the **language** (Українська / English - you switch it
yourself when a lecture is in the other one), toggle audio copy / silence alerts
/ marker hotkeys, then press **▶ Почати**. It always runs the best model
(`large-v3`). Your choices are remembered for next time. *Додатково* hides the
output-device picker and an auto-stop timer.

For a one-off rare-words hint or a lighter model, launch from
`gui.bat --prompt "..." --model medium` instead.

**While recording.**

- The transcript fills in live.
- Click the **☆** in the left margin of a line to flag it **★ important** - the
  line is highlighted and flagged lines are also written to a sidecar file
  `Запис ....важливо.txt` next to the transcript.
- Right-click a line: mark / unmark, or copy just that line. `Ctrl+M` flags the
  last line.
- Menu **Правка**: select all (Ctrl+A), copy selection, **copy the whole
  transcript**, **copy only the important lines**.
- Menu **Файл**: open the recording folder / the `.ogg`, save the transcript
  (or just the important lines) elsewhere.
- Status bar: device, elapsed, line and mark counts; turns red (and a red line
  appears) if the sound drops out.
- **⏹ Зупинити** stops the recording, saves the files, and returns to the start
  panel so you can begin another lecture.

To launch straight past the panel with fixed options:
**`gui.bat --language uk --prompt "..."`** (its console closes itself).

## Console version / start-stop

- Double-click **`start.bat`** (or run it from a terminal) for the plain
  console tool - same engine, no window.
- Older desktop shortcuts **"Транскрипція лекції (укр)"** / **"(англ)"** still
  point at the console tool; repoint them to `Транскрипція.vbs` for the window.
- Stop with **Ctrl+C** (or just close the window). The transcript is saved
  continuously, so nothing is lost if it crashes.
- Each run gets its own folder `Записи\Запис DD.MM.YYYY\` with the transcript and
  audio inside. Sit through a lecture, close the transcriber, and that folder is
  your record of it.

## Options

The same flags work for the window: `gui.bat --language uk ...`

    start.bat --language uk        force Ukrainian (or: en; default: auto-detect)
    start.bat --prompt "тема: перетворення Фур'є, лектор Іваненко, GMRES, SVD"
                                  hint rare words / names / acronyms -> better accuracy
    start.bat --model medium      lighter model (use if the GPU is busy with a game)
    start.bat --compute-type int8_float16   faster + less VRAM (default: float16)
    start.bat --beam-size 3       faster, a little less accurate (default: 5)
    start.bat --duration 90       stop automatically after 90 minutes
    start.bat --max-segment 10    shorter -> lower latency, longer -> more context
    start.bat --min-silence 0.4   pause length that ends a line
    start.bat --list-devices      list capture devices
    start.bat --device-index 19   capture a specific device
    start.bat --no-audio          transcript only, skip the .ogg audio copy
    start.bat --mark-key ctrl+alt+space   rebind the "important" marker hotkey
    start.bat --silence-alert 30  seconds of no audio before it warns you (0 = off)
    start.bat --no-toast          silence warning in console/transcript only

## Speed / quality balance (current defaults)

Tuned for quality first, with the latency win kept:

- model `large-v3`, compute `float16`, `beam-size 5` (~4.5 GB VRAM)
- transcription runs in a separate thread, so recording never stalls while the GPU works
- the previous line is fed back as context, so terminology stays consistent
- a line is emitted after **0.6 s** of silence, or every **18 s** during
  non-stop speech (cut at the quietest point so no word is lost)

Result: for normal lecture speech (pauses between sentences) text appears
**~1-2 s** after it is spoken. During an uninterrupted monologue you wait up to
~18 s for that stretch - lower `--max-segment` if that bothers you.

Faster, lower quality: `--compute-type int8_float16 --beam-size 1 --max-segment 10`.
For acronyms / names, always pass `--prompt "..."` - it helps more than any setting.

## Notes

- First run downloads the model (~1.5 GB for `large-v3`) once; then it loads in
  a few seconds.
- A silent keep-alive tone is played to your output device so capture keeps
  working during pauses. It is inaudible. Disable with `--no-keepalive`.
- The `.ogg` audio copy needs `soundfile`; the marker hotkeys need `keyboard`
  (both in `requirements.txt`). If either package is missing that feature just
  logs a line and switches itself off - the transcript still works.
- The silence notification is a plain Windows toast raised via PowerShell; no
  extra package needed.
- The window (`gui.py`) uses `tkinter`, which ships with Python - nothing to
  install. `transcribe.py` is the shared engine; the window and the console are
  just two front-ends for it.
- Everything runs locally - no audio is sent anywhere.

## If it picks the wrong audio device / no text appears

If your headphones disconnect, Windows can switch the default output to another
device and the tool then captures silence. You'll get a Windows notification and
a `⚠️` line in the transcript (see `--silence-alert`). Fix: run
`start.bat --list-devices`, find the `[Loopback]` entry for the output you
actually listen through, and pass its number:
`start.bat --language uk --device-index N`.
