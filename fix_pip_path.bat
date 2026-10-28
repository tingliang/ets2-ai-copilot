@echo off
rem ============================================================
rem  fix_pip_path.bat  -  Fix "pip is not recognized" permanently
rem
rem  What it does:
rem    Add Python's Scripts folder (where pip.exe lives) to the
rem    user PATH permanently, so "pip install xxx" works in any
rem    new terminal window, even after reboot.
rem
rem  Why: pip is missing because Scripts folder is not in PATH.
rem       This script auto-detects Python's Scripts folder and
rem       writes it into the user PATH (setx + PowerShell).
rem ============================================================

setlocal enabledelayedexpansion

echo ==== Fix pip command ====
echo.

rem 1. Locate python.exe
set "PY_EXE="
where python >nul 2>nul
if %errorlevel%==0 (
    for /f "delims=" %%i in ('where python') do (
        set "PY_EXE=%%i"
        goto :found
    )
)

:found
if not defined PY_EXE (
    echo [!] python not found. Please install Python and check
    echo     "Add to PATH" during installation, or run:
    echo     python -m pip install xxx
    pause
    exit /b 1
)

rem 2. Derive Scripts folder from python.exe path
for %%F in ("%PY_EXE%") do set "PY_DIR=%%~dpF"
set "SCRIPTS_DIR=%PY_DIR%Scripts"

echo Python executable: %PY_EXE%
echo Scripts folder:    %SCRIPTS_DIR%
echo.

rem 3. Verify Scripts folder and pip.exe exist
if not exist "%SCRIPTS_DIR%\pip.exe" (
    echo [!] pip.exe not found in %SCRIPTS_DIR%
    echo     Please check your Python installation.
    pause
    exit /b 1
)

rem 4. Check whether the folder is already in PATH
set "ALREADY="
echo %PATH% | find /i "%SCRIPTS_DIR%" >nul 2>nul && set "ALREADY=1"
if defined ALREADY (
    echo [OK] Scripts folder is already in PATH.
    echo.
    echo If pip still does not work, close and reopen the terminal,
    echo or reboot, then try again.
    pause
    exit /b 0
)

rem 5. Permanently write into user PATH (dedup, no overwrite)
powershell -NoProfile -Command ^
  "$cur = [Environment]::GetEnvironmentVariable('Path','User');" ^
  "$target = '%SCRIPTS_DIR%';" ^
  "$parts = @($cur -split ';' | Where-Object { $_ -and ($_ -ne $target) });" ^
  "$parts += $target;" ^
  "$new = ($parts -join ';');" ^
  "[Environment]::SetEnvironmentVariable('Path', $new, 'User');" ^
  "Write-Host ('[OK] Added to user PATH: ' + $target)"

if errorlevel 1 (
    echo [!] Failed to write PATH. Please add this folder manually:
    echo     %SCRIPTS_DIR%
    pause
    exit /b 1
)

echo.
echo ==== Done ====
echo Scripts folder added to user PATH permanently.
echo.
echo NOTE: already-open terminals will NOT inherit the new PATH.
echo Close and reopen a terminal, or reboot, then verify:
echo.
echo     pip --version
echo.
pause
