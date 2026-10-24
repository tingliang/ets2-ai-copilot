"""
本地语音识别（STT）：faster-whisper，中文。

- 模型默认 base（9800X3D 跑得飞快），可用 .env 的 WHISPER_MODEL 调整
  （tiny / base / small / medium / large-v3，越大越准越慢）。
- 输入是 float32 / int16 的 PCM 音频（16kHz 单声道），来自 PTT 录音。
"""
from __future__ import annotations

import os
import sys

import numpy as np

import config

_model = None


def _setup_cuda_dll_path():
    """Windows 上：把 pip 安装的 nvidia CUDA 库的 bin 目录加进 DLL 搜索路径。

    ctranslate2 有时找不到 pip 包里的 cublas64_12.dll / cudnn 等，需要手动
    用 os.add_dll_directory() 把这些目录加进去。这里自动扫描 site-packages
    下所有 nvidia 包的 bin 目录，只要里面含 cuda 相关的 dll 就注册。
    """
    if sys.platform != "win32":
        return
    try:
        import site
        added = []
        for base in site.getsitepackages():
            nvidia_root = os.path.join(base, "nvidia")
            if not os.path.isdir(nvidia_root):
                continue
            # 遍历 nvidia/cublas/bin、nvidia/cudnn/bin 等子目录
            for entry in os.listdir(nvidia_root):
                bin_dir = os.path.join(nvidia_root, entry, "bin")
                if os.path.isdir(bin_dir):
                    # 只注册含 cuda 相关 dll 的目录
                    has_dll = any(
                        f.lower().startswith(("cublas", "cudnn", "cudart",
                                              "cufft", "curand", "cusparse",
                                              "nvrtc", "cublaslt"))
                        and f.lower().endswith(".dll")
                        for f in os.listdir(bin_dir)
                    )
                    if has_dll and bin_dir not in added:
                        try:
                            os.add_dll_directory(bin_dir)
                            added.append(bin_dir)
                        except Exception:
                            pass
        if added:
            print(f"[stt] 已注册 CUDA DLL 目录（{len(added)} 个）")
    except Exception as e:
        # 失败不影响主流程，只是 GPU 可能加载不了，会回退 CPU
        print(f"[stt] 注册 CUDA DLL 目录失败（忽略）: {e}")


def _get_model():
    global _model
    if _model is None:
        device = getattr(config, "WHISPER_DEVICE", "cpu") or "cpu"
        # GPU 必须用 float16：RTX 50 系（Blackwell/sm_120）的 INT8 会报
        # CUBLAS_STATUS_NOT_SUPPORTED。CPU 用 int8 加速。
        compute = "float16" if device == "cuda" else "int8"
        # 关键：必须在 import faster_whisper（会触发 ctranslate2 加载 cublas）
        # 之前注册 CUDA DLL 搜索路径，否则报 cublas64_12.dll not found
        if device == "cuda":
            _setup_cuda_dll_path()
        from faster_whisper import WhisperModel
        try:
            _model = WhisperModel(
                config.WHISPER_MODEL, device=device, compute_type=compute)
            print(f"[stt] 已加载 {config.WHISPER_MODEL} 模型（{device}/{compute}）")
        except Exception as e:
            # GPU 加载失败（缺 CUDA 库/驱动不支持等）→ 自动回退 CPU，不崩溃
            print(f"[stt] GPU 加载失败（{e}），回退 CPU/int8")
            _model = WhisperModel(
                config.WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


def transcribe(audio: np.ndarray, sample_rate: int = 16000) -> str:
    """把一段 PCM 音频转成中文文本。audio: float32 [-1,1] 或 int16。"""
    if audio.dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0
    if sample_rate != 16000:
        # 简单线性重采样（faster-whisper 要 16k）
        n = int(len(audio) * 16000 / sample_rate)
        audio = np.interp(
            np.linspace(0, len(audio) - 1, n),
            np.arange(len(audio)),
            audio,
        ).astype(np.float32)

    model = _get_model()
    segments, _info = model.transcribe(audio, language="zh", vad_filter=True)
    return "".join(s.text for s in segments).strip()
