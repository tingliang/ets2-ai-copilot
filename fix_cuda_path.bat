@echo off
REM ============================================
REM  ETS2 AI Copilot - permanent CUDA DLL PATH fix
REM  Calls fix_cuda_path.ps1 to add the CUDA DLL
REM  folders to the USER Path permanently.
REM ============================================

cd /d "%~dp0"

echo.
echo   ==========================================
echo     Fixing CUDA DLL PATH (permanent)...
echo   ==========================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fix_cuda_path.ps1"

echo.
echo   ==========================================
echo     Finished. Next steps:
echo       1. Close this window.
echo       2. Open a NEW PowerShell window.
echo       3. Run:  python main.py --debug-timing
echo   ==========================================
echo.
pause
