@echo off
REM Live lecture transcriber - double-click to start, close the window or press Ctrl+C to stop.
REM Pass extra options through, e.g.:  start.bat --language uk
cd /d "%~dp0"
".venv\Scripts\python.exe" -u transcribe.py %*
echo.
echo ==== stopped. transcript is the lecture_YYYY-MM-DD.txt file in this folder ====
pause
