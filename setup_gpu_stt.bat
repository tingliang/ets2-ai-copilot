@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================
echo  ETS2 副驾 - STT 上 GPU 一键配置（RTX 50系）
echo ============================================
echo.

:: —— 检测可用的 pip 命令 ——
:: 优先用 python -m pip（最通用，避免 PATH 里 pip 缺失的问题）
set "PIP_CMD="
python -m pip --version >nul 2>&1
if !errorlevel!==0 (
    set "PIP_CMD=python -m pip"
    echo [检测] 使用 python -m pip
) else (
    py -m pip --version >nul 2>&1
    if !errorlevel!==0 (
        set "PIP_CMD=py -m pip"
        echo [检测] 使用 py -m pip
    ) else (
        pip --version >nul 2>&1
        if !errorlevel!==0 (
            set "PIP_CMD=pip"
            echo [检测] 使用 pip
        ) else (
            echo [!] 找不到可用的 pip 命令！
            echo     请确认 Python 已安装，或手动执行：
            echo     python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
            pause
            exit /b 1
        )
    )
)
echo.

echo [1/3] 安装 faster-whisper 的 CUDA 加速依赖...
echo         （nvidia-cublas-cu12 + nvidia-cudnn-cu12，自动带 CUDA 运行时）
!PIP_CMD! install nvidia-cublas-cu12 nvidia-cudnn-cu12
if errorlevel 1 (
    echo.
    echo [!] 依赖安装失败，请检查网络后重试，或手动执行：
    echo     !PIP_CMD! install nvidia-cublas-cu12 nvidia-cudnn-cu12
    pause
    exit /b 1
)

echo.
echo [2/3] 升级 faster-whisper 到支持 50 系显卡的版本...
!PIP_CMD! install --upgrade faster-whisper
if errorlevel 1 (
    echo [!] 升级失败，可忽略继续，但建议升级到最新版
)

echo.
echo [3/3] 修改 .env 配置，开启 GPU 识别...
python -c "import re; p='.env'; s=open(p,encoding='utf-8').read(); s2=re.sub(r'(?m)^WHISPER_DEVICE=.*$','WHISPER_DEVICE=cuda',s); open(p,'w',encoding='utf-8').write(s2 if 'WHISPER_DEVICE' in s else s+'\nWHISPER_DEVICE=cuda\n'); print('已写入 WHISPER_DEVICE=cuda')"

echo.
echo ============================================
echo  完成！现在运行 python main.py 测试
echo  启动时会打印 [stt] 已加载 xxx 模型（cuda/float16）
echo  如果打印（cpu/int8）说明 GPU 没生效，会报回退原因
echo ============================================
echo.
pause
