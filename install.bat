@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   ETS2 AI Copilot - One-Click Installer
echo ============================================================
echo.

REM ============================================================
REM Step 1: Detect Python
REM ============================================================
set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY (
    where py >nul 2>&1 && set "PY=py"
)
if not defined PY (
    echo [ERROR] Python 3 not found.
    echo.
    echo Please install Python 3.11 from:
    echo   https://www.python.org/downloads/
    echo.
    echo IMPORTANT: during install, check "Add Python to PATH".
    echo Then run this installer again.
    echo.
    pause
    exit /b 1
)

echo [1/5] Python found. Checking version...
%PY% --version
if errorlevel 1 (
    echo [ERROR] Failed to run Python. Please reinstall Python.
    pause
    exit /b 1
)
echo.

REM ============================================================
REM Step 2: Install Python dependencies (Tsinghua mirror)
REM ============================================================
echo [2/5] Installing Python packages. This may take a few minutes...
%PY% -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
%PY% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo.
    echo [ERROR] Package installation failed.
    echo Please check your internet connection, then run this installer again.
    pause
    exit /b 1
)
echo.

REM ============================================================
REM Step 3: Detect NVIDIA GPU, install CUDA libs if needed
REM ============================================================
echo [3/5] Checking for NVIDIA GPU...
%PY% -c "import sys; from ctranslate2 import get_cuda_device_count; sys.exit(0 if get_cuda_device_count() else 1)" >nul 2>&1
if errorlevel 1 (
    echo       No usable NVIDIA GPU detected. CPU mode is fine.
) else (
    echo       NVIDIA GPU detected. Installing CUDA libraries...
    %PY% -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 -i https://pypi.tuna.tsinghua.edu.cn/simple
)
echo.

REM ============================================================
REM Step 4: Download local models (ModelScope mirror)
REM ============================================================
echo [4/5] Downloading local models. About 340MB, one time only...
%PY% download_models.py
if errorlevel 1 (
    echo.
    echo [WARN] Some model files failed to download.
    echo You can retry later by running:  %PY% download_models.py
)
echo.

REM ============================================================
REM Step 5: Prepare config file (.env)
REM ============================================================
echo [5/5] Preparing config file...
if not exist .env (
    copy .env.example .env >nul
    echo       Created .env from template.
) else (
    echo       .env already exists. Keeping it unchanged.
)
echo.

echo ============================================================
echo   Install finished!
echo.
echo   NEXT STEPS:
echo     1. Open .env and set your DEEPSEEK_API_KEY
echo        (get one at https://platform.deepseek.com)
echo     2. Run start.bat to launch the copilot
echo.
echo   Optional:
echo     - fix_pip_path.bat   : make the "pip" command work in CMD
echo     - fix_cuda_path.bat  : fix cublas64_12.dll error after reboot
echo ============================================================
echo.
pause
