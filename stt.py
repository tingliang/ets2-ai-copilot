"""
本地语音识别（STT）：faster-whisper，中文。

- 模型默认 base（9800X3D 跑得飞快），可用 .env 的 WHISPER_MODEL 调整
  （tiny / base / small / medium / large-v3，越大越准越慢）。
- 输入是 float32 / int16 的 PCM 音频（16kHz 单声道），来自 PTT 录音。
"""
from __future__ import annotations

import os

# 国内网络友好：默认走 HF 镜像下载模型（已被设置则不覆盖）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import numpy as np

import config

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        device = getattr(config, "WHISPER_DEVICE", "cpu") or "cpu"
        compute = "int8" if device == "cpu" else "float16"
        # CPU 用 int8 加速；GPU 需装 CUDA 运行时，否则会报 cublas 缺失
        _model = WhisperModel(config.WHISPER_MODEL, device=device, compute_type=compute)
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
