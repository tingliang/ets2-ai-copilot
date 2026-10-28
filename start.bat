@echo off
REM ============================================
REM  ETS2 AI Copilot - one-click launcher
REM  Double-click this file to start (install deps first, see README)
REM ============================================

cd /d "%~dp0"

REM Detect Python (python first, then py launcher)
set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY (
    where py >nul 2>&1 && set "PY=py"
)
if not defined PY (
    echo.
    echo   [!] Python not found.
    echo   [!] Please run install.bat first, or install Python 3.11.
    echo.
    pause
    exit /b 1
)

echo.
echo   ==========================================
echo     ETS2 AI Copilot starting...
echo   ==========================================
echo.

REM Check if .env exists (do NOT create or overwrite it)
if not exist ".env" (
    echo   [!] .env not found
    echo   [!] Please run install.bat, or copy .env.example to .env
    echo        and fill in your DeepSeek Key.
    echo.
    pause
    exit /b 1
)

REM Run the main program
%PY% main.py

echo.
echo   Program exited.
pause
