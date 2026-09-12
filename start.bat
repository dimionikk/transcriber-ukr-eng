@echo off
setlocal enabledelayedexpansion
REM Live lecture transcriber - double-click to start, close the window or press Ctrl+C to stop.
REM Pass extra options through, e.g.:  start.bat --language uk
cd /d "%~dp0"

set "VPY=.venv\Scripts\python.exe"

REM --- first run / fresh PC: install dependencies before starting ---
set "NEEDSETUP="
if not exist "%VPY%" set "NEEDSETUP=1"
if not defined NEEDSETUP (
    "%VPY%" -c "import faster_whisper, pyaudiowpatch, keyboard" >nul 2>&1 || set "NEEDSETUP=1"
)
if defined NEEDSETUP (
    echo Dependencies for the lecture transcriber are not installed yet.
    set "OK="
    set /p OK="Install everything now? [Y/n] "
    if /i "!OK!"=="n" (
        echo Cannot start without the dependencies. Run setup.bat when ready.
        pause
        exit /b 1
    )
    call "%~dp0setup.bat" --auto
    if errorlevel 1 (
        echo Setup did not finish - see the messages above.
        pause
        exit /b 1
    )
)

"%VPY%" -u transcribe.py %*
echo.
echo ==== stopped. this run's transcript is under "Zapysy\Zapys DD.MM.YYYY" ====
pause
