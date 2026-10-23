@echo off
chcp 65001 >nul
REM ============================================
REM  ETS2 AI 副驾 - 一键启动
REM  双击本文件即可启动小满（需先按 README 装好依赖）
REM ============================================

cd /d "%~dp0"

echo.
echo   ==========================================
echo     ETS2 AI 副驾（小满）启动中...
echo   ==========================================
echo.

REM 检查 .env 是否已配置
if not exist ".env" (
    echo   [!] 未找到 .env 配置文件
    echo   [!] 请先复制 .env.example 为 .env 并填入 DeepSeek Key
    echo        copy .env.example .env
    echo.
    pause
    exit /b 1
)

REM 运行主程序
python main.py

echo.
echo   程序已退出。
pause
