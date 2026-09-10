@echo off
REM Open the transcriber window, passing options through, e.g.:
REM   gui.bat --language uk --prompt "тема, лектор, GMRES"
REM The console window this opens closes itself right after launch.
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "VPY=.venv\Scripts\python.exe"
set "VPYW=.venv\Scripts\pythonw.exe"

set "NEEDSETUP="
if not exist "%VPY%" set "NEEDSETUP=1"
if not defined NEEDSETUP (
    "%VPY%" -c "import faster_whisper, pyaudiowpatch, soundfile" >nul 2>&1 || set "NEEDSETUP=1"
)
if defined NEEDSETUP (
    echo Dependencies are not installed yet.
    set "OK="
    set /p OK="Install everything now? [Y/n] "
    if /i "!OK!"=="n" ( echo Run setup.bat when ready. & pause & exit /b 1 )
    call "%~dp0setup.bat" --auto
    if errorlevel 1 ( echo Setup did not finish. & pause & exit /b 1 )
)

start "" "%VPYW%" "%~dp0gui.py" %*
