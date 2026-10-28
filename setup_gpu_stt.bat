@echo off
setlocal enabledelayedexpansion

echo ============================================
echo  ETS2 Copilot - GPU STT setup (RTX 50 series)
echo ============================================
echo.

rem -- Detect an available pip command --
rem Prefer "python -m pip" (most reliable, avoids missing pip in PATH)
set "PIP_CMD="
python -m pip --version >nul 2>&1
if %errorlevel%==0 (
    set "PIP_CMD=python -m pip"
    echo [detect] using python -m pip
) else (
    py -m pip --version >nul 2>&1
    if %errorlevel%==0 (
        set "PIP_CMD=py -m pip"
        echo [detect] using py -m pip
    ) else (
        pip --version >nul 2>&1
        if %errorlevel%==0 (
            set "PIP_CMD=pip"
            echo [detect] using pip
        ) else (
            echo [!] No usable pip command found!
            echo     Make sure Python is installed, or run manually:
            echo     python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
            pause
            exit /b 1
        )
    )
)
echo.

echo [1/3] Installing CUDA acceleration deps for faster-whisper...
echo       (nvidia-cublas-cu12 + nvidia-cudnn-cu12, bundles CUDA runtime)
%PIP_CMD% install nvidia-cublas-cu12 nvidia-cudnn-cu12
if errorlevel 1 (
    echo.
    echo [!] Install failed. Check network and retry, or run manually:
    echo     %PIP_CMD% install nvidia-cublas-cu12 nvidia-cudnn-cu12
    pause
    exit /b 1
)

echo.
echo [2/3] Upgrading faster-whisper for RTX 50 series support...
%PIP_CMD% install --upgrade faster-whisper
if errorlevel 1 (
    echo [!] Upgrade failed. You may continue, but upgrading is recommended.
)

echo.
echo [3/3] Updating .env to enable GPU recognition...
python -c "import re; p='.env'; s=open(p,encoding='utf-8').read(); s2=re.sub(r'(?m)^WHISPER_DEVICE=.*$','WHISPER_DEVICE=cuda',s); open(p,'w',encoding='utf-8').write(s2 if 'WHISPER_DEVICE' in s else s+'\nWHISPER_DEVICE=cuda\n'); print('Wrote WHISPER_DEVICE=cuda')"

echo.
echo ============================================
echo  Done! Now run: python main.py
echo  On startup it should print (cuda/float16).
echo  If it prints (cpu/int8), GPU did not take effect.
echo ============================================
echo.
pause
