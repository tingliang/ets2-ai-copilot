@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   CosyVoice2 - natural emotional TTS - one-click setup
echo   (downloads ~4.5GB model, make sure you have enough disk space)
echo ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.11 and add it to PATH first.
    pause
    exit /b 1
)

python setup_cosyvoice.py
if errorlevel 1 (
    echo.
    echo [ERROR] Setup failed, see the error messages above.
    pause
    exit /b 1
)

echo.
echo Setup complete! Edit .env and set TTS_ENGINE=cosyvoice.
echo Then restart start.bat to take effect.
pause
