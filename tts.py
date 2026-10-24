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


def _apply_volume(pcm: np.ndarray) -> np.ndarray:
    """按 config.TTS_VOLUME 缩放音量（0.0~1.0，1.0=原声）。"""
    vol = config.TTS_VOLUME
    if vol >= 1.0:
        return pcm
    return (pcm * vol).astype(np.float32)

# 预合成的填充音（thinking filler / pre-filler）。
#
# 研究结论（见 requirements-and-architecture.md「填充音设计」）：
#   1. 填充音必须「不预设立场」（verdict-neutral）——不能用带情绪的「嗯！」「好！」，
#      否则用户会预判答案方向，一旦答案相反就显怪。
#   2. 不能放具体语义（如「让我想想」）——用户会开始等后续，反而加重等待感。
#   3. 正确做法是接近人类「沉吟 + 呼吸」的短音，最好随机轮换。
#
# 关键：edge-tts 不支持自定义 SSML（<break>/<prosody> 会被转义成文字），
# 单个语气词固定约 1.3s，标点也改不了。要「拖长沉吟」只能在音频层面做：
# 取尾音一小段做音量渐弱并重复，模拟「嗯～～～」逐渐消失的思考拖音。
_FILLERS = [
    "嗯",
    "呃",
    "哦",
]

# 拖长参数：从语气词尾部取 _FILLER_TAIL_MS 毫秒做渐弱，重复 _FILLER_LOOP 次，
# 让总时长从约 1.3s 延长到约 2~2.5s，更像真人在沉吟。
_FILLER_TAIL_MS = 300     # 取尾部这段做渐弱
_FILLER_LOOP = 3          # 重复拼接次数（越大拖得越长）


def _lengthen_filler(pcm: np.ndarray) -> np.ndarray:
    """把语气词音频的尾音做淡出延长，制造自然沉吟拖音。

    原理：取尾部一小段，音量线性渐弱到接近 0，再重复拼接几次。
    这样「嗯」从一声短音变成「嗯～～～」逐渐消失的感觉，而不是生硬静音。
    """
    if pcm is None or len(pcm) == 0:
        return pcm
    tail_n = int(_sample_rate * _FILLER_TAIL_MS / 1000)
    if tail_n <= 0 or len(pcm) < tail_n:
        return pcm
    tail = pcm[-tail_n:].copy()
    # 渐弱包络：1 → 0.05（不完全静音，留一点气声更自然）
    env = np.linspace(1.0, 0.05, len(tail), dtype=np.float32)
    faded = tail * env
    # 重复拼接：原音 + N 次渐弱尾音
    return np.concatenate([pcm] + [faded] * _FILLER_LOOP)


def synthesize(text: str, rate: str = None) -> bytes:
    """用 edge-tts 合成一段文字，返回 mp3 字节。

    注意：edge-tts 是纯文本合成，不支持 SSML（<break> 等标签会被当文字读）。
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
        self._filler_playing = False
        self._stop_flag = threading.Event()
        self._play_lock = threading.Lock()  # 同一时间只允许一个声音播放
        self._fillers = self._preload_fillers()
        self._current_pcm = None  # 正在播放的音频（用于打断淡出）

    def _preload_fillers(self) -> list[np.ndarray]:
        """启动时预合成填充音（含尾音拖长），存成 PCM，用时零延迟。"""
        pcm_list = []
        for text in _FILLERS:
            try:
                pcm = _mp3_to_pcm(synthesize(text))
                pcm = _lengthen_filler(pcm)  # 尾音淡出延长，制造沉吟拖音
                pcm_list.append(pcm)
                print(f"[tts] 预合成填充音: {text}（约 {len(pcm)/_sample_rate:.1f}s）")
            except Exception as e:
                print(f"[tts] 预合成填充音失败（忽略）: {e}")
        return pcm_list

    def play_filler(self):
        """零延迟播放一个填充音（随机挑一个），播完自动释放锁。

        非阻塞：在独立线程里播放，主线程继续走「识别→生成」流程，
        真正回答准备好后由 speak_text 打断填充音接上（抢锁时 stop 填充音）。
        """
        if not self._fillers:
            return
        import random
        pcm = random.choice(self._fillers)
        with self._play_lock:
            if self._stop_flag.is_set():
                return
            self._filler_playing = True
            try:
                self._current_pcm = pcm
                sd.play(_apply_volume(pcm), _sample_rate)
                sd.wait()
            except Exception as e:
                print(f"[tts] 播放填充音失败: {e}")
            finally:
                self._filler_playing = False
                self._current_pcm = None

    def stop(self):
        """打断当前播放（回答或填充音都打断），带平滑淡出。

        淡出：取当前音频尾部约 120ms 做音量线性渐降到 0，再停，避免戛然而止。
        """
        self._stop_flag.set()
        pcm = self._current_pcm
        if pcm is not None and len(pcm) > 0:
            try:
                # 取尾部最多 120ms 做渐弱
                fade_n = int(_sample_rate * 0.12)
                tail = pcm[-fade_n:] if len(pcm) > fade_n else pcm
                # 线性渐变包络：1 → 0
                env = np.linspace(1.0, 0.0, len(tail), dtype=np.float32)
                sd.play(_apply_volume(tail) * env, _sample_rate)
                sd.wait()
            except Exception:
                pass
        sd.stop()

    def is_playing(self) -> bool:
        return self._playing or self._filler_playing

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
            # 拿到声卡锁前，先打断还在播的填充音（避免被填充音尾音拖延）
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
