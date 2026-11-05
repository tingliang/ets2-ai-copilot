# -*- coding: utf-8 -*-
"""
CosyVoice2（自然情感语音）一键安装脚本。

在项目下创建一个独立 venv（venv_cosyvoice/），安装 CosyVoice2 依赖，
并从 ModelScope 下载模型到 models/CosyVoice2-0.5B。

为什么独立 venv：CosyVoice2 依赖 torch + transformers(锁版本) + pynini/
WeTextProcessing，和主程序的 faster-whisper / sherpa-onnx 会冲突，必须隔离。

用法：
    python setup_cosyvoice.py            # 全自动：建 venv + 装依赖 + 下模型
    python setup_cosyvoice.py --venv-only      # 只建 venv + 装依赖
    python setup_cosyvoice.py --model-only     # 只下模型
    python setup_cosyvoice.py --check          # 只检查环境是否就绪

装完后在 .env 里设 TTS_ENGINE=cosyvoice 即可启用（子进程起不来自动回退 sherpa）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(BASE, "venv_cosyvoice")
MODEL_DIR = os.path.join(BASE, "models", "CosyVoice2-0.5B")

# ModelScope 仓库
MODEL_ID = "iic/CosyVoice2-0.5B"

# 依赖清单（版本锁定，CosyVoice 官方要求）
# 注意：torch 单独装（选 cuda/cpu 版本），这里列其余依赖
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"

PACKAGES = [
    "transformers==4.40.1",
    "diffusers==0.29.0",
    "librosa==0.10.2",
    "soundfile",
    "onnxruntime",
    "fastapi==0.104.1",
    "uvicorn[standard]",
    "pydantic==2.10.6",
    "hyperpyyaml==1.2.2",
    "inflect==7.3.1",
    "flask",
    "requests",
]


def _venv_python() -> str:
    """返回独立 venv 的 python 路径（Windows/Linux 自适应）。"""
    if sys.platform == "win32":
        p = os.path.join(VENV_DIR, "Scripts", "python.exe")
    else:
        p = os.path.join(VENV_DIR, "bin", "python")
    return p


def _run(cmd: list[str], desc: str) -> bool:
    print(f"\n>>> {desc}")
    print("    " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=BASE)
    return r.returncode == 0


def setup_venv() -> bool:
    """创建独立 venv 并装依赖。"""
    py = _venv_python()
    if os.path.exists(py):
        print(f"[venv] 已存在：{py}")
        return True

    print(f"[venv] 创建独立环境 {VENV_DIR} …")
    if not _run([sys.executable, "-m", "venv", VENV_DIR], "创建 venv"):
        print("[错误] venv 创建失败，请确认 Python 已安装并支持 venv 模块")
        return False

    # 升级 pip
    _run([py, "-m", "pip", "install", "--upgrade", "pip", "-i", PIP_MIRROR], "升级 pip")

    # 装 torch（先探测是否有 N 卡，决定装 cuda 版还是 cpu 版）
    print("\n>>> 检测 GPU …")
    has_cuda = False
    try:
        import ctypes
        has_cuda = ctypes.windll.kernel32.LoadLibraryW("nvcuda.dll") is not None
    except Exception:
        try:
            has_cuda = subprocess.run(
                ["nvidia-smi"], capture_output=True, timeout=5
            ).returncode == 0
        except Exception:
            has_cuda = False
    if has_cuda:
        torch_pkg = "torch"  # 默认 wheel 自带 cuda（或按需改 cu121 版）
        print("    检测到 NVIDIA GPU，安装 torch（cuda 版）")
    else:
        torch_pkg = "torch"
        print("    未检测到 GPU，安装 torch（cpu 版）")

    # 装 torch 和其余依赖
    _run([py, "-m", "pip", "install", torch_pkg, "-i", PIP_MIRROR], "安装 torch（较大，耐心等待）")
    for pkg in PACKAGES:
        if not _run([py, "-m", "pip", "install", pkg, "-i", PIP_MIRROR], f"安装 {pkg}"):
            print(f"[警告] {pkg} 安装失败，继续尝试后续包")

    # pynini（Windows 需要 conda 或预编译 wheel，这里尝试 pip，失败提示）
    print("\n>>> 安装 pynini（Windows 上可能需 conda，见文末说明）")
    if not _run([py, "-m", "pip", "install", "pynini==2.1.5", "-i", PIP_MIRROR], "安装 pynini"):
        print("[警告] pynini 安装失败。若 WeTextProcessing 报错，请用 conda 安装：")
        print("        conda install -c conda-forge pynini==2.1.5")

    _run([py, "-m", "pip", "install", "WeTextProcessing", "--no-deps", "-i", PIP_MIRROR],
         "安装 WeTextProcessing")

    print("\n[venv] 依赖安装完成 ✓")
    return True


def download_model() -> bool:
    """从 ModelScope 下载 CosyVoice2-0.5B 模型。"""
    if os.path.isdir(MODEL_DIR) and os.path.exists(os.path.join(MODEL_DIR, "cosyvoice.yaml")):
        print(f"[model] 已存在：{MODEL_DIR}")
        return True

    print(f"[model] 从 ModelScope 下载 {MODEL_ID}（约 4.5GB，请耐心等待）…")
    py = _venv_python() if os.path.exists(_venv_python()) else sys.executable
    # 用 modelscope SDK 下载；优先 venv 里的（若已装 modelscope）
    code = (
        "from modelscope import snapshot_download\n"
        f"snapshot_download('{MODEL_ID}', local_dir=r'{MODEL_DIR}')\n"
        f"print('模型已下载到 {MODEL_DIR}')\n"
    )
    # modelscope 可能没装，先尝试装
    _run([py, "-m", "pip", "install", "modelscope", "-i", PIP_MIRROR], "安装 modelscope SDK")
    r = subprocess.run([py, "-c", code], cwd=BASE)
    if r.returncode != 0:
        print("[错误] 模型下载失败，可手动从以下地址下载后解压：")
        print(f"        https://www.modelscope.cn/models/{MODEL_ID}")
        print(f"    解压到 {MODEL_DIR}")
        return False
    return True


def check() -> bool:
    """检查环境是否就绪。"""
    ok = True
    py = _venv_python()
    if not os.path.exists(py):
        print("[✗] 未找到独立 venv（venv_cosyvoice）")
        ok = False
    else:
        print(f"[✓] venv: {py}")
    if not (os.path.isdir(MODEL_DIR) and os.path.exists(os.path.join(MODEL_DIR, "cosyvoice.yaml"))):
        print(f"[✗] 模型未就绪（{MODEL_DIR}）")
        ok = False
    else:
        print(f"[✓] 模型: {MODEL_DIR}")
    if ok:
        print("\n环境就绪！在 .env 里设 TTS_ENGINE=cosyvoice 即可启用。")
    return ok


def main():
    ap = argparse.ArgumentParser(description="CosyVoice2 一键安装")
    ap.add_argument("--venv-only", action="store_true", help="只建 venv + 装依赖")
    ap.add_argument("--model-only", action="store_true", help="只下载模型")
    ap.add_argument("--check", action="store_true", help="只检查环境")
    args = ap.parse_args()

    if args.check:
        sys.exit(0 if check() else 1)
    if args.model_only:
        sys.exit(0 if download_model() else 1)
    if args.venv_only:
        sys.exit(0 if setup_venv() else 1)

    # 默认全流程
    if not setup_venv():
        sys.exit(1)
    if not download_model():
        sys.exit(1)
    print("\n============================================")
    print("  CosyVoice2 安装完成 ✓")
    print("  下一步：")
    print("    1. 编辑 .env，设 TTS_ENGINE=cosyvoice")
    print("    2. 重启 start.bat")
    print("  若启动失败会自动回退 sherpa，不影响使用。")
    print("============================================")


if __name__ == "__main__":
    main()
