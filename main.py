"""
M2 主循环：按住 Alt 说话 → 松开 → 本地 Whisper 识别 → DeepSeek（小满）流式回答（先打印，M3 接 TTS）。

依赖（回家 pip install）：
    pip install openai faster-whisper sounddevice pynput numpy

运行：
    python main.py
    - 按住 Alt 说话，松开即发送
    - Ctrl+C 退出
"""
from __future__ import annotations

import queue
import threading
import time

import numpy as np
import sounddevice as sd
from pynput import keyboard

import config
from llm import CopilotLLM, telemetry_snapshot

SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK = 1600  # 0.1s 一块


class Recorder:
    """按住录音、松开停止的简易 PTT 录音器。"""

    def __init__(self):
        self._frames: queue.Queue = queue.Queue()
        self._recording = False
        self._chunks: list[np.ndarray] = []

    def _callback(self, indata, frames, time_info, status):
        if self._recording:
            self._frames.put(indata.copy())

    def start(self):
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS,
            dtype="float32", blocksize=BLOCK, callback=self._callback,
        )
        self._stream.start()

    def start_recording(self):
        self._chunks = []
        self._recording = True
        print("🎤 开始录音...（松开 Alt 结束）")

    def is_recording(self) -> bool:
        return self._recording

    def stop_recording(self) -> np.ndarray | None:
        self._recording = False
        # 把队列里剩余的收干净
        time.sleep(0.05)
        while not self._frames.empty():
            self._chunks.append(self._frames.get())
        if not self._chunks:
            return None
        return np.concatenate(self._chunks, axis=0).flatten()


class CopilotApp:
    def __init__(self):
        self.llm = CopilotLLM()
        self.rec = Recorder()
        self.telemetry = None   # M1 的读取器，Windows 上才可用
        self._busy = False

        from tts import TTS
        self.tts = TTS()

        try:
            from telemetry import Ets2Telemetry
            self.telemetry = Ets2Telemetry()
            print("[telemetry] 已连接共享内存")
        except Exception as e:
            print(f"[telemetry] 不可用（不影响对话）: {e}")

    # ---------- PTT ----------
    def _on_press(self, key):
        if self._busy:
            return
        if key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r):
            if self.tts.is_playing():
                # 正在朗读时按 Alt = 打断，而不是开始录音
                self.tts.stop()
                return
            if self.rec.is_recording():
                return  # 长按 Alt 会触发键盘自动重复，已在录音则跳过
            # 按下 Alt 瞬间先播一声「嗯？」填充音，让用户立刻感到被回应
            threading.Thread(target=self.tts.play_filler, daemon=True).start()
            self.rec.start_recording()

    def _on_release(self, key):
        if key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r):
            if not self.rec.is_recording():
                return  # 没在录音就不处理（避免重复松开误触发）
            audio = self.rec.stop_recording()
            if audio is not None and len(audio) > SAMPLE_RATE // 4:  # >0.25s 才处理
                threading.Thread(target=self._handle, args=(audio,), daemon=True).start()

    # ---------- 一轮对话 ----------
    def _handle(self, audio: np.ndarray):
        self._busy = True
        try:
            import stt
            user_text = stt.transcribe(audio, SAMPLE_RATE)
            if not user_text:
                print("（没听清，忽略）")
                return
            print(f"你说: {user_text}")

            snap = None
            if self.telemetry is not None:
                try:
                    snap = telemetry_snapshot(self.telemetry.read())
                except Exception:
                    pass

            # 收集流式回复（同时打印），整段生成完后台朗读
            print("小满: ", end="", flush=True)
            reply_parts = []
            for delta in self.llm.chat(user_text, snap):
                reply_parts.append(delta)
                print(delta, end="", flush=True)
            print()
            reply = "".join(reply_parts).strip()
            if reply:
                threading.Thread(target=self.tts.speak_text,
                                 args=(reply,), daemon=True).start()
        except Exception as e:
            print(f"\n[error] {e}")
        finally:
            self._busy = False

    def run(self):
        print(f"=== ETS2 副驾 M3（按住 Alt 说话，朗读中按 Alt 打断，Ctrl+C 退出）===")
        print(f"模型: {config.DEEPSEEK_MODEL} @ {config.DEEPSEEK_BASE_URL}")
        print(f"语音: {config.TTS_VOICE}")
        self.rec.start()
        with keyboard.Listener(on_press=self._on_press, on_release=self._on_release) as lis:
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                print("\n退出。")


if __name__ == "__main__":
    CopilotApp().run()
