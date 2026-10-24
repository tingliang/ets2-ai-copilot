@echo off
REM ============================================
REM  ETS2 AI Copilot - one-click launcher
REM  Double-click this file to start (install deps first, see README)
REM ============================================

cd /d "%~dp0"

echo.
echo   ==========================================
echo     ETS2 AI Copilot starting...
echo   ==========================================
echo.

REM Check if .env exists (do NOT create or overwrite it)
if not exist ".env" (
    echo   [!] .env not found
    echo   [!] Please copy .env.example to .env and fill in your DeepSeek Key:
    echo        copy .env.example .env
    echo.
    pause
    exit /b 1
)

REM Run the main program
python main.py

echo.
echo   Program exited.
pause
