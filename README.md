# Live lecture transcriber

Records everything coming out of your speakers (Zoom / Meet / browser / any app),
transcribes it on your RTX 3050 with Whisper `large-v3`, and appends timestamped
lines to `lecture_YYYY-MM-DD.txt` in this folder. Keep that file open in an editor
that auto-reloads (VS Code, Notepad++) and scroll back whenever you zone out.

## Start / stop

- Desktop shortcuts: **"Транскрипція лекції (укр)"** / **"(англ)"**.
- Or double-click **`start.bat`**, or run it from a terminal in this folder.
- Stop with **Ctrl+C** (or just close the window). The transcript is saved
  continuously, so nothing is lost if it crashes.

## Options

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
- Everything runs locally - no audio is sent anywhere.

## If it picks the wrong audio device / no text appears

If your headphones disconnect, Windows can switch the default output to another
device and the tool then captures silence. It prints a WARNING after ~8 s if it
sees no audio. Fix: run `start.bat --list-devices`, find the `[Loopback]` entry
for the output you actually listen through, and pass its number:
`start.bat --language uk --device-index N`.
