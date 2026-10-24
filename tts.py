"""
中文朗读（TTS）：edge-tts 整段合成 + 播放。

设计：
- 整段文字合成一次 → 解码 → 播放（简单可靠）。
- 不做填充音：所有「伪沉吟」都试过（拖长「嗯」、短句「让我看看」），
  都不自然。改为「识别完成 → 立即生成 → 立即合成播放」，缩短真实延迟。
- 播放用 sounddevice，可被 stop() 打断（带淡出）。

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


def _apply_volume(pcm: np.ndarray) -> np.ndarray:
    """按 config.TTS_VOLUME 缩放音量（0.0~1.0，1.0=原声）。"""
    vol = config.TTS_VOLUME
    if vol >= 1.0:
        return pcm
    return (pcm * vol).astype(np.float32)


def synthesize(text: str, rate: str = None) -> bytes:
    """用 edge-tts 合成一段文字，返回 mp3 字节。

    注意：edge-tts 是纯文本合成，不支持 SSML（<break> 等标签会被当文字读）。
    合成失败（返回空音频）时自动重试一次，应对偶发的网络限流。
    """
    rate = rate or config.TTS_RATE

    async def _run():
        communicate = __import__("edge_tts").Communicate(
            text, config.TTS_VOICE, rate=rate)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        return buf.getvalue()

    for attempt in (1, 2):
        data = asyncio.run(_run())
        if data:
            return data
        if attempt == 2:
            break
        # 空音频（可能被限流），稍等片刻重试一次
        import time
        time.sleep(0.3)
    raise RuntimeError("No audio was received. Please verify that your parameters are correct.")


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
    """整段朗读。支持打断。

    另提供按句流式播放：speak_sentence() 把每个完整句子放进队列，
    由后台线程依次「合成→播放」，实现边生成边念，消除字幕和声音的落差。
    """

    def __init__(self):
        self._playing = False
        self._stop_flag = threading.Event()
        self._play_lock = threading.Lock()  # 同一时间只允许一个声音播放
        self._current_pcm = None  # 正在播放的音频（用于打断淡出）

        # —— 按句流式播放：句子队列 + 专用播放线程 ——
        self._sent_queue: "queue.Queue" = None
        self._sent_thread: threading.Thread = None
        self._sent_active = False  # 流式会话进行中（未收到 end 前保持线程）

    # ---------- 按句流式播放 ----------
    def start_stream(self):
        """开启一次流式朗读会话：起后台线程，按队列顺序播放句子。"""
        import queue
        # 若上一轮流还没结束（残留线程/句子），先中止它，避免新句子被吞
        if self._sent_thread and self._sent_thread.is_alive():
            self.abort_stream()
        self._sent_queue = queue.Queue()
        self._sent_active = True
        self._sent_thread = threading.Thread(
            target=self._stream_worker, daemon=True)
        self._sent_thread.start()

    def _stream_worker(self):
        import queue
        while True:
            try:
                item = self._sent_queue.get(timeout=0.2)
            except queue.Empty:
                # 会话已结束且队列空 → 退出线程
                if not self._sent_active:
                    break
                continue
            if item is None:
                break  # end 哨兵
            self.speak_text(item)

    def speak_sentence(self, text: str):
        """把一个完整句子送入播放队列（后台串行合成+播放，不阻塞）。

        过滤：去掉空白和纯标点后，若不足 1 个字则丢弃，
        避免把过短/纯标点片段丢给 edge-tts 导致「No audio was received」。
        """
        text = (text or "").strip()
        if not text or self._sent_queue is None:
            return
        # 去掉标点/空白后检查是否还有实际内容
        import re
        meaningful = re.sub(r"[\s。！？!?；;，、,.：:…—\-~·]+", "", text)
        if len(meaningful) < 1:
            return
        self._sent_queue.put(text)

    def end_stream(self):
        """结束流式会话：清空标志，等后台线程把剩余句子读完。"""
        self._sent_active = False
        if self._sent_queue is not None:
            self._sent_queue.put(None)  # 哨兵，让 worker 尽快退出

    def abort_stream(self):
        """中止流式会话：清空未播句子并停止当前播放。"""
        self._sent_active = False
        if self._sent_queue is not None:
            # 清空队列
            try:
                while True:
                    self._sent_queue.get_nowait()
            except Exception:
                pass
        self.stop()

    def stop(self):
        """打断当前播放，带平滑淡出。

        淡出：取当前音频尾部约 120ms 做音量线性渐降到 0，再停，避免戛然而止。
        """
        self._stop_flag.set()
        pcm = self._current_pcm
        if pcm is not None and len(pcm) > 0:
            try:
                fade_n = int(_sample_rate * 0.12)
                tail = pcm[-fade_n:] if len(pcm) > fade_n else pcm
                env = np.linspace(1.0, 0.0, len(tail), dtype=np.float32)
                sd.play(_apply_volume(tail) * env, _sample_rate)
                sd.wait()
            except Exception:
                pass
        sd.stop()

    def is_playing(self) -> bool:
        return self._playing

    def speak_text(self, text: str):
        """整段合成 + 播放（阻塞直到读完）。"""
        text = text.strip()
        if not text:
            return
        import time
        _debug_timing = "--debug-timing" in __import__("sys").argv
        t_synth_start = time.time()
        self._playing = True
        self._stop_flag.clear()
        try:
            mp3 = synthesize(text)
            t_synth_end = time.time()
            if _debug_timing:
                print(f"[timing] TTS合成 {t_synth_end - t_synth_start:.2f}s")
            if self._stop_flag.is_set():
                return
            pcm = _mp3_to_pcm(mp3)
            if self._stop_flag.is_set():
                return
            sd.stop()
            with self._play_lock:
                if self._stop_flag.is_set():
                    return
                self._current_pcm = pcm
                sd.play(_apply_volume(pcm), _sample_rate)
                sd.wait()
        except Exception as e:
            print(f"[tts] 合成/播放失败: {e}")
        finally:
            self._current_pcm = None
            self._playing = False


if __name__ == "__main__":
    print("TTS 测试（整段朗读）：")
    t = TTS()
    t.speak_text("哎哟，就这车技还跑长途呢，回去重考本吧！")
    print("完成。")
