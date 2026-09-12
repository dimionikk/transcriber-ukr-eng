# Live lecture transcriber

Records whatever's coming out of your speakers (Zoom / Meet / browser) and
transcribes it live with Whisper, so you can scroll back whenever you zone out.

## First run

1. Install **Python 3.11+** (64-bit), ticking "Add python.exe to PATH".
2. Double-click **`Транскрипція.vbs`**.

It checks its components and offers to install anything missing (~1.5 GB,
one-time). An NVIDIA GPU is optional; without one it runs on CPU (slower).

## Using it

Double-click **`Транскрипція.vbs`** to open the window:

1. Pick the language (Українська / English) and press **▶ Почати**.
2. The transcript fills in live as you go.
3. **⏹ Зупинити** stops and saves it.
4. **📋 Скопіювати транскрипт** copies everything shown to the clipboard.

Each run is saved to its own folder:

    Записи\Запис DD.MM.YYYY\Запис DD.MM.YYYY.txt

While recording:

- `Ctrl+Alt+M` drops a "⭐ ВАЖЛИВО" marker, `Ctrl+Alt+K` drops "❓ НЕ ЗРОЗУМІВ" —
  handy for flagging spots to revisit later.
- If the audio drops out (e.g. headset disconnects), you get a Windows
  notification and a `⚠️` line in the transcript.

## Tips

- Got acronyms, names, or a specific topic? Launch instead with
  `gui.bat --prompt "topic, names, acronyms..."` — it noticeably improves
  accuracy on unusual words.
- Nothing appearing? Run `start.bat --list-devices`, find the right
  `[Loopback]` device, then use `gui.bat --device-index N`.
- Everything runs locally — no audio ever leaves your machine.

For the full list of options, run `start.bat --help`.
