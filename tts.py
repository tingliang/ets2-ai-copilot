"""
中文朗读（TTS）：edge-tts 整段合成 + 播放，带填充音（thinking filler）。

设计：
- 整段文字合成一次 → 解码 → 播放（简单可靠）。
- 填充音（如「嗯？」「让我想想」）在启动时预合成好，按下 Alt 的瞬间
  零延迟播放，填补「识别 + 生成」的感知空窗。
- 播放用 sounddevice，可被 stop() 打断。

依赖：edge-tts（合成）、sounddevice + numpy（播放）、ffmpeg（解码 mp3）。
"""
from __future__ import annotations

import asyncio
import io
import subprocess
import tempfile
import threading

import numpy as np
import sounddevice as sd

import config

_sample_rate = 24000  # 播放采样率（edge-tts 输出 24k）

# 预合成的填充音（按下 Alt 时零延迟播放，掩盖处理延迟）
_FILLERS = ["嗯？", "让我想想啊"]


def synthesize(text: str) -> bytes:
    """用 edge-tts 合成一段文字，返回 mp3 字节。"""
    async def _run():
        communicate = __import__("edge_tts").Communicate(
            text, config.TTS_VOICE, rate=config.TTS_RATE)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        return buf.getvalue()

    return asyncio.run(_run())


def _mp3_to_pcm(mp3_bytes: bytes) -> np.ndarray:
    """用 ffmpeg 把 mp3 解码成 float32 PCM（单声道 24k）。"""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(mp3_bytes)
        mp3_path = f.name
    try:
        cmd = [
            "ffmpeg", "-y", "-i", mp3_path,
            "-f", "f32le", "-acodec", "pcm_f32le",
            "-ar", str(_sample_rate), "-ac", "1", "-",
        ]
        out = subprocess.run(cmd, capture_output=True, check=True)
        return np.frombuffer(out.stdout, dtype=np.float32)
    finally:
        import os
        os.unlink(mp3_path)


class TTS:
    """整段朗读 + 填充音。支持打断。"""

    def __init__(self):
        self._playing = False
        self._stop_flag = threading.Event()
        self._play_lock = threading.Lock()  # 同一时间只允许一个声音播放
        self._fillers = self._preload_fillers()

    def _preload_fillers(self) -> list[np.ndarray]:
        """启动时预合成填充音，存成 PCM，用时零延迟。"""
        pcm_list = []
        for text in _FILLERS:
            try:
                pcm_list.append(_mp3_to_pcm(synthesize(text)))
                print(f"[tts] 预合成填充音: {text}")
            except Exception as e:
                print(f"[tts] 预合成填充音失败（忽略）: {e}")
        return pcm_list

    def play_filler(self):
        """零延迟播放一个填充音（随机挑一个），播完自动释放锁。"""
        if not self._fillers:
            return
        import random
        pcm = random.choice(self._fillers)
        with self._play_lock:
            if self._stop_flag.is_set():
                return
            try:
                sd.play(pcm, _sample_rate)
                sd.wait()
            except Exception as e:
                print(f"[tts] 播放填充音失败: {e}")

    def stop(self):
        """打断当前播放。"""
        if self._playing:
            self._stop_flag.set()
            sd.stop()

    def is_playing(self) -> bool:
        return self._playing

    def speak_text(self, text: str):
        """整段合成 + 播放（阻塞直到读完）。"""
        text = text.strip()
        if not text:
            return
        self._playing = True
        self._stop_flag.clear()
        try:
            mp3 = synthesize(text)
            if self._stop_flag.is_set():
                return
            pcm = _mp3_to_pcm(mp3)
            if self._stop_flag.is_set():
                return
            # 等填充音播完再出声（避免抢声卡）
            with self._play_lock:
                if self._stop_flag.is_set():
                    return
                sd.play(pcm, _sample_rate)
                sd.wait()
        except Exception as e:
            print(f"[tts] 合成/播放失败: {e}")
        finally:
            self._playing = False


if __name__ == "__main__":
    print("TTS 测试（整段朗读）：")
    t = TTS()
    t.speak_text("哎哟，就这车技还跑长途呢，回去重考本吧！")
    print("完成。")
