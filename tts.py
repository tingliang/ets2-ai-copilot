"""
中文朗读（TTS）：双引擎。

- sherpa 引擎（默认）：本地离线中文女声（vits-melo-tts-zh_en），完全离线、
  国内零依赖、零成本。模型从 ModelScope 魔搭直链下载（约 190MB）。
- edge 引擎（可选）：微软 edge-tts「陕西小妮」整段合成，需联网（境外接口）。

通过 .env 的 TTS_ENGINE 切换（sherpa / edge）。

设计：
- 整段文字合成一次 → 播放（简单可靠）。
- 播放用 sounddevice，可被 stop() 打断（带淡出）。
- sherpa 引擎用 numpy + wave 写 wav 后播放，不依赖 ffmpeg。
- edge 引擎沿用原 mp3 解码方案（依赖 ffmpeg）。

依赖：
- sherpa 引擎：sherpa-onnx + numpy + requests + sounddevice
- edge 引擎：edge-tts + sounddevice + numpy + ffmpeg
"""
from __future__ import annotations

import asyncio
import io
import os
import re
import subprocess
import tempfile
import threading
import wave

import numpy as np
import sounddevice as sd

import config

_sample_rate = 24000  # edge 引擎播放采样率（edge-tts 输出 24k）


def _apply_volume(pcm: np.ndarray) -> np.ndarray:
    """按 config.TTS_VOLUME 缩放音量。1.0=原声，>1.0 放大、<1.0 减小，自动防削波。"""
    vol = config.TTS_VOLUME
    if vol == 1.0:
        return pcm
    return np.clip(pcm * vol, -1.0, 1.0).astype(np.float32)


def _apply_fixed_gain(pcm: np.ndarray, gain: float) -> np.ndarray:
    """统一固定增益缩放。

    注意：这里刻意**不做逐句峰值归一化**。逐句归一化会把每句都拉到同一峰值，
    但短句/长句的感知响度（RMS）不同，结果是一句响一句轻（忽高忽低）。
    改成固定增益后，所有句子保持 VITS 原始的相对响度，句间自然一致。
    """
    if gain <= 1.0:
        return pcm
    return np.clip(pcm * gain, -1.0, 1.0).astype(np.float32)


def _split_sentences(text: str) -> list[str]:
    """按句末标点拆句，并把「单字/极短句」归并到相邻句，避免单独合成听不见。

    VITS 对「行。」这类单字句合成出的峰值极低（约 0.02，几乎静音），
    所以拆句后要把过短的句子合并到前一句，让它有上下文音量。
    """
    # 按句末标点切分，保留标点
    parts = re.split(r"(?<=[。！？!?；;])", text)
    parts = [p.strip() for p in parts if p.strip()]

    merged: list[str] = []
    for p in parts:
        # 去掉标点后太短（≤2 字）的句子，归并到上一句
        core = re.sub(r"[。！？!?；;，,、\s]", "", p)
        if len(core) <= 2 and merged:
            merged[-1] = merged[-1] + p
        else:
            merged.append(p)
    return merged


def _postprocess(pcm: np.ndarray, sample_rate: int, gain: float) -> np.ndarray:
    """句首静音 + 固定增益 + 极短句温和兜底。

    极短句（如单字「行」「嗯」）VITS 合成峰值极低（约 0.02），
    即使固定增益也几乎无音可放。这里对极短音频额外补一个**温和**的增益
    （最多再 2 倍，而不是强拉到满幅），保证短句听得清又不至于突然变响。
    """
    lead = int(sample_rate * config.TTS_LOCAL_LEAD_SILENCE)
    if lead > 0:
        pcm = np.concatenate([np.zeros(lead, dtype=np.float32), pcm])

    pcm = _apply_fixed_gain(pcm, gain)

    # 极短句兜底：时长 < 0.5s 且固定增益后仍很弱，再温和补偿
    dur = len(pcm) / sample_rate
    if dur < 0.5:
        peak = float(np.max(np.abs(pcm)))
        if peak < 0.4:
            extra = min(0.4 / max(peak, 1e-4), 2.0)
            pcm = np.clip(pcm * extra, -1.0, 1.0).astype(np.float32)
    return pcm


# ==================== sherpa 本地引擎 ====================

_MODEL_ID = "liaowenbin/vits-melo-tts-zh_en"   # ModelScope 仓库 ID
_MODEL_BASE_URL = f"https://modelscope.cn/models/{_MODEL_ID}/resolve/master"

# 完整文件清单（缺失 dict/ 会导致中文分词失败、发音错误）
_MODEL_FILES = [
    "model.onnx",
    "lexicon.txt",
    "tokens.txt",
    "phone.fst",
    "date.fst",
    "number.fst",
    "new_heteronym.fst",
    "dict/hmm_model.utf8",
    "dict/idf.utf8",
    "dict/jieba.dict.utf8",
    "dict/stop_words.utf8",
    "dict/user.dict.utf8",
    "dict/pos_dict/char_state_tab.utf8",
    "dict/pos_dict/prob_emit.utf8",
    "dict/pos_dict/prob_start.utf8",
    "dict/pos_dict/prob_trans.utf8",
]


def _default_model_dir() -> str:
    if config.TTS_LOCAL_MODEL_DIR:
        return config.TTS_LOCAL_MODEL_DIR
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "models", "vits-melo-tts-zh_en")


def _download_file(url: str, dest: str, desc: str = "") -> bool:
    """下载单个文件；已存在且大小一致则跳过。返回是否成功。"""
    import requests

    if os.path.exists(dest):
        try:
            head = requests.head(url, timeout=15)
            total = int(head.headers.get("Content-Length", 0))
            if total and os.path.getsize(dest) == total:
                return True
        except Exception:
            pass

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    for attempt in range(3):
        try:
            r = requests.get(url, stream=True, timeout=30)
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0))
            done = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = done * 100 // total
                        print(f"\r    [模型下载] {desc} {pct}%", end="", flush=True)
            print(f"\r    [模型下载] {desc} 完成    ", flush=True)
            return True
        except Exception as e:
            print(f"\r    [模型下载] {desc} 失败({attempt+1}/3): {e}", flush=True)
            import time
            time.sleep(1)
    return False


def _ensure_model() -> str | None:
    """确保本地模型完整。返回模型目录，失败返回 None。"""
    model_dir = _default_model_dir()
    key_files = [
        os.path.join(model_dir, "model.onnx"),
        os.path.join(model_dir, "lexicon.txt"),
        os.path.join(model_dir, "tokens.txt"),
        os.path.join(model_dir, "dict", "jieba.dict.utf8"),
    ]
    if all(os.path.exists(f) for f in key_files):
        return model_dir

    print(f"[tts] 本地模型缺失，从 ModelScope 下载到：{model_dir}")
    print(f"[tts] 首次约 190MB，请耐心等待……")
    for path in _MODEL_FILES:
        url = f"{_MODEL_BASE_URL}/{path}"
        dest = os.path.join(model_dir, path.replace("/", os.sep))
        if not _download_file(url, dest, desc=path):
            print(f"[tts] 模型下载失败：{path}")
            print(f"[tts] 可手动下载整包：https://modelscope.cn/models/{_MODEL_ID}")
            print(f"[tts] 解压到 {model_dir}")
            return None
    print("[tts] 模型下载完成 ✓")
    return model_dir


class _SherpaTTS:
    """本地离线 sherpa-onnx 引擎。"""

    def __init__(self):
        import sherpa_onnx
        self._sherpa_onnx = sherpa_onnx
        model_dir = _ensure_model()
        if not model_dir:
            raise RuntimeError("本地 TTS 模型不可用")

        model_path = os.path.join(model_dir, "model.onnx")
        lexicon_path = os.path.join(model_dir, "lexicon.txt")
        tokens_path = os.path.join(model_dir, "tokens.txt")

        fst_order = ["phone.fst", "date.fst", "number.fst", "new_heteronym.fst"]
        fsts = [os.path.join(model_dir, f) for f in fst_order
                if os.path.exists(os.path.join(model_dir, f))]
        for name in sorted(os.listdir(model_dir)):
            if name.endswith(".fst") and name not in fst_order:
                fsts.append(os.path.join(model_dir, name))

        tts_config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=model_path,
                    lexicon=lexicon_path,
                    tokens=tokens_path,
                ),
                provider="cpu",
                num_threads=2,
                debug=False,
            ),
            rule_fsts=",".join(fsts) if fsts else "",
            max_num_sentences=1,
        )
        if not tts_config.validate():
            raise ValueError("TTS 配置校验失败，请检查模型文件是否完整")

        self._tts = sherpa_onnx.OfflineTts(tts_config)
        self.sample_rate = 44100
        # 校准一次固定增益（之后所有句子都用它，避免逐句归一化导致音量忽高忽低）
        self._gain = self._calibrate_gain()

    def _calibrate_gain(self) -> float:
        """合成一段中等长度文本，算出固定增益（目标峰值 0.75，增益上限 6.0）。"""
        try:
            probe = self.synthesize_raw("嗯，好的，我看看现在的情况。")
        except Exception:
            return 4.0  # 典型增益兜底（VITS 峰值约 0.18 → 0.75 需约 4.2 倍）
        if len(probe) == 0:
            return 0.75
        peak = float(np.max(np.abs(probe)))
        if peak < 1e-6:
            return 0.75
        return min(0.75 / peak, 6.0)

    def synthesize_raw(self, text: str) -> np.ndarray:
        """裸合成，返回 float32 PCM（单声道，44.1k），不做任何后处理。"""
        try:
            gen = self._sherpa_onnx.GenerationConfig()
            gen.speed = config.TTS_LOCAL_SPEED
            audio = self._tts.generate(text, gen)
        except (AttributeError, TypeError):
            audio = self._tts.generate(text, sid=0, speed=config.TTS_LOCAL_SPEED)
        return np.asarray(audio.samples, dtype=np.float32)

    def synthesize(self, text: str) -> np.ndarray:
        """合成文本，返回 float32 PCM（单声道，44.1k）。

        做了两处后处理：
        1. 句首垫一小段静音（config.TTS_LOCAL_LEAD_SILENCE），
           避免播放设备起音延迟把第一个字的音头吞掉。
        2. 响度归一化，对齐 edge 引擎音量。
        """
        pcm = self.synthesize_raw(text)
        return _postprocess(pcm, self.sample_rate, self._gain)


# ==================== CosyVoice2 引擎（独立子进程 HTTP） ====================

class _CosyVoiceTTS:
    """CosyVoice2 引擎：通过 HTTP 调独立子进程（cosyvoice_server.py）合成。

    子进程由 TTS 类在启用 cosyvoice 时拉起；本类只负责：
    1. 等子进程 /health 就绪（带超时）
    2. POST /tts 拿 WAV，转成 float32 PCM（24k 单声道）
    3. 子进程不可用抛异常，由上层回退 sherpa
    """

    def __init__(self):
        import requests
        self._requests = requests
        self._host = config.COSYVOICE_HOST
        self._port = config.COSYVOICE_PORT
        self.sample_rate = 24000  # CosyVoice2 默认 24k

    @property
    def _url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def wait_ready(self, timeout: float = 120.0) -> bool:
        """等子进程就绪（模型加载要几秒~十几秒）。返回是否就绪。"""
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = self._requests.get(f"{self._url}/health", timeout=2)
                if r.status_code == 200 and r.json().get("ok"):
                    sr = r.json().get("sample_rate")
                    if sr:
                        self.sample_rate = int(sr)  # 以子进程实际采样率为准
                    return True
            except Exception:
                pass
            time.sleep(1.0)
        return False

    def synthesize(self, text: str, emotion: str | None = None) -> np.ndarray:
        """合成文本，返回 float32 PCM（单声道 24k）。失败抛异常。"""
        body = {"text": text}
        if emotion and getattr(config, "COSYVOICE_EMOTION", True):
            body["emotion"] = emotion
        body["speed"] = getattr(config, "TTS_LOCAL_SPEED", 1.0)

        r = self._requests.post(f"{self._url}/tts", json=body, timeout=60)
        r.raise_for_status()
        # 解析 WAV -> float32 PCM
        import io as _io
        import wave as _wave
        with _wave.open(_io.BytesIO(r.content), "rb") as w:
            assert w.getnchannels() == 1, "CosyVoice 返回了多声道音频"
            assert w.getframerate() == self.sample_rate, "CosyVoice 采样率不一致"
            data = w.readframes(w.getnframes())
        pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        return pcm

    def synthesize_stream(self, text: str, emotion: str | None = None):
        """流式合成：逐 chunk yield float32 PCM（单声道 24k）。

        每个 chunk 是增量音频（服务端 stream=True 产出），客户端按序拼接播放
        即可实现边生成边播放，首字延迟降到 ~150ms-1s。
        """
        import struct
        import io as _io
        import wave as _wave
        body = {"text": text}
        if emotion and getattr(config, "COSYVOICE_EMOTION", True):
            body["emotion"] = emotion

        r = self._requests.post(f"{self._url}/tts_stream", json=body, stream=True, timeout=120)
        r.raise_for_status()
        buf = b""
        while True:
            # 读取 4 字节长度头
            while len(buf) < 4:
                chunk = r.raw.read(4 - len(buf))
                if not chunk:
                    return
                buf += chunk
            (n,) = struct.unpack(">I", buf[:4])
            buf = buf[4:]
            if n == 0:
                # 0-length 帧 = 服务端错误信号，终止
                return
            while len(buf) < n:
                chunk = r.raw.read(n - len(buf))
                if not chunk:
                    return
                buf += chunk
            wav_bytes = buf[:n]
            buf = buf[n:]
            # 解析 WAV chunk -> float32 PCM
            with _wave.open(_io.BytesIO(wav_bytes), "rb") as w:
                data = w.readframes(w.getnframes())
            pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            yield pcm


# 保存 CosyVoice 子进程句柄，退出时据此杀掉它并释放 GPU 显存。
# 之前这里 Popen 后就把句柄丢了，导致 Ctrl+C 退出后子进程仍驻留，
# 显存（约 5GB）一直不释放。
_cosyvoice_proc = None


def _start_cosyvoice_server() -> bool:
    """拉起 cosyvoice_server.py 子进程（若未运行）。返回是否已在运行/成功拉起。

    优先用独立 venv（setup_cosyvoice.py 装的），找不到就退回当前解释器。
    这里只负责把子进程拉起来，具体模型加载由子进程自己完成（异步）。
    """
    global _cosyvoice_proc
    import subprocess as sp
    import sys as _sys

    root = os.path.dirname(os.path.abspath(__file__))
    server = os.path.join(root, "cosyvoice_server.py")

    # 先探测服务是否已经在跑（幂等，避免重复拉起）
    import requests as _req
    try:
        r = _req.get(f"http://{config.COSYVOICE_HOST}:{config.COSYVOICE_PORT}/health", timeout=2)
        if r.status_code == 200:
            return True
    except Exception:
        pass

    # 选解释器：优先独立 venv
    python = _sys.executable
    venv_py = os.path.join(root, "venv_cosyvoice", "Scripts", "python.exe")  # Windows
    if not os.path.exists(venv_py):
        venv_py = os.path.join(root, "venv_cosyvoice", "bin", "python")  # Linux/mac
    if os.path.exists(venv_py):
        python = venv_py

    model_dir = config.COSYVOICE_MODEL_DIR or os.path.join(root, "models", "CosyVoice2-0.5B")
    if not os.path.isdir(model_dir):
        print(f"[tts] CosyVoice 模型目录不存在：{model_dir}")
        print("[tts] 请先运行 python setup_cosyvoice.py 安装模型")
        return False

    try:
        proc = sp.Popen(
            [python, server, "--model-dir", model_dir, "--device", config.COSYVOICE_DEVICE],
            cwd=root,
            # 子进程 stdout/stderr 继承，日志直接打到主程序控制台
            stdout=None, stderr=None,
        )
        _cosyvoice_proc = proc
        print(f"[tts] 已拉起 CosyVoice 子进程（{python}）")
        return True
    except Exception as e:
        print(f"[tts] 拉起 CosyVoice 子进程失败: {e}")
        return False


def _stop_cosyvoice_server():
    """停掉 CosyVoice 子进程，释放其占用的 GPU 显存。

    优先走 HTTP /shutdown（即使子进程不是本次会话拉起的——比如上一次异常退出
    留下的孤儿——也能通过端口把它停掉）；若还持有句柄则 terminate() 兜底强杀。
    注意 terminate()/kill() 只是发信号，必须再 wait() 才能回收句柄。
    """
    global _cosyvoice_proc
    import requests as _req

    # 1) 优雅关停：HTTP /shutdown 对「孤儿进程」同样有效
    try:
        _req.post(
            f"http://{config.COSYVOICE_HOST}:{config.COSYVOICE_PORT}/shutdown",
            timeout=3,
        )
    except Exception:
        pass

    # 2) 兜底：直接杀掉本会话拉起的子进程
    proc = _cosyvoice_proc
    _cosyvoice_proc = None
    if proc is None:
        return
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
        return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


# ==================== edge 引擎 ====================

def synthesize_edge(text: str, rate: str = None) -> bytes:
    """用 edge-tts 合成一段文字，返回 mp3 字节。"""
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
        os.unlink(mp3_path)


# ==================== 统一 TTS 类 ====================

class TTS:
    """整段朗读，三引擎（sherpa/cosyvoice/edge）。支持打断。"""

    def __init__(self):
        self._playing = False
        self._stop_flag = threading.Event()
        self._play_lock = threading.Lock()
        self._current_pcm = None
        self._stream = None  # 流式播放时持有的 OutputStream（供打断用）
        self._engine = config.TTS_ENGINE
        self._sherpa = None
        self._cosyvoice = None

        if self._engine == "cosyvoice":
            self._init_cosyvoice()
        elif self._engine == "edge":
            self._sample_rate = _sample_rate
        else:
            # 默认 sherpa 本地引擎；加载失败自动回退 edge
            try:
                self._sherpa = _SherpaTTS()
                self._sample_rate = self._sherpa.sample_rate
            except Exception as e:
                print(f"[tts] 本地 sherpa 引擎不可用（{e}）")
                print("[tts] 回退到 edge-tts（需联网）")
                self._sherpa = None
                self._engine = "edge"
                self._sample_rate = _sample_rate

    def _init_cosyvoice(self):
        """启用 CosyVoice2：拉起子进程并等它就绪；失败回退 sherpa。"""
        print("[tts] 正在启用 CosyVoice2（自然情感语音）…")
        if not _start_cosyvoice_server():
            self._fallback_sherpa("CosyVoice 子进程启动失败")
            return
        try:
            cv = _CosyVoiceTTS()
            if not cv.wait_ready(timeout=120):
                raise TimeoutError("CosyVoice 子进程就绪超时")
            self._cosyvoice = cv
            self._sample_rate = cv.sample_rate
            print("[tts] CosyVoice2 就绪 ✓")
        except Exception as e:
            self._cosyvoice = None
            self._fallback_sherpa(f"CosyVoice 不可用（{e}）")

    def _fallback_sherpa(self, reason: str):
        """回退到 sherpa；sherpa 也不可用则回退 edge。"""
        print(f"[tts] {reason}，回退到 sherpa 本地引擎")
        try:
            self._sherpa = _SherpaTTS()
            self._sample_rate = self._sherpa.sample_rate
            self._engine = "sherpa"
        except Exception as e:
            print(f"[tts] sherpa 也不可用（{e}），回退 edge-tts")
            self._sherpa = None
            self._engine = "edge"
            self._sample_rate = _sample_rate

    def stop(self):
        """打断当前播放，带平滑淡出。"""
        self._stop_flag.set()
        # 若正在流式播放，立即中断输出流（不等待缓冲播完）
        stream = self._stream
        if stream is not None:
            try:
                stream.abort()
                stream.close()
            except Exception:
                pass
            self._stream = None
        pcm = self._current_pcm
        if pcm is not None and len(pcm) > 0:
            try:
                fade_n = int(self._sample_rate * 0.12)
                tail = pcm[-fade_n:] if len(pcm) > fade_n else pcm
                env = np.linspace(1.0, 0.0, len(tail), dtype=np.float32)
                sd.play(_apply_volume(tail) * env, self._sample_rate)
                sd.wait()
            except Exception:
                pass
        sd.stop()

    def shutdown(self):
        """退出时统一清理：停播放 + 杀掉 CosyVoice 子进程释放 GPU 显存。

        在 main.py 的退出路径（Ctrl+C / 桌宠右键退出）调用。若不杀子进程，
        cosyvoice_server.py 会继续驻留并占着几 GB 显存，表现为「退出后显存没释放」。
        """
        self.stop()
        _stop_cosyvoice_server()

    def is_playing(self) -> bool:
        return self._playing

    def _speak_sherpa_stream(self, text: str):
        """按句流式：逐句「合成 → 写入输出流」，第一句最快出声，句间无缝不中断。

        重要：sounddevice 的 sd.play(blocking=False) 每次调用都会先 stop() 掉
        上一个流（源码注释明确「cannot be used for multiple overlapping playbacks」），
        所以不能用多次 sd.play() 来排队——那样前一句会被下一句打断，
        表现为「一段话说一半就没了、直接掉到下一句」。
        正确做法：自建一个 OutputStream，逐句 stream.write()，数据按顺序真正串行播放。
        """
        sents = _split_sentences(text)
        if not sents:
            return None

        # 清掉上一次可能残留的播放
        sd.stop()

        import time
        first_ready = None  # 第一句写入成功的时间（用于统计首句延迟）

        stream = sd.OutputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="float32",
        )
        self._stream = stream
        try:
            stream.start()
        except Exception as e:
            # 打开流失败（极少见），退化为整段合成后一次播放
            print(f"[tts] 流式播放打开失败，回退整段: {e}")
            self._stream = None
            try:
                stream.close()
            except Exception:
                pass
            self._speak_sherpa_whole(text)
            return None

        try:
            for s in sents:
                if self._stop_flag.is_set():
                    break
                pcm = self._sherpa.synthesize(s)
                if self._stop_flag.is_set():
                    break
                self._current_pcm = pcm
                # write() 会阻塞到数据被消费，播放是按序连续的（真正的 FIFO）
                data = np.ascontiguousarray(_apply_volume(pcm)).reshape(-1, 1)
                stream.write(data)
                if first_ready is None:
                    first_ready = time.time()
        except Exception:
            # 被 stop() 打断（abort/close）或设备异常，正常退出
            pass
        finally:
            if self._stop_flag.is_set():
                # 用户打断：立即丢弃剩余缓冲
                try:
                    stream.abort()
                except Exception:
                    pass
            else:
                # 正常播完：stop() 会等待所有 pending 缓冲播完再返回
                try:
                    stream.stop()
                except Exception:
                    pass
            try:
                stream.close()
            except Exception:
                pass
            if self._stream is stream:
                self._stream = None
        return first_ready

    def _speak_sherpa_whole(self, text: str):
        """整段合成后一次播放（流式不可用时的回退）。"""
        pcm = self._sherpa.synthesize(text)
        if self._stop_flag.is_set():
            return
        sd.stop()
        self._current_pcm = pcm
        sd.play(_apply_volume(pcm), self._sample_rate)
        sd.wait()

    def _speak_cosyvoice_stream(self, text: str, emotion: str | None = None):
        """CosyVoice token 级流式：边合成边写入输出流，首字延迟 ~150ms-1s。

        服务端 /tts_stream 用 stream=True 产出增量 chunk，这里逐 chunk 写入
        同一个 OutputStream，实现真正的边生成边播放（不是按句，是按 token）。
        """
        sd.stop()

        import time
        first_ready = None

        stream = sd.OutputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="float32",
        )
        self._stream = stream
        try:
            stream.start()
        except Exception as e:
            print(f"[tts] 流式播放打开失败，回退整段: {e}")
            self._stream = None
            try:
                stream.close()
            except Exception:
                pass
            self._speak_cosyvoice_whole(text, emotion)
            return None

        try:
            for pcm in self._cosyvoice.synthesize_stream(text, emotion):
                if self._stop_flag.is_set():
                    break
                self._current_pcm = pcm
                data = np.ascontiguousarray(_apply_volume(pcm)).reshape(-1, 1)
                stream.write(data)
                if first_ready is None:
                    first_ready = time.time()
        except Exception:
            # 被 stop() 打断或流式连接中断，正常退出
            pass
        finally:
            if self._stop_flag.is_set():
                try:
                    stream.abort()
                except Exception:
                    pass
            else:
                try:
                    stream.stop()
                except Exception:
                    pass
            try:
                stream.close()
            except Exception:
                pass
            if self._stream is stream:
                self._stream = None
        return first_ready

    def _speak_cosyvoice_whole(self, text: str, emotion: str | None = None):
        """整段合成后一次播放（流式不可用时的回退）。"""
        pcm = self._cosyvoice.synthesize(text, emotion)
        if self._stop_flag.is_set():
            return
        pcm = _postprocess(pcm, self._sample_rate, 1.0)
        sd.stop()
        self._current_pcm = pcm
        sd.play(_apply_volume(pcm), self._sample_rate)
        sd.wait()

    def speak_text(self, text: str, emotion: str | None = None):
        """朗读文本（阻塞直到读完）。默认按句流式，可配置整段。

        emotion：情绪 key（happy/angry/sad/surprised/calm），
        cosyvoice 引擎用它做情感语气控制，其它引擎忽略。
        """
        text = text.strip()
        if not text:
            return
        import sys
        import time
        _debug_timing = "--debug-timing" in sys.argv
        t_synth_start = time.time()
        self._playing = True
        self._stop_flag.clear()
        try:
            use_stream = getattr(config, "TTS_STREAM", True) and self._engine == "sherpa"
            if self._engine == "edge":
                mp3 = synthesize_edge(text)
                if self._stop_flag.is_set():
                    return
                pcm = _mp3_to_pcm(mp3)
            elif self._engine == "cosyvoice" and self._cosyvoice is not None:
                # CosyVoice token 级流式：边合成边播放，首字延迟最低。
                first_ready = self._speak_cosyvoice_stream(text, emotion)
                if _debug_timing:
                    t_done = time.time()
                    if first_ready:
                        print(f"[timing] TTS首字出声 {first_ready - t_synth_start:.2f}s | "
                              f"整段播完 {t_done - t_synth_start:.2f}s（流式）")
                    else:
                        print(f"[timing] TTS流式播完 {t_done - t_synth_start:.2f}s")
                return
            elif use_stream:
                first_ready = self._speak_sherpa_stream(text)
                if _debug_timing:
                    t_done = time.time()
                    if first_ready:
                        print(f"[timing] TTS首句出声 {first_ready - t_synth_start:.2f}s | "
                              f"整段播完 {t_done - t_synth_start:.2f}s（流式）")
                    else:
                        print(f"[timing] TTS流式播完 {t_done - t_synth_start:.2f}s")
                return
            else:
                pcm = self._sherpa.synthesize(text)
            t_synth_end = time.time()
            if _debug_timing:
                print(f"[timing] TTS合成 {t_synth_end - t_synth_start:.2f}s")
            if self._stop_flag.is_set():
                return
            sd.stop()
            with self._play_lock:
                if self._stop_flag.is_set():
                    return
                self._current_pcm = pcm
                sd.play(_apply_volume(pcm), self._sample_rate)
                sd.wait()
        except Exception as e:
            print(f"[tts] 合成/播放失败: {e}")
        finally:
            self._current_pcm = None
            self._playing = False


def engine_name() -> str:
    """返回当前引擎名（供 main.py 打印）。"""
    if config.TTS_ENGINE == "cosyvoice":
        return "cosyvoice（自然情感语音）"
    if config.TTS_ENGINE == "edge":
        return "edge（陕西小妮，需联网）"
    return "sherpa（本地离线女声）"


if __name__ == "__main__":
    print("TTS 测试（整段朗读）：")
    print(f"引擎: {engine_name()}")
    t = TTS()
    t.speak_text("哎哟，就这车技还跑长途呢，回去重考本吧！")
    print("完成。")
