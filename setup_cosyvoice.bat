@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   CosyVoice2 - 自然情感语音 一键安装
echo   （会额外下载约 4.5GB 模型，请确保磁盘空间充足）
echo ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. 请先安装 Python 3.11 并加入 PATH。
    pause
    exit /b 1
)

python setup_cosyvoice.py
if errorlevel 1 (
    echo.
    echo [ERROR] 安装失败，请查看上方错误信息。
    pause
    exit /b 1
)

echo.
echo 安装完成！请编辑 .env，把 TTS_ENGINE 改为 cosyvoice。
echo 改完后重启 start.bat 生效。
pause
