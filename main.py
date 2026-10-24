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
import sys
import threading
import time

# —— 强制标准输出用 UTF-8，避免 Windows 控制台（GBK/ASCII）打印中文报错 ——
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
import sounddevice as sd
from pynput import keyboard

import config
from llm import CopilotLLM, telemetry_snapshot

DEBUG_EVENT = "--debug-event" in sys.argv  # 诊断事件检测，默认关
DEBUG_TIMING = "--debug-timing" in sys.argv  # 诊断对话延迟分段，默认关

SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK = 1600  # 0.1s 一块

# 主动闲聊（M6）参数
PROACTIVE_IDLE = 180.0        # 安静这么久（秒）才考虑主动搭话，默认 3 分钟
PROACTIVE_COOLDOWN = 300.0    # 两次主动闲聊最小间隔（秒），默认 5 分钟
PROACTIVE_MIN_SPEED = 30.0    # 平稳行驶速度下限（km/h），低于此不搭话
PROACTIVE_MAX_SPEED = 120.0   # 平稳行驶速度上限（km/h），高于此（狂飙）不搭话


# 句末标点：遇到这些就认为一个完整句子结束，可以立即送去朗读
# 注意：不含换行 \n（流式输出里换行可能出现在句子中间，会导致切得太碎）
_SENT_END = "。！？!?；;"


def split_sentences(stream_text: str, pending: str):
    """按句末标点把流式文本切成完整句子。

    返回 (完整句子列表, 剩余未成句的尾巴)。
    流式场景下，尾巴是还没等到句末标点的半截话，留到下一次拼上继续切。
    """
    buf = pending + stream_text
    sentences = []
    while True:
        # 找第一个句末标点的位置
        idx = -1
        for i, ch in enumerate(buf):
            if ch in _SENT_END:
                idx = i
                break
        if idx == -1:
            break
        # 含标点截成一个句子
        piece = buf[:idx + 1]
        # 跳过紧随的空格/换行，避免句子开头带空白
        rest = buf[idx + 1:].lstrip()
        if piece.strip():
            sentences.append(piece.strip())
        buf = rest
    return sentences, buf


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
        self.telemetry = None   # 遥测读取器（Windows 上才可用）
        self._busy = False
        self._ptt_keys = config.get_ptt_keys() or ()  # 按住说话的热键（可配置）

        from tts import TTS
        self.tts = TTS()

        # 创建遥测读取器（不要求游戏已启动，连不上也能创建，之后自动重连）
        try:
            from telemetry import Ets2Telemetry
            self.telemetry = Ets2Telemetry()
            if self.telemetry.is_connected:
                print("[telemetry] 已连接共享内存")
            else:
                print("[telemetry] 尚未连接（游戏未启动），将自动重试…")
        except Exception as e:
            print(f"[telemetry] 不可用（不影响对话）: {e}")

        self._event_thread = None
        self._event_stop = threading.Event()
        self._last_event_time = 0.0  # 事件播报节流用
        self._last_activity_time = time.time()  # 上次任何「开口」的时间（主动闲聊用）
        self._last_proactive_time = 0.0          # 上次主动闲聊的时间（冷却用）

    # ---------- 事件自动播报（M4）----------
    def _event_loop(self):
        """后台线程：周期读遥测 → 检测事件 → 小满主动点评。

        支持自动重连：游戏启动后会自动接上共享内存，无需重启脚本。
        """
        from events import EventDetector
        det = EventDetector()
        prev = None
        while not self._event_stop.is_set():
            try:
                if self.telemetry is None:
                    break  # 完全不可用（非 Windows 等），退出

                # 没连上（游戏还没启动/共享内存还没建）→ 尝试重连
                if not self.telemetry.is_connected:
                    if self.telemetry.connect():
                        print("[telemetry] 已重新连接共享内存")
                        det = EventDetector()  # 重连后重置事件检测，避免残留状态
                        prev = None
                    else:
                        # 还没连上，等 2 秒再试（不刷屏）
                        time.sleep(2.0)
                        continue

                cur = self.telemetry.read()

                # 游戏断开（读不到 SDK 激活）时，重置事件检测状态
                if not cur.sdk_active:
                    det = EventDetector()
                    prev = None
                    time.sleep(1.0)
                    continue

                if DEBUG_EVENT:
                    # 诊断：确认遥测在动、关键字段读到了什么
                    print(
                        f"[diag] sdk={cur.sdk_active} 速度{cur.speed_kmh:.0f}km/h 限速{cur.speed_limit:.0f} "
                        f"油量{cur.fuel:.0f}L 油警{cur.fuel_warning} 任务{cur.on_job} "
                        f"罚款{cur.fined} 收费站{cur.tollgate} 加油{cur.refuel} "
                        f"ax{cur.accel_x:.1f} ay{cur.accel_y:.1f} az{cur.accel_z:.1f}"
                    )
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

                # —— 主动闲聊（M6）：长时间安静 + 稳定行驶时，小满主动找话题 ——
                self._maybe_proactive(cur)
            except Exception as e:
                # 读遥测偶尔失败（游戏切换等），静默继续
                if DEBUG_EVENT:
                    print(f"[event] 读遥测异常: {e}")
                time.sleep(0.5)
                continue
            time.sleep(0.5)

    def _maybe_proactive(self, cur):
        """安静探测：连续一段时间没对话/事件，且车在平稳行驶，就主动说一句。"""
        now = time.time()
        # 冷却：两次主动闲聊至少间隔 PROACTIVE_COOLDOWN 秒
        if now - self._last_proactive_time < PROACTIVE_COOLDOWN:
            return
        # 距上次任何开口不足 PROACTIVE_IDLE 秒 → 还不够安静，不打扰
        if now - self._last_activity_time < PROACTIVE_IDLE:
            return
        # 正在对话/录音/朗读时跳过
        if self._busy or self.rec.is_recording() or self.tts.is_playing():
            return
        # 只有「平稳行驶中」（速度适中、没暂停）才主动搭话，别在等红灯或停车时冒出来
        if not (PROACTIVE_MIN_SPEED <= cur.speed_kmh <= PROACTIVE_MAX_SPEED):
            return
        self._last_proactive_time = now
        self._last_activity_time = now
        threading.Thread(target=self._speak_proactive, args=(cur,), daemon=True).start()

    def _speak_proactive(self, cur):
        """生成一句主动闲聊并朗读。"""
        try:
            snap = telemetry_snapshot(cur)
            text = self.llm.proactive_chat(snap)
            if not text:
                return
            print(f"[主动] 小满: {text}")
            self.tts.speak_text(text)
        except Exception as e:
            print(f"[主动] 闲聊失败: {e}")

    def _speak_event(self, ev):
        """把事件交给 DeepSeek 生成一句评论并朗读。"""
        try:
            comment = self.llm.comment_on_event(ev.message)
            if not comment:
                return
            self._last_activity_time = time.time()  # 事件播报也算「开口」
            print(f"[事件:{ev.type}] 小满: {comment}")
            self.tts.speak_text(comment)
        except Exception as e:
            print(f"[event] 播报失败: {e}")

    # ---------- PTT ----------
    def _on_press(self, key):
        if self._busy:
            return
        if key in self._ptt_keys:
            if self.tts.is_playing():
                # 正在朗读时按热键 = 打断（含流式未播句子），而不是开始录音
                self.tts.abort_stream()
                return
            if self.rec.is_recording():
                return  # 长按热键会触发键盘自动重复，已在录音则跳过
            self.rec.start_recording()

    def _on_release(self, key):
        if key in self._ptt_keys:
            if not self.rec.is_recording():
                return  # 没在录音就不处理（避免重复松开误触发）
            audio = self.rec.stop_recording()
            if audio is not None and len(audio) > SAMPLE_RATE // 4:  # >0.25s 才处理
                threading.Thread(target=self._handle, args=(audio,), daemon=True).start()

    # ---------- 一轮对话 ----------
    def _handle(self, audio: np.ndarray):
        self._busy = True
        t0 = time.time()
        try:
            import stt
            t_stt_start = time.time()
            user_text = stt.transcribe(audio, SAMPLE_RATE)
            t_stt_end = time.time()
            if not user_text:
                print("（没听清，忽略）")
                return
            self._last_activity_time = time.time()  # 用户说话也算「活动」
            print(f"你说: {user_text}")

            snap = None
            if self.telemetry is not None and self.telemetry.is_connected:
                try:
                    snap = telemetry_snapshot(self.telemetry.read())
                except Exception:
                    pass

            # 收集流式回复（同时打印），按句切分、边生成边朗读
            print("小满: ", end="", flush=True)
            self.tts.start_stream()  # 开启按句流式朗读
            reply_parts = []
            pending = ""
            t_llm_start = time.time()
            try:
                for delta in self.llm.chat(user_text, snap):
                    reply_parts.append(delta)
                    print(delta, end="", flush=True)
                    # 每来一段增量就尝试切句，切出的完整句子立刻送去朗读
                    if any(ch in delta for ch in _SENT_END):
                        done, pending = split_sentences(delta, pending)
                        for s in done:
                            self.tts.speak_sentence(s)
            finally:
                # 收尾：把剩余尾巴（可能没有句末标点）也送去朗读
                tail = pending.strip()
                if tail:
                    self.tts.speak_sentence(tail)
                self.tts.end_stream()
            t_llm_end = time.time()
            print()
            reply = "".join(reply_parts).strip()

            if DEBUG_TIMING:
                print(
                    f"[timing] STT {t_stt_end - t_stt_start:.2f}s | "
                    f"LLM {t_llm_end - t_llm_start:.2f}s | "
                    f"总耗时 {time.time() - t0:.2f}s（按句流式，声音与字幕并行）"
                )
        except Exception as e:
            print(f"\n[error] {e}")
        finally:
            self._busy = False

    def run(self):
        print(f"=== ETS2 副驾 M4（按住 {config.get_ptt_key_display()} 说话，朗读中按同一键打断，Ctrl+C 退出）===")
        print(f"模型: {config.DEEPSEEK_MODEL} @ {config.DEEPSEEK_BASE_URL}")
        print(f"语音: {config.TTS_VOICE}（音量 {config.TTS_VOLUME:.0%}）")
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
