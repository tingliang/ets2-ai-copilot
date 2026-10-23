"""
ETS2 副驾主循环：按住 Alt 说话 → 本地 Whisper 识别 → DeepSeek（小满）流式回答 → TTS 朗读。
另含事件自动播报（M4）：事故/低油/超速/罚款等车况事件触发小满主动点评。

运行：
    python main.py
    - 按住 Alt 说话，松开即发送
    - 朗读中按 Alt 打断
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

        self._event_thread = None
        self._event_stop = threading.Event()
        self._last_event_time = 0.0  # 事件播报节流用

    # ---------- 事件自动播报（M4）----------
    def _event_loop(self):
        """后台线程：周期读遥测 → 检测事件 → 小满主动点评。"""
        from events import EventDetector
        det = EventDetector()
        prev = None
        while not self._event_stop.is_set():
            try:
                if self.telemetry is None:
                    break
                cur = self.telemetry.read()
                for ev in det.detect(prev, cur):
                    # 节流：事件播报间隔至少 3 秒，避免刷屏
                    now = time.time()
                    if now - self._last_event_time < 3.0:
                        continue
                    # 正在对话或朗读时，跳过事件播报（避免抢话/抢声卡）
                    if self._busy or self.rec.is_recording() or self.tts.is_playing():
                        continue
                    self._last_event_time = now
                    threading.Thread(target=self._speak_event,
                                     args=(ev,), daemon=True).start()
                prev = cur
            except Exception as e:
                # 读遥测偶尔失败（游戏切换等），静默继续
                pass
            time.sleep(0.5)

    def _speak_event(self, ev):
        """把事件交给 DeepSeek 生成一句评论并朗读。"""
        try:
            comment = self.llm.comment_on_event(ev.message)
            if not comment:
                return
            print(f"[事件:{ev.type}] 小满: {comment}")
            self.tts.speak_text(comment)
        except Exception as e:
            print(f"[event] 播报失败: {e}")

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

            # 识别出内容后、正式回答前，播一声无语义填充音「嗯」，
            # 表示「听懂了、正在想」，自然衔接后续回答
            threading.Thread(target=self.tts.play_filler, daemon=True).start()

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
        print(f"=== ETS2 副驾 M4（按住 Alt 说话，朗读中按 Alt 打断，Ctrl+C 退出）===")
        print(f"模型: {config.DEEPSEEK_MODEL} @ {config.DEEPSEEK_BASE_URL}")
        print(f"语音: {config.TTS_VOICE}")
        self.rec.start()
        # 启动事件自动播报线程（M4）
        if self.telemetry is not None:
            self._event_thread = threading.Thread(target=self._event_loop, daemon=True)
            self._event_thread.start()
            print("[event] 事件自动播报已启动")
        with keyboard.Listener(on_press=self._on_press, on_release=self._on_release) as lis:
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                self._event_stop.set()
                print("\n退出。")


if __name__ == "__main__":
    CopilotApp().run()
