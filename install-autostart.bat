@echo off
chcp 65001 >nul
REM ============================================
REM  设置开机自启（可选）
REM  运行本脚本后，Windows 登录时自动启动小满
REM  取消自启：运行 remove-autostart.bat
REM ============================================

cd /d "%~dp0"

set "TASK_NAME=ETS2-AI-Copilot"

echo.
echo   正在设置开机自启...
echo   任务名: %TASK_NAME%
echo.

REM 用 schtasks 创建登录时触发的计划任务（静默运行，不弹黑框）
schtasks /Create /F /TN "%TASK_NAME%" /TR "\"%~dp0start.bat\"" /SC ONLOGON /RL LIMITED >nul 2>&1

if %errorlevel%==0 (
    echo   [OK] 开机自启已设置成功
    echo   下次开机登录后，小满会自动启动
) else (
    echo   [!] 设置失败，可能需要管理员权限
    echo   请右键本文件「以管理员身份运行」
)

echo.
pause
