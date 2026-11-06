# -*- coding: utf-8 -*-
"""Preview the current CosyVoice voice by synthesizing a few short "嗯" fillers.

This is meant for a quick voice check: it records (saves) a couple of common
interjection variants to WAV and plays them back, so you can hear what the
zero-shot reference voice actually sounds like on short responses.

Usage:
    python preview_voice.py                 # default filler list, saves + plays
    python preview_voice.py "嗯？"           # synthesize a specific text
    python preview_voice.py --no-play       # save WAVs only, do not play

Output files are written next to this script:
    preview_1.wav, preview_2.wav, ...

The CosyVoice subprocess is started automatically if it is not already running
(same code path as main.py), so you do NOT need to keep main.py open.
"""
import os
import sys
import wave

import numpy as np

DEFAULT_TEXTS = ["嗯？", "嗯。", "嗯嗯。", "嗯，好的。"]


def _save_wav(pcm: np.ndarray, sample_rate: int, path: str):
    pcm = np.clip(np.asarray(pcm, dtype=np.float32), -1.0, 1.0)
    int16 = (pcm * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(int16.tobytes())


def main():
    args = [a for a in sys.argv[1:]]
    no_play = "--no-play" in args
    texts = [a for a in args if not a.startswith("--")]
    if not texts:
        texts = DEFAULT_TEXTS

    # Import here so `python preview_voice.py` does not pull torch/sherpa deps
    # until we actually need them (cosyvoice server runs in its own subprocess).
    from tts import _start_cosyvoice_server, _CosyVoiceTTS
    import sounddevice as sd

    print("[preview] ensuring CosyVoice subprocess is running ...")
    _start_cosyvoice_server()
    cv = _CosyVoiceTTS()
    if not cv.wait_ready(timeout=180):
        print("[preview] CosyVoice subprocess not ready (model still loading or failed).")
        print("[preview] Run `python main.py` once and check its log, then retry.")
        return 1

    here = os.path.dirname(os.path.abspath(__file__))
    for idx, text in enumerate(texts, 1):
        print(f"[preview] synthesizing: {text!r}")
        try:
            pcm = cv.synthesize(text)
        except Exception as e:
            print(f"    FAILED: {e}")
            continue
        path = os.path.join(here, f"preview_{idx}.wav")
        _save_wav(pcm, cv.sample_rate, path)
        print(f"    saved -> {path}")
        if not no_play:
            sd.stop()
            sd.play(pcm, cv.sample_rate)
            sd.wait()

    print("[preview] done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
