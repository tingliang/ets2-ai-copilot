"""
中文朗读（TTS）：edge-tts，按句流式合成 + 播放。

设计要点（解决「字幕先到、声音后到」）：
- DeepSeek 是流式吐字，我们攒到句子边界（。！？；\n）就立刻把这一句送去合成，
  不等整段回答结束，声音尽量跟上文字。
- 播放用 sounddevice（和录音同库），合成出 mp3 → 解码为 PCM → 播放。
- 播放可被下一次 PTT 打断（见 main.py 的 stop()）。

依赖：edge-tts（合成）、sounddevice + numpy（播放）、解码 mp3 用 ffmpeg（
  如果系统没有 ffmpeg，退回用 edge-tts 直出的 mp3 交给系统播放器，
  但那样无法精确控制打断，推荐装 ffmpeg）。

注意：edge-tts 走微软在线服务，需联网（新加坡访问很快）。
"""
from __future__ import annotations

import asyncio
import io
import queue
import subprocess
import tempfile
import threading
import time

import numpy as np
import sounddevice as sd

import config

# 句子边界：攒到这些字符就把当前缓冲送合成
_SENTENCE_END = "。！？!?；;…\n"

_sample_rate = 24000  # 播放采样率（edge-tts 输出 24k）


def _split_sentences(stream):
    """把流式文本按句子边界切块 yield。"""
    buf = ""
    for delta in stream:
        if not delta:
            continue
        buf += delta
        # 找到最后一个句子边界，切出完整句
        cut = -1
        for ch in _SENTENCE_END:
            i = buf.rfind(ch)
            if i > cut:
                cut = i
        if cut >= 0:
            yield buf[:cut + 1]
            buf = buf[cut + 1:]
    if buf.strip():
        yield buf


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
        pcm = np.frombuffer(out.stdout, dtype=np.float32)
        return pcm
    finally:
        import os
        os.unlink(mp3_path)


class TTS:
    """按句朗读。支持打断。"""

    def __init__(self):
        self._playing = False
        self._stop_flag = threading.Event()

    def stop(self):
        """打断当前播放。"""
        if self._playing:
            self._stop_flag.set()
            sd.stop()

    def is_playing(self) -> bool:
        return self._playing

    def speak_stream(self, stream):
        """把流式文本按句合成并朗读（阻塞直到读完）。"""
        self._playing = True
        self._stop_flag.clear()
        try:
            for sentence in _split_sentences(stream):
                if self._stop_flag.is_set():
                    break
                sentence = sentence.strip()
                if not sentence:
                    continue
                try:
                    mp3 = synthesize(sentence)
                    pcm = _mp3_to_pcm(mp3)
                    if self._stop_flag.is_set():
                        break
                    sd.play(pcm, _sample_rate)
                    sd.wait()
                except Exception as e:
                    print(f"[tts] 合成/播放失败: {e}")
        finally:
            self._playing = False
            self._stop_flag.clear()


def speak_text(text: str):
    """简单同步朗读一段文字（测试用）。"""
    t = TTS()
    t.speak_stream(iter([text]))


class SentenceSpeaker:
    """边生成边念：一个后台线程从队列取整句 → 合成 → 播放。

    主线程（DeepSeek 流式生成）按句 push；本线程逐句消费，
    实现「第一句生成完就立刻开始念」，降低首句延迟。
    支持 stop() 打断。
    """

    def __init__(self, voice: str = None, rate: str = None):
        self.voice = voice or config.TTS_VOICE
        self.rate = rate or config.TTS_RATE
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._playing = False

    def start(self):
        self._thread.start()

    def push(self, sentence: str):
        """主线程调用：喂入一句完整的话（可多次调用）。"""
        s = sentence.strip()
        if s:
            self._q.put(s)

    def finish(self):
        """主线程调用：表示没有更多句子了，队列消费完即结束。"""
        self._q.put(None)  # 哨兵

    def stop(self):
        """打断播放并清空队列。"""
        self._stop.set()
        sd.stop()
        # 清空队列，避免残留
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def is_playing(self) -> bool:
        return self._playing

    def _run(self):
        self._playing = True
        try:
            while True:
                item = self._q.get()
                if item is None:
                    break
                if self._stop.is_set():
                    break
                try:
                    mp3 = self._synthesize(item)
                    pcm = _mp3_to_pcm(mp3)
                    if self._stop.is_set():
                        break
                    sd.play(pcm, _sample_rate)
                    sd.wait()
                except Exception as e:
                    print(f"[tts] 合成/播放失败: {e}")
        finally:
            self._playing = False

    def _synthesize(self, text: str) -> bytes:
        async def _run():
            communicate = __import__("edge_tts").Communicate(
                text, self.voice, rate=self.rate)
            buf = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            return buf.getvalue()
        return asyncio.run(_run())


if __name__ == "__main__":
    print("TTS 测试：")
    t = TTS()
    t.speak_stream(iter(["哎哟，就这车技还跑长途呢，回去重考本吧！"]))
    print("完成。")
