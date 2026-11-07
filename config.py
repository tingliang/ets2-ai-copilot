"""极简 .env 加载器（避免额外依赖 python-dotenv）。"""
import os


def load_env(path: str = None) -> None:
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


load_env()

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
# 单次回答的 token 上限。调大能让副驾聊游戏时讲得更完整，但生成更慢、更耗 token。
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "256"))
PERSONA = os.environ.get("PERSONA", "taozi")  # 人设名，对应 personas/<name>.md
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
# STT 设备：auto（自动探测，有 N 卡用 cuda，否则 cpu）/ cpu / cuda
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "auto")
PTT_KEY = os.environ.get("PTT_KEY", "alt")  # 按住说话热键，见下方支持列表

# ---------- 语音输入模式（免按键 / 按键）----------
# INPUT_MODE:
#   auto  = 唤醒词免按键（默认）：持续监听，喊「WAKEWORD」后进入收音，说完整句自动识别。
#           适合开车时不想腾手按 Alt。
#   keys  = 按住说话（传统 PTT）：按住 PTT_KEY 说话、松开发送。
INPUT_MODE = os.environ.get("INPUT_MODE", "auto").strip().lower()
# 唤醒词（仅 INPUT_MODE=auto 生效），喊这个词唤醒副驾开始听你说话
WAKEWORD = os.environ.get("WAKEWORD", "桃子").strip()

# ---------- 桌宠 ----------
# 是否在启动时弹出桌宠窗口（Live2D 副驾形象）。true/1=开启（默认），false/0=关闭。
# 命令行 --no-pet 可临时覆盖为关闭。
ENABLE_PET = os.environ.get("ENABLE_PET", "1").strip().lower() in ("1", "true", "yes", "on")

# ---------- TTS 三引擎 ----------
# TTS_ENGINE:
#   sherpa    —— 本地离线中文女声（vits-melo-tts-zh_en），国内零依赖、断网可用（默认，推荐）
#   cosyvoice —— CosyVoice2 自然情感语音（需先跑 setup_cosyvoice.py 装独立环境 + 模型，
#                自然度最高、支持情绪控制；子进程起不来自动回退 sherpa）
#   edge      —— 微软 edge-tts「陕西小妮」（需联网，境外接口，国内可能不稳定）
TTS_ENGINE = os.environ.get("TTS_ENGINE", "sherpa").strip().lower()
TTS_VOICE = os.environ.get("TTS_VOICE", "zh-CN-shaanxi-XiaoniNeural")  # 仅 edge 引擎生效
TTS_RATE = os.environ.get("TTS_RATE", "+0%")   # 仅 edge 引擎生效，如 "+20%" / "-10%"
TTS_LOCAL_MODEL_DIR = os.environ.get("TTS_LOCAL_MODEL_DIR", "")  # sherpa 模型目录（空=默认 models/vits-melo-tts-zh_en）
TTS_LOCAL_SPEED = float(os.environ.get("TTS_LOCAL_SPEED", "1.0"))  # 语速，越大越快（sherpa 引擎直接生效）
TTS_LOCAL_LEAD_SILENCE = float(os.environ.get("TTS_LOCAL_LEAD_SILENCE", "0.12"))  # 句首静音秒数（防首字被吞）
TTS_VOLUME = float(os.environ.get("TTS_VOLUME", "1.0"))  # 音量，可 >1.0 放大（自动防削波）
# 按句流式朗读：第一句合成完就开播、边播边合成后续句，显著降低「听到声音」的等待。
# true=流式（默认），false=整段合成后一次播放。仅 sherpa 引擎生效。
TTS_STREAM = os.environ.get("TTS_STREAM", "true").strip().lower() in ("1", "true", "yes", "on")

# ---------- CosyVoice2（第三引擎）----------
# 子进程 HTTP 服务地址；主程序用它合成，起不来自动回退 sherpa
COSYVOICE_HOST = os.environ.get("COSYVOICE_HOST", "127.0.0.1")
COSYVOICE_PORT = int(os.environ.get("COSYVOICE_PORT", "9860"))
COSYVOICE_MODEL_DIR = os.environ.get("COSYVOICE_MODEL_DIR", "")  # 空=默认 models/CosyVoice2-0.5B
COSYVOICE_DEVICE = os.environ.get("COSYVOICE_DEVICE", "auto")  # auto/cuda/cpu
# 零样本音色克隆：换一段参考音频即可换音色（不用重新训练）。
# COSYVOICE_PROMPT_WAV  = 参考音频路径（3~10 秒、清晰、单说话人；空=默认童声，见下）
# COSYVOICE_PROMPT_TEXT = 参考音频里**逐字对应**的文本（中文参考音频必须一致，否则会乱念）
#   参考音频若不是中文（英文/日文等），**留空 COSYVOICE_PROMPT_TEXT** 即可，
#   程序会自动切到跨语言克隆（cross-lingual，只借音色、不借内容）。
# 默认参考音频：voices/kid_voice.wav（用户儿子童声，语速较快的独立录音）。
# 若想回到官方成人女声，把 .env 里 COSYVOICE_PROMPT_WAV 和 COSYVOICE_PROMPT_TEXT
# 都显式设为空字符串（空字符串会覆盖默认值，让子进程回退到官方 zero_shot_prompt.wav）。
_COSYVOICE_DEFAULT_PROMPT_WAV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "voices", "kid_voice.wav")
_COSYVOICE_DEFAULT_PROMPT_TEXT = "今天是星期六，我要去太原了，我很开心"
COSYVOICE_PROMPT_WAV = os.environ.get("COSYVOICE_PROMPT_WAV", _COSYVOICE_DEFAULT_PROMPT_WAV)
COSYVOICE_PROMPT_TEXT = os.environ.get("COSYVOICE_PROMPT_TEXT", _COSYVOICE_DEFAULT_PROMPT_TEXT)
# 是否启用情绪控制（把 happy/angry/sad 等映射成 CosyVoice 的语气指令）
COSYVOICE_EMOTION = os.environ.get("COSYVOICE_EMOTION", "true").strip().lower() in ("1", "true", "yes", "on")
# CosyVoice 语速（独立于 sherpa 的 TTS_LOCAL_SPEED）。流式模式下官方 speed 参数
# 无效，这里用后处理时间拉伸实现变速。默认 1.0（参考音频本身语速已较快）。
# 若仍觉得慢，可调到 1.1~1.3（后处理变速，会轻微变调）。
COSYVOICE_SPEED = float(os.environ.get("COSYVOICE_SPEED", "1.0"))


# ---------- 热键解析 ----------
# 把 PTT_KEY 字符串映射到 pynput 的按键对象。支持：
#   功能键: alt / ctrl / shift / space / enter / tab / esc / backspace ...
#   字母键: 直接写小写字母，如 "a" / "f" / "t" ...
def get_ptt_keys():
    """返回 pynput 的 key 对象元组，用于等值比较；pynput 不可用时返回 None。"""
    try:
        from pynput import keyboard as kb
    except Exception:
        return None
    s = (PTT_KEY or "").strip().lower()
    special = {
        "alt": (kb.Key.alt, kb.Key.alt_l, kb.Key.alt_r),
        "ctrl": (kb.Key.ctrl, kb.Key.ctrl_l, kb.Key.ctrl_r),
        "shift": (kb.Key.shift, kb.Key.shift_l, kb.Key.shift_r),
        "space": (kb.Key.space,),
        "enter": (kb.Key.enter,),
        "tab": (kb.Key.tab,),
        "esc": (kb.Key.esc,),
        "backspace": (kb.Key.backspace,),
        "caps_lock": (kb.Key.caps_lock,),
        "caps": (kb.Key.caps_lock,),
    }
    if s in special:
        return special[s]
    if len(s) == 1 and s.isalpha():
        return (s,)
    return special["alt"]


def get_ptt_key_display() -> str:
    """返回热键的显示名（用于日志）。"""
    return (PTT_KEY or "alt").strip()
