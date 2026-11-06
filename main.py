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

# —— 提前注册 CUDA DLL 搜索路径（必须在任何会加载 ctranslate2 的 import 之前）——
# RTX 50 系 + ctranslate2 推理时找不到 cublas64_12.dll。根因：
# ctranslate2 把 cublas/cudnn 的 DLL 打包在 site-packages/ctranslate2/ 目录里，
# 但 Windows 的 LoadLibrary 不会自动去那找。这里进程一启动就注册两个来源：
#   1) site-packages/ctranslate2/ 目录本身（ctranslate2 自带的 DLL）
#   2) site-packages/nvidia/*/bin/（pip 装的 nvidia-*-cu12 包）
if sys.platform == "win32":
    try:
        import os as _os
        import site as _site
        _added = []
        for _base in _site.getsitepackages():
            # 1) ctranslate2 自带 DLL
            _ct2 = _os.path.join(_base, "ctranslate2")
            if _os.path.isdir(_ct2) and _ct2 not in _added:
                try:
                    _os.add_dll_directory(_ct2)
                    _added.append(_ct2)
                except Exception:
                    pass
            # 2) nvidia/*/bin 目录
            _nvidia_root = _os.path.join(_base, "nvidia")
            if _os.path.isdir(_nvidia_root):
                for _entry in _os.listdir(_nvidia_root):
                    _bin = _os.path.join(_nvidia_root, _entry, "bin")
                    if _os.path.isdir(_bin):
                        _has = any(
                            f.lower().startswith(("cublas", "cudnn", "cudart",
                                                  "cufft", "curand", "cusparse",
                                                  "nvrtc", "cublaslt"))
                            and f.lower().endswith(".dll")
                            for f in _os.listdir(_bin)
                        )
                        if _has and _bin not in _added:
                            try:
                                _os.add_dll_directory(_bin)
                                _added.append(_bin)
                            except Exception:
                                pass
        if _added:
            print(f"[cuda] 已注册 CUDA DLL 目录（{len(_added)} 个）")
    except Exception as _e:
        print(f"[cuda] 注册 CUDA DLL 目录失败（忽略）: {_e}")

import numpy as np
import sounddevice as sd
from pynput import keyboard

import config
from llm import CopilotLLM, telemetry_snapshot, parse_emotion, PERSONA_META
from pet_bridge import PetBridge, EVENT_EMOTION_MAP, EVENT_ACTION_MAP

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

# 唤醒词免按键（M7）参数
WAKE_TIMEOUT = 8.0            # 唤醒后这么久（秒）没说正文，自动回到监听态


class Recorder:
    """按住录音、松开停止的简易 PTT 录音器。

    支持「无麦克风也能运行」：
    - start() 找不到输入设备时不崩溃，标记 mic_ready=False，返回 bool
    - 之后每次 start_recording() 都会尝试（重新）打开输入流，蓝牙耳机
      接上后会自动恢复录音能力，无需重启脚本。

    另外提供「持续收音 + VAD 切句」能力（供唤醒词免按键模式用）：
    - start_continuous()：常驻收音，靠能量阈值把音频切成一「段」说话片段，
      静音超过 VAD_SILENCE 秒即认为一句话结束，回调 on_utterance(音频)。
    - stop_continuous()：关闭常驻收音。
    """

    # VAD 参数（能量阈值 + 静音判定时长）
    VAD_THRESHOLD = 0.015   # RMS 能量阈值，超过算「有声音」
    VAD_SILENCE = 0.6       # 连续静音这么久（秒）认为一句话结束
    VAD_MIN_DUR = 0.3       # 一句话最短时长（秒），更短视为噪音丢弃

    def __init__(self):
        self._frames: queue.Queue = queue.Queue()
        self._recording = False
        self._chunks: list[np.ndarray] = []
        self._stream = None
        self.mic_ready = False  # 当前是否有可用的输入设备/流
        # —— 持续收音状态 ——
        self._continuous = False
        self._on_utterance = None       # 一句话回调：fn(audio: np.ndarray)
        self._voice_chunks: list[np.ndarray] = []
        self._in_voice = False
        self._silence_blocks = 0

    def _callback(self, indata, frames, time_info, status):
        if self._recording:
            self._frames.put(indata.copy())
        if self._continuous:
            self._on_block(indata.copy())

    def _open_stream(self) -> bool:
        """尝试打开输入流。成功返回 True，失败（无麦克风）返回 False。"""
        if self._stream is not None:
            # 已有流，直接可用
            return True
        # 关键：PortAudio 在初始化时会固化设备列表，之后热插拔的麦克风
        # （如蓝牙耳机）不会被自动识别。所以每次流没打开时，先 terminate
        # 再 initialize，强制重新枚举设备，实现「接入后自动恢复」。
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=CHANNELS,
                dtype="float32", blocksize=BLOCK, callback=self._callback,
            )
            self._stream.start()
            self.mic_ready = True
            return True
        except Exception as e:
            self._stream = None
            self.mic_ready = False
            return False

    def start(self) -> bool:
        """启动录音器。无麦克风也返回（不崩溃），返回是否有麦克风。"""
        ok = self._open_stream()
        if ok:
            print("[mic] 麦克风就绪")
        else:
            print("[mic] 未检测到输入设备，语音输入暂不可用（接入后自动恢复）")
        return ok

    def start_recording(self):
        # 每次按热键都尝试（重新）打开输入流，支持蓝牙耳机后接入自动恢复
        if not self._open_stream():
            self.mic_ready = False
            return  # 无麦克风，不进入录音状态
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

    # ---------- 持续收音 + VAD 切句 ----------
    def start_continuous(self, on_utterance) -> bool:
        """常驻收音，检测到一句完整的话就回调 on_utterance(音频)。

        返回是否成功开启（无麦克风返回 False）。
        """
        if not self._open_stream():
            self.mic_ready = False
            return False
        self._on_utterance = on_utterance
        self._voice_chunks = []
        self._in_voice = False
        self._silence_blocks = 0
        self._continuous = True
        return True

    def stop_continuous(self):
        """关闭常驻收音（保留输入流，供 PTT 模式复用）。"""
        self._continuous = False
        self._on_utterance = None
        self._voice_chunks = []
        self._in_voice = False
        self._silence_blocks = 0

    def _on_block(self, block: np.ndarray):
        """每收到一块音频，做能量 VAD，切出一句完整的话回调出去。"""
        # 计算这一块的 RMS 能量（float32 [-1,1]）
        mono = block.reshape(-1)
        rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)))

        if rms >= self.VAD_THRESHOLD:
            # 有声音
            if not self._in_voice:
                self._in_voice = True
                self._voice_chunks = []
            self._voice_chunks.append(block)
            self._silence_blocks = 0
        else:
            # 静音
            if self._in_voice:
                self._voice_chunks.append(block)
                self._silence_blocks += 1
                silence_dur = self._silence_blocks * (BLOCK / SAMPLE_RATE)
                if silence_dur >= self.VAD_SILENCE:
                    self._emit_utterance()

    def _emit_utterance(self):
        """把当前收集的一句话交出去（若够长），并复位状态。"""
        chunks = self._voice_chunks
        self._voice_chunks = []
        self._in_voice = False
        self._silence_blocks = 0
        if not chunks:
            return
        audio = np.concatenate(chunks, axis=0).flatten()
        dur = len(audio) / SAMPLE_RATE
        if dur < self.VAD_MIN_DUR:
            return  # 太短，视为噪音/误触发，丢弃
        if self._on_utterance:
            self._on_utterance(audio)


class CopilotApp:
    def __init__(self):
        self.llm = CopilotLLM()
        self.rec = Recorder()
        self.telemetry = None   # 遥测读取器（Windows 上才可用）
        self._busy = False
        self._ptt_keys = config.get_ptt_keys() or ()  # 按住说话的热键（可配置）

        from tts import TTS
        self.tts = TTS()

        # 桌宠桥接：默认开启，--no-pet 或 ENABLE_PET=0 关闭
        pet_enabled = ("--no-pet" not in sys.argv) and getattr(config, "ENABLE_PET", True)
        self.pet = PetBridge(enabled=pet_enabled)
        # 桌宠反向命令处理：右键「切换人设」→ 热切换 llm 人设
        self.pet.set_command_handler(self._on_pet_command)

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
        self._quit_event = threading.Event()  # 桌宠右键「退出」或 Ctrl+C 触发整体退出
        self._last_event_time = 0.0  # 事件播报节流用
        self._last_activity_time = time.time()  # 上次任何「开口」的时间（主动闲聊用）
        self._last_proactive_time = 0.0          # 上次主动闲聊的时间（冷却用）
        self._used_notes: set[str] = set()       # 已讲过的当地趣事（去重，避免重复）

    # ---------- 朗读 + 桌宠同步 ----------
    def _say(self, text: str, emotion: str | None = None):
        """朗读一段话，并同步驱动桌宠（换表情 → 张嘴+字幕 → 闭嘴）。

        阻塞直到读完（TTS 本身就是阻塞的）。调用方若想不阻塞，自行包线程。
        emotion 同时传给 TTS（cosyvoice 引擎做情感语气）和桌宠（换表情）。
        """
        if self.pet:
            self.pet.speak_start(text, emotion)
        try:
            self.tts.speak_text(text, emotion)
        finally:
            if self.pet:
                self.pet.speak_stop()

    # ---------- 桌宠反向命令（右键菜单 → 主程序）----------
    def _on_pet_command(self, cmd: dict):
        """处理桌宠上报的命令（通过 stdout 反向通道）。"""
        ctype = cmd.get("type")
        if ctype == "quit_app":
            # 右键「退出」：整体退出（关语音、停 TTS、释放资源）
            print("[pet] 收到退出指令，正在关闭…")
            self._quit_event.set()
        elif ctype == "switch_persona":
            name = cmd.get("name")
            if name and self.llm.set_persona(name):
                # 切换成功后，回推最新人设清单（高亮新当前人设）
                self._push_personas()
                # 桌宠说一句确认，更有代入感
                meta = PERSONA_META.get(name, {})
                display = meta.get("name", name)
                threading.Thread(
                    target=self._say,
                    args=(f"好嘞，我是{display}啦，接下来陪你的就是我～", "happy"),
                    daemon=True,
                ).start()
            else:
                print(f"[pet] 切换人设失败: {name}")

    def _push_personas(self):
        """把人设清单推给桌宠（右键菜单显示用）。"""
        if not self.pet:
            return
        from llm import list_personas, PERSONA_META
        current = getattr(self.llm, "current_persona", None) or getattr(config, "PERSONA", "taozi")
        personas = []
        for name in list_personas():
            meta = PERSONA_META.get(name, {})
            personas.append({
                "name": name,
                "display": meta.get("name", name),
                "emoji": meta.get("emoji", "🎭"),
                "current": name == current,
            })
        self.pet.push({"type": "personas", "personas": personas})

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
        """生成一句主动闲聊并朗读。优先讲当地趣事/特产/历史，无则通用闲聊。"""
        try:
            from travel_notes import pick_note
            snap = telemetry_snapshot(cur)
            # 拿当前位置城市：优先目的地，其次起点（ETS2 的城市名是英文）
            city = (getattr(cur, "city_dst", "") or getattr(cur, "city_src", "") or "").strip()
            topic = pick_note(city, self._used_notes) if city else None
            text = self.llm.proactive_chat(snap, topic)
            if not text:
                return
            if topic:
                self._used_notes.add(topic)  # 记下讲过的，下次换一条
            _, clean = parse_emotion(text)  # 剥离 LLM 可能带的情绪标签
            print(f"[主动] 小满: {clean}")
            self._say(clean, "happy")
        except Exception as e:
            print(f"[主动] 闲聊失败: {e}")

    def _speak_event(self, ev):
        """把事件交给 DeepSeek 生成一句评论并朗读。"""
        try:
            comment = self.llm.comment_on_event(ev.message)
            if not comment:
                return
            self._last_activity_time = time.time()  # 事件播报也算「开口」
            _, clean = parse_emotion(comment)  # 剥离 LLM 可能带的情绪标签
            print(f"[事件:{ev.type}] 小满: {clean}")
            # 事件驱动动作：先播动作+表情（说话前起势），再朗读
            if self.pet:
                emotion = EVENT_EMOTION_MAP.get(ev.type)
                candidates = EVENT_ACTION_MAP.get(ev.type, [])
                if candidates:
                    self.pet.play_action(emotion, candidates)
            self._say(clean, EVENT_EMOTION_MAP.get(ev.type))
        except Exception as e:
            print(f"[event] 播报失败: {e}")

    # ---------- PTT ----------
    def _on_press(self, key):
        if self._busy:
            return
        if key in self._ptt_keys:
            if self.tts.is_playing():
                # 正在朗读时按热键 = 打断，而不是开始录音
                self.tts.stop()
                return
            if self.rec.is_recording():
                return  # 长按热键会触发键盘自动重复，已在录音则跳过
            self.rec.start_recording()
            # 无麦克风时 start_recording 不进入录音状态，给个提示
            if not self.rec.is_recording():
                print("[mic] 没有检测到麦克风，请接入耳机/麦克风后再试")

    def _on_release(self, key):
        if key in self._ptt_keys:
            if not self.rec.is_recording():
                return  # 没在录音就不处理（避免重复松开误触发）
            audio = self.rec.stop_recording()
            if audio is not None and len(audio) > SAMPLE_RATE // 4:  # >0.25s 才处理
                threading.Thread(target=self._handle, args=(audio,), daemon=True).start()

    # ---------- 唤醒词免按键（INPUT_MODE=auto）----------
    def _start_wakeword_loop(self):
        """后台线程：持续收音 → VAD 切句 → 转写 → 命中唤醒词进入收音态。

        命中唤醒词后进入「已唤醒」状态，持续收一句完整的话送去对话；
        对话结束后回到监听态，等下一次喊唤醒词。
        """
        self._wake_awake = False  # 是否已唤醒（等待用户说正文）
        self._wake_awake_at = 0.0  # 唤醒时刻（超时回退用）
        self._wake_stop = threading.Event()

        def _on_utterance(audio: np.ndarray):
            # 注意：本回调在 PortAudio 的音频回调线程里执行，必须尽快返回，
            # 否则会阻塞采集、漏掉音频块。所以这里只做「转发」，把重的
            # 转写/对话逻辑丢到独立线程。
            threading.Thread(target=self._process_utterance, args=(audio,), daemon=True).start()

        def _loop():
            if not self.rec.start_continuous(_on_utterance):
                print("[wake] 麦克风不可用，唤醒词模式无法启动")
                return
            print(f"[wake] 唤醒词监听已启动（说「{config.WAKEWORD or '桃子'}」唤醒）")
            while not self._wake_stop.is_set():
                self._wake_stop.wait(0.5)
            self.rec.stop_continuous()

        self._wake_thread = threading.Thread(target=_loop, daemon=True)
        self._wake_thread.start()

    def _process_utterance(self, audio: np.ndarray):
        """唤醒词监听切出一句话后的处理：转写 → 命中唤醒词进入收音态 / 否则对话。

        在独立线程执行（不阻塞音频采集）。
        """
        # 正在对话/朗读/录音时，忽略新输入（避免抢话）
        if self._busy or self.rec.is_recording() or self.tts.is_playing():
            return
        try:
            import stt
            text = stt.transcribe(audio, SAMPLE_RATE)
        except Exception as e:
            print(f"[wake] 转写失败: {e}")
            return
        if not text:
            return
        text = text.strip()
        wake = (config.WAKEWORD or "桃子").strip()
        # 唤醒后超过这么久没说话，自动回到监听态（避免一直挂「已唤醒」）
        if self._wake_awake and time.time() - self._wake_awake_at > WAKE_TIMEOUT:
            self._wake_awake = False
            print("[wake] 唤醒超时，回到监听态")
        if not self._wake_awake:
            # 监听态：只认唤醒词
            if wake and wake in text:
                self._wake_awake = True
                self._wake_awake_at = time.time()
                print(f"[wake] 听到唤醒词「{wake}」，我在听～")
        else:
            # 已唤醒：这句就是正文，直接送去对话（复用已转写文本，避免重复 STT）
            self._wake_awake = False
            self._last_activity_time = time.time()
            print(f"你说: {text}")
            threading.Thread(target=self._handle, args=(audio, text), daemon=True).start()

    # ---------- 一轮对话 ----------
    def _handle(self, audio: np.ndarray, pre_text: str | None = None):
        """一轮对话。audio=音频，pre_text=已经转写好的文本（唤醒词模式复用，避免重复 STT）。"""
        self._busy = True
        t0 = time.time()
        try:
            if pre_text is not None:
                user_text = pre_text
                t_stt_start = t_stt_end = time.time()  # 无 STT 耗时
            else:
                import stt
                t_stt_start = time.time()
                user_text = stt.transcribe(audio, SAMPLE_RATE)
                t_stt_end = time.time()
            if not user_text:
                print("（没听清，忽略）")
                return
            self._last_activity_time = time.time()  # 用户说话也算「活动」
            print(f"你说: {user_text}")

            # —— 先判断是不是语音控制指令（本地执行，不发 DeepSeek，省 token 零延迟）——
            import commands
            cmd_reply = commands.run_command(user_text)
            if cmd_reply:
                threading.Thread(target=self._say,
                                 args=(cmd_reply, "calm"), daemon=True).start()
                return

            snap = None
            if self.telemetry is not None and self.telemetry.is_connected:
                try:
                    snap = telemetry_snapshot(self.telemetry.read())
                except Exception:
                    pass

            # 收集流式回复（同时打印），整段生成完后台朗读
            print("小满: ", end="", flush=True)
            reply_parts = []
            t_llm_start = time.time()
            for delta in self.llm.chat(user_text, snap):
                reply_parts.append(delta)
                print(delta, end="", flush=True)
            t_llm_end = time.time()
            print()
            reply = "".join(reply_parts).strip()
            if reply:
                emotion, clean = parse_emotion(reply)  # 拆出情绪标签，正文朗读
                if clean:  # 有正文才朗读（避免 LLM 只输出情绪标签时的空播报）
                    threading.Thread(target=self._say,
                                     args=(clean, emotion), daemon=True).start()

            if DEBUG_TIMING:
                print(
                    f"[timing] STT {t_stt_end - t_stt_start:.2f}s | "
                    f"LLM {t_llm_end - t_llm_start:.2f}s | "
                    f"总耗时 {time.time() - t0:.2f}s"
                )
        except Exception as e:
            print(f"\n[error] {e}")
        finally:
            self._busy = False

    def run(self):
        input_mode = getattr(config, "INPUT_MODE", "auto") or "auto"
        if input_mode == "keys":
            print(f"=== ETS2 副驾 M4（按住 {config.get_ptt_key_display()} 说话，朗读中按同一键打断，Ctrl+C 退出）===")
        else:
            print(f"=== ETS2 副驾 M4（说「{config.WAKEWORD or '桃子'}」唤醒后直接说话，Ctrl+C 退出）===")
        print(f"模型: {config.DEEPSEEK_MODEL} @ {config.DEEPSEEK_BASE_URL}")
        print(f"人设: {getattr(config, 'PERSONA', 'xiaoman')}")
        from tts import engine_name
        print(f"语音: {engine_name()}（音量 {config.TTS_VOLUME:.0%}）")
        print(f"识别设备: {config.WHISPER_DEVICE}（auto 会自动探测 N 卡）")
        # 启动录音器：无麦克风也不崩溃（会打印提示，接入后自动恢复）
        self.rec.start()
        # 启动桌宠窗口（默认开启，--no-pet 关闭；启动失败自动降级为纯控制台）
        if self.pet:
            if self.pet.start():
                # 桌宠起来后，稍等它初始化完，再推人设清单（供右键菜单）
                def _push_delayed():
                    time.sleep(1.5)
                    self._push_personas()
                threading.Thread(target=_push_delayed, daemon=True).start()
        # 启动事件自动播报线程（M4）
        if self.telemetry is not None:
            self._event_thread = threading.Thread(target=self._event_loop, daemon=True)
            self._event_thread.start()
            print("[event] 事件自动播报已启动")

        # 语音输入模式：
        #   auto = 唤醒词免按键（持续监听，喊唤醒词后直接说话）
        #   keys = 按住 PTT 键说话
        input_mode = getattr(config, "INPUT_MODE", "auto") or "auto"
        if input_mode == "keys":
            with keyboard.Listener(on_press=self._on_press, on_release=self._on_release) as lis:
                try:
                    # 主循环：等桌宠「退出」命令或 Ctrl+C 触发整体退出
                    while not self._quit_event.is_set():
                        self._quit_event.wait(0.5)
                except KeyboardInterrupt:
                    pass
                finally:
                    # 统一退出清理：停事件播报、关 TTS/录音、关桌宠
                    self._event_stop.set()
                    if self.tts:
                        try:
                            self.tts.shutdown()  # 停播放并杀掉 CosyVoice 子进程，释放 GPU 显存
                        except Exception:
                            pass
                    if self.rec:
                        try:
                            self.rec.stop_recording()
                        except Exception:
                            pass
                    if self.pet:
                        self.pet.stop()
                    print("\n退出。")
        else:
            # 唤醒词免按键模式：启动唤醒词监听线程
            self._start_wakeword_loop()
            try:
                while not self._quit_event.is_set():
                    self._quit_event.wait(0.5)
            except KeyboardInterrupt:
                pass
            finally:
                self._event_stop.set()
                if hasattr(self, "_wake_stop"):
                    self._wake_stop.set()
                if self.tts:
                    try:
                        self.tts.shutdown()
                    except Exception:
                        pass
                if self.rec:
                    try:
                        self.rec.stop_continuous()
                    except Exception:
                        pass
                if self.pet:
                    self.pet.stop()
                print("\n退出。")


if __name__ == "__main__":
    CopilotApp().run()
