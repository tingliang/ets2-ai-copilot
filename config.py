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
PERSONA = os.environ.get("PERSONA", "xiaoman")  # 人设名，对应 personas/<name>.md
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
# STT 设备：auto（自动探测，有 N 卡用 cuda，否则 cpu）/ cpu / cuda
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "auto")
PTT_KEY = os.environ.get("PTT_KEY", "alt")  # 按住说话热键，见下方支持列表

# ---------- TTS 双引擎 ----------
# TTS_ENGINE:
#   sherpa —— 本地离线中文女声（vits-melo-tts-zh_en），国内零依赖、断网可用（默认，推荐）
#   edge   —— 微软 edge-tts「陕西小妮」（需联网，境外接口，国内可能不稳定）
TTS_ENGINE = os.environ.get("TTS_ENGINE", "sherpa").strip().lower()
TTS_VOICE = os.environ.get("TTS_VOICE", "zh-CN-shaanxi-XiaoniNeural")  # 仅 edge 引擎生效
TTS_RATE = os.environ.get("TTS_RATE", "+0%")   # 仅 edge 引擎生效，如 "+20%" / "-10%"
TTS_LOCAL_MODEL_DIR = os.environ.get("TTS_LOCAL_MODEL_DIR", "")  # sherpa 模型目录（空=默认 models/vits-melo-tts-zh_en）
TTS_LOCAL_SPEED = float(os.environ.get("TTS_LOCAL_SPEED", "1.0"))  # sherpa 语速，越大越快
TTS_LOCAL_LEAD_SILENCE = float(os.environ.get("TTS_LOCAL_LEAD_SILENCE", "0.12"))  # 句首静音秒数（防首字被吞）
TTS_VOLUME = float(os.environ.get("TTS_VOLUME", "1.0"))  # 音量，可 >1.0 放大（自动防削波）


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
