@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem  --auto  = called from start.bat: no model-prefetch question, no final pause
set "AUTO="
if /i "%~1"=="--auto" set "AUTO=1"

echo.
echo ============================================================
echo    Lecture transcriber  -  installing dependencies
echo ============================================================
echo.

rem ---- 1. find a Python interpreter ---------------------------
set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C --version >nul 2>&1 && set "PY=%%~C"
    )
)
if not defined PY (
    echo [X] Python was not found on this PC.
    echo.
    echo     Install Python 3.11 or newer, then run setup.bat again:
    echo       * https://www.python.org/downloads/   ^(tick "Add python.exe to PATH"^)
    echo       * or in a terminal:   winget install -e --id Python.Python.3.12
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('%PY% --version 2^>^&1') do echo [i] Using %%v   ^(%PY%^)

rem ---- 2. must be 64-bit ------------------------------------
for /f %%b in ('%PY% -c "import sys;print(64 if sys.maxsize>2**32 else 32)" 2^>nul') do set "BITS=%%b"
if not "%BITS%"=="64" (
    echo [X] This Python is 32-bit. faster-whisper / CTranslate2 need 64-bit Python.
    echo     Install the 64-bit build of Python and run setup.bat again.
    pause
    exit /b 1
)

rem ---- 3. virtual environment -----------------------------
if exist ".venv\Scripts\python.exe" (
    echo [i] .venv already exists - reusing it.
) else (
    echo [i] Creating virtual environment  .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [X] Could not create the virtual environment.
        pause
        exit /b 1
    )
)
set "VPY=.venv\Scripts\python.exe"

rem ---- 4. install packages -------------------------------
echo [i] Updating pip ...
"%VPY%" -m pip install --upgrade pip --disable-pip-version-check
echo.
echo [i] Installing everything from requirements.txt
echo     ^(~1.5 GB download incl. the CUDA runtime - several minutes^) ...
echo.
"%VPY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo [X] Install failed. Check your internet / proxy and run setup.bat again.
    echo     If it keeps failing: delete the .venv folder, then retry.
    pause
    exit /b 1
)

rem ---- 5. verify ------------------------------------------
echo.
echo [i] Verifying ...
"%VPY%" -c "import faster_whisper, pyaudiowpatch, scipy, numpy, soundfile, keyboard; print('    all core packages import OK')"
if errorlevel 1 (
    echo [X] Something is still missing. Delete .venv and run setup.bat once more.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo    Done.  Start recording with:   start.bat
echo    ^(the first start downloads the Whisper model, ~1.5 GB, once^)
echo ============================================================
echo.

if defined AUTO exit /b 0

set "PREF="
set /p PREF="Pre-download the large-v3 model now so the first lecture starts fast? [y/N] "
if /i "!PREF!"=="y" (
    echo [i] Downloading large-v3 ...
    "%VPY%" -c "from faster_whisper import WhisperModel; WhisperModel('large-v3'); print('    model cached')"
)
echo.
pause
exit /b 0
