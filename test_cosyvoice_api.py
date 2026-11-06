# -*- coding: utf-8 -*-
"""Quick diagnostics for the CosyVoice subprocess (bypasses the main program).

This hits the HTTP endpoints directly so we can tell WHERE audio is lost:
  [1] /health      -> is the subprocess up at all?
  [2] /tts         -> does NON-stream synthesis return a valid WAV? (saved to disk)
  [3] /tts_stream  -> does STREAM synthesis return audio chunks, or an error frame?

Run AFTER the subprocess is running (start `python main.py` with TTS_ENGINE=cosyvoice,
then run this in a SECOND terminal, or set the server running some other way).

The produced WAVs are written next to this script so you can double-click them:
  - tts_nonstream.wav  (whole sentence, non-stream)
  - tts_stream.wav     (chunks concatenated, stream)

Usage:
    python test_cosyvoice_api.py
"""
import io
import os
import struct
import sys
import wave

import requests

HOST = "127.0.0.1"
PORT = 9860
BASE = f"http://{HOST}:{PORT}"
HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_RATE = 24000


def _wav_info(wav_bytes: bytes, label: str):
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        n = w.getnframes()
        sr = w.getframerate()
        ch = w.getnchannels()
        dur = n / sr if sr else 0.0
        print(f"    {label}: {sr}Hz {ch}ch {n} frames = {dur:.2f}s")
    return dur


def main():
    # [1] health
    try:
        r = requests.get(f"{BASE}/health", timeout=3)
        print(f"[1] /health -> {r.status_code} {r.text}")
        if r.status_code != 200:
            print("    -> subprocess NOT healthy; start main.py first.")
            return
    except Exception as e:
        print(f"[1] /health FAILED: {e}")
        print("    -> subprocess is NOT running. Start main.py (TTS_ENGINE=cosyvoice) first.")
        return

    text = "晚上好呀，夜路开车我陪你聊。"

    # [2] non-stream /tts
    print(f"\n[2] POST /tts (non-stream), text={text!r}")
    try:
        r = requests.post(f"{BASE}/tts", json={"text": text, "speed": 1.0}, timeout=120)
        print(f"    -> {r.status_code}, {len(r.content)} bytes, type={r.headers.get('content-type')}")
        if r.status_code == 200 and r.content[:4] == b"RIFF":
            _wav_info(r.content, "non-stream wav")
            path = os.path.join(HERE, "tts_nonstream.wav")
            with open(path, "wb") as f:
                f.write(r.content)
            print(f"    saved -> {path}")
        else:
            print(f"    body: {r.text[:300]}")
    except Exception as e:
        print(f"    FAILED: {e}")

    # [3] stream /tts_stream
    print(f"\n[3] POST /tts_stream (stream), text={text!r}")
    try:
        r = requests.post(f"{BASE}/tts_stream", json={"text": text}, stream=True, timeout=120)
        print(f"    -> {r.status_code}")
        buf = b""
        chunks = 0
        err_frames = 0
        out = b""
        for raw in r.iter_content(chunk_size=4096):
            if not raw:
                continue
            buf += raw
            while len(buf) >= 4:
                (n,) = struct.unpack(">I", buf[:4])
                if len(buf) < 4 + n:
                    break
                frame = buf[4:4 + n]
                buf = buf[4 + n:]
                chunks += 1
                if n == 0:
                    err_frames += 1
                    print("    ERROR frame (length 0) -> server hit an exception")
                else:
                    if frame[:4] == b"RIFF":
                        with wave.open(io.BytesIO(frame), "rb") as w:
                            out += w.readframes(w.getnframes())
        print(f"    chunks={chunks}, error_frames={err_frames}")
        if out:
            total = len(out) / 2 / SAMPLE_RATE  # 16-bit mono
            print(f"    total audio (concatenated): {total:.2f}s")
            path = os.path.join(HERE, "tts_stream.wav")
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(out)
            print(f"    saved -> {path}")
        else:
            print("    NO audio produced by /tts_stream.")
    except Exception as e:
        print(f"    FAILED: {e}")


if __name__ == "__main__":
    main()
