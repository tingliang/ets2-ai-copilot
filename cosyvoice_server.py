# -*- coding: utf-8 -*-
"""
CosyVoice2 text-to-speech subprocess server.

Why a separate subprocess:
- CosyVoice2 has heavy deps (torch + pinned transformers) that conflict with the
  main program's faster-whisper / sherpa-onnx.
- Model loading takes seconds to tens of seconds and must stay resident.
- The main program calls it over HTTP; if it fails to start, the main program
  auto-falls back to the sherpa engine.

HTTP API (default 127.0.0.1:9860):
  GET  /health  -> {"ok": true, "engine": "cosyvoice2", "sample_rate": 24000}
  POST /tts     -> JSON {"text": "...", "emotion": "happy", "speed": 1.0}
                   returns WAV bytes (audio/wav), mono 24kHz float -> 16-bit PCM.

Emotion -> natural-language instruction (keys match parse_emotion in main.py):
  happy/surprised/sad/angry/calm/idle -> an instruction the model interprets.

Run (after setup_cosyvoice.py installs the venv):
  venv_cosyvoice/Scripts/python.exe cosyvoice_server.py
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import wave

# NOTE: We intentionally do NOT use CosyVoice2's inference_instruct2 for emotion
# control. Its frontend feeds the instruct text in as prompt_text, so the
# instruction itself is read aloud (e.g. "用开心的语气说" is spoken verbatim).
# Instead we use plain zero-shot and let emotion come through the LLM's wording
# and the pet's expression. See the /tts handler below.

# CosyVoice2 is a zero-shot model: it has no built-in speaker (no spk2info.pt),
# so every synthesis needs a reference audio (prompt_wav). The official repo
# ships one at <repo>/asset/zero_shot_prompt.wav, cloned together with the repo.
_PROMPT_WAV = os.path.join("asset", "zero_shot_prompt.wav")


def _resolve_repo_dir() -> str:
    """Locate the CosyVoice source repo (contains the `cosyvoice` package)."""
    env = os.environ.get("COSYVOICE_REPO_DIR")
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "CosyVoice")


def _load_engine(model_dir: str, device: str):
    """Load the CosyVoice2 model via AutoModel (auto-detects cosyvoice2.yaml)."""
    repo_dir = _resolve_repo_dir()
    # The `cosyvoice` package lives in the source repo, plus its third_party submodule.
    for p in (repo_dir, os.path.join(repo_dir, "third_party", "Matcha-TTS")):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)

    from cosyvoice.cli.cosyvoice import AutoModel

    kwargs = {"model_dir": model_dir, "load_jit": False, "load_trt": False}
    if device == "cuda":
        # fp16 halves memory and speeds up inference on NVIDIA GPUs.
        # load_jit precompiles the LLM + flow encoder (~1.5x faster on a single
        # request), recommended for single-user local use. load_trt is left off
        # (needs a TensorRT engine build + extra deps, and adds first-run latency).
        kwargs["fp16"] = True
        kwargs["load_jit"] = True
    return AutoModel(**kwargs)


def _to_wav(pcm, sample_rate: int) -> bytes:
    """float32 PCM -> 16-bit WAV bytes."""
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


def _prompt_wav_path() -> str:
    """Return an existing reference wav; None if not found."""
    repo_dir = _resolve_repo_dir()
    candidates = [
        os.path.join(repo_dir, _PROMPT_WAV),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), _PROMPT_WAV),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9860)
    ap.add_argument("--model-dir", default="", help="CosyVoice2 model dir")
    ap.add_argument("--prompt-wav", default="", help="reference audio for zero-shot/instruct")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    args = ap.parse_args()

    model_dir = args.model_dir or os.environ.get(
        "COSYVOICE_MODEL_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "CosyVoice2-0.5B"),
    )
    prompt_wav = args.prompt_wav or _prompt_wav_path()

    # Auto-detect device
    device = args.device
    if device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"

    print(f"[cosyvoice] loading model {model_dir} (device {device}) ...", flush=True)
    engine = _load_engine(model_dir, device)
    sample_rate = engine.sample_rate  # CosyVoice default 24k
    if prompt_wav:
        print(f"[cosyvoice] reference audio: {prompt_wav}", flush=True)
    else:
        print("[cosyvoice] WARNING: no reference audio found; instruct/zero-shot will fail", flush=True)

    # Warm up the GPU: the first synthesis triggers CUDA kernel compilation /
    # cuDNN autotune, which otherwise makes the user's first real request take
    # ~20s. Do a short throwaway synthesis now so the first real sentence is fast.
    if device == "cuda" and prompt_wav:
        try:
            print("[cosyvoice] warming up (first CUDA inference) ...", flush=True)
            list(engine.inference_zero_shot("你好", "", prompt_wav, stream=False, speed=1.0))
            print("[cosyvoice] warm-up done", flush=True)
        except Exception as e:
            print(f"[cosyvoice] warm-up failed (non-fatal): {e}", flush=True)

    print(f"[cosyvoice] ready, sample rate {sample_rate}, listening on {args.host}:{args.port}", flush=True)

    # Lazy import to avoid loading torch before startup completes.
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
        # `emotion` is accepted for API compatibility but is intentionally NOT
        # passed to inference_instruct2: CosyVoice2's instruct2 feeds the instruct
        # text in as prompt_text, so the instruction itself gets read aloud
        # (e.g. "用开心的语气说" is spoken). We use plain zero-shot instead —
        # emotion is conveyed by the LLM's wording plus the pet's expression.
        speed = float(data.get("speed") or 1.0)

        try:
            import numpy as np
            if not prompt_wav:
                return jsonify({"error": "no reference audio (prompt_wav) available"}), 500
            # Plain zero-shot: clone the reference voice, no instruction leak.
            results = list(
                engine.inference_zero_shot(text, "", prompt_wav, stream=False, speed=speed)
            )
            pcm = np.concatenate([r["tts_speech"].numpy() for r in results]) if results else np.zeros(0, dtype=np.float32)
        except Exception as e:
            print(f"[cosyvoice] synthesis failed: {e}", flush=True)
            return jsonify({"error": str(e)}), 500

        wav = _to_wav(pcm, sample_rate)
        return send_file(io.BytesIO(wav), mimetype="audio/wav")

    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
