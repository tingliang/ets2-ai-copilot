@echo off
chcp 65001 >nul
REM ============================================
REM  取消开机自启
REM ============================================

set "TASK_NAME=ETS2-AI-Copilot"

echo.
echo   正在取消开机自启...

schtasks /Delete /F /TN "%TASK_NAME%" >nul 2>&1

if %errorlevel%==0 (
    echo   [OK] 已取消开机自启
) else (
    echo   [!] 未找到自启任务（可能本来就没设置）
)

echo.
pause
