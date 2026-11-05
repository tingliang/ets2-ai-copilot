# -*- coding: utf-8 -*-
"""
CosyVoice 2 语音合成子进程服务。

为什么独立子进程：
- CosyVoice2 依赖重（torch + transformers 锁版本 + pynini/WeTextProcessing），
  与主程序的 faster-whisper / sherpa-onnx 会冲突，必须隔离环境。
- 模型加载要几秒~十几秒，必须常驻，不能每次说话重新初始化。
- 主程序通过 HTTP 调用；本服务起不来时主程序自动回退 sherpa 引擎，不卡死。

对外接口（HTTP，默认 127.0.0.1:9860）：
  GET  /health         -> {"ok": true, "engine": "cosyvoice2"}
  POST /tts            -> JSON body {"text": "...", "emotion": "happy", "speed": 1.0}
                          返回 WAV 字节（audio/wav），单声道 24kHz float→16bit PCM。

情绪 → CosyVoice 自然语言指令（与主程序 parse_emotion 的 key 对应）：
  happy/surprised/sad/angry/calm -> 对应的「语气」指令，由模型自身演绎。

运行方式（由 setup_cosyvoice.py 装好独立 venv 后）：
  venv_cosyvoice/Scripts/python.exe cosyvoice_server.py
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import wave

# 情绪 -> CosyVoice instruct 提示词（自然语言控制语气）
EMOTION_INSTRUCT = {
    "happy": "用开心、轻快、上扬的语气说这句话",
    "surprised": "用惊讶、好奇、语气上扬地说这句话",
    "sad": "用温柔、略带低落、放慢语速的语气说这句话",
    "angry": "用有点生气、不耐烦、语气急促地说这句话",
    "calm": "用平静、自然、日常聊天的语气说这句话",
    "idle": "用平静、自然、日常聊天的语气说这句话",
    None: None,
}


def _load_engine(model_dir: str, device: str):
    """Load the CosyVoice2 model (first call, takes seconds to tens of seconds)."""
    # The `cosyvoice` package lives in the source repo (models/CosyVoice), NOT in the
    # model dir. Add the repo root + its third_party/Matcha-TTS submodule to sys.path.
    repo_dir = os.environ.get(
        "COSYVOICE_REPO_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "CosyVoice"),
    )
    for p in (repo_dir, os.path.join(repo_dir, "third_party", "Matcha-TTS")):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)

    from cosyvoice.cli.cosyvoice import CosyVoice2

    if device == "cuda":
        # Use fp16 on NVIDIA GPU (5080's 16GB is more than enough).
        try:
            return CosyVoice2(model_dir, load_jit=False, load_trt=False, fp16=True)
        except Exception as e:
            print(f"[cosyvoice] CUDA fp16 load failed ({e}); falling back to CPU")
    return CosyVoice2(model_dir, load_jit=False, load_trt=False, fp16=False)


def _to_wav(pcm, sample_rate: int) -> bytes:
    """float32 PCM -> 16bit WAV 字节。"""
    import numpy as np
    pcm = np.asarray(pcm, dtype=np.float32)
    pcm = np.clip(pcm, -1.0, 1.0)
    int16 = (pcm * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(int16.tobytes())
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9860)
    ap.add_argument("--model-dir", default="", help="CosyVoice2 模型目录")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    args = ap.parse_args()

    model_dir = args.model_dir or os.environ.get(
        "COSYVOICE_MODEL_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "CosyVoice2-0.5B"),
    )

    # 设备自动探测
    device = args.device
    if device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"

    print(f"[cosyvoice] 加载模型 {model_dir}（设备 {device}）…", flush=True)
    engine = _load_engine(model_dir, device)
    sample_rate = engine.sample_rate  # CosyVoice 默认 24k
    print(f"[cosyvoice] 模型就绪，采样率 {sample_rate}，监听 {args.host}:{args.port}", flush=True)

    # 惰性 import，避免 torch 等重依赖在启动前就加载
    from flask import Flask, request, jsonify, send_file

    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "engine": "cosyvoice2", "sample_rate": sample_rate})

    @app.post("/tts")
    def tts():
        data = request.get_json(force=True)
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "empty text"}), 400
        emotion = data.get("emotion") or None
        speed = float(data.get("speed") or 1.0)

        # 情绪指令：用 inference_instruct2 做自然语言情感控制（不依赖参考音频）
        instruct = EMOTION_INSTRUCT.get(emotion)
        try:
            import numpy as np
            if instruct:
                # 带情绪：inference_instruct2(text, instruct, prompt_speech=None)
                # prompt_speech=None 用预训练音色（不克隆），instruct 控制语气
                results = list(
                    engine.inference_instruct2(text, instruct, prompt_speech=None, stream=False)
                )
                pcm = np.concatenate([r["tts_speech"].numpy() for r in results]) if results else np.zeros(0, dtype=np.float32)
            else:
                # 无情绪：直接预训练音色合成（inference_sft）
                results = list(engine.inference_sft(text, stream=False, speed=speed))
                pcm = np.concatenate([r["tts_speech"].numpy() for r in results]) if results else np.zeros(0, dtype=np.float32)
        except Exception as e:
            print(f"[cosyvoice] 合成失败: {e}", flush=True)
            return jsonify({"error": str(e)}), 500

        wav = _to_wav(pcm, sample_rate)
        return send_file(io.BytesIO(wav), mimetype="audio/wav")

    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
