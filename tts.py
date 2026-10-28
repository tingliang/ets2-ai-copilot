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


def _normalize_loudness(pcm: np.ndarray, target_peak: float = 0.75, max_gain: float = 6.0) -> np.ndarray:
    """响度归一化：把峰值拉到 target_peak（防太小声/太大声），但增益不超过 max_gain。

    离线 VITS 输出响度偏低（峰值约 0.18），这里归一化到接近满幅，让音量对齐 edge 引擎。
    """
    if len(pcm) == 0:
        return pcm
    peak = float(np.max(np.abs(pcm)))
    if peak < 1e-6:
        return pcm
    gain = min(target_peak / peak, max_gain)
    return (pcm * gain).astype(np.float32)


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


def _postprocess(pcm: np.ndarray, sample_rate: int) -> np.ndarray:
    """句首静音 + 响度归一化 + 极短句兜底增益。

    极短句（如单字「行」「嗯」）VITS 合成峰值极低（约 0.02），
    即使归一化也几乎无音可放。这里对极短音频额外补一个增益下限，
    保证短句也能听得清。
    """
    lead = int(sample_rate * config.TTS_LOCAL_LEAD_SILENCE)
    if lead > 0:
        pcm = np.concatenate([np.zeros(lead, dtype=np.float32), pcm])

    pcm = _normalize_loudness(pcm)

    # 极短句兜底：时长 < 0.5s 且归一化后仍很弱，再放大
    dur = len(pcm) / sample_rate
    if dur < 0.5:
        peak = float(np.max(np.abs(pcm)))
        if peak < 0.4:
            pcm = np.clip(pcm * (0.75 / max(peak, 1e-4)), -1.0, 1.0).astype(np.float32)
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
        return _postprocess(pcm, self.sample_rate)


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
    """整段朗读，双引擎。支持打断。"""

    def __init__(self):
        self._playing = False
        self._stop_flag = threading.Event()
        self._play_lock = threading.Lock()
        self._current_pcm = None
        self._engine = config.TTS_ENGINE

        if self._engine == "edge":
            self._sherpa = None
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

    def stop(self):
        """打断当前播放，带平滑淡出。"""
        self._stop_flag.set()
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

    def is_playing(self) -> bool:
        return self._playing

    def _speak_sherpa_stream(self, text: str):
        """按句流式：第一句合成完立即播放，之后「边播边合成」无空隙衔接。

        用双缓冲流水线：
          - 先合成第 1 句，立即 blocking=False 开播（用户此刻就听到声音）
          - 之后每句：先合成第 N+1 句，再用 blocking=True 播第 N 句（等它播完），
            此时第 N+1 句已就绪，下一轮直接播，无停顿。
        全程单线程，无竞态；sd.stop() 可整体打断（stop_flag 检查）。
        """
        sents = _split_sentences(text)
        if not sents:
            return

        # 合成第一句并立即开播（关键：最小化「听到声音」的延迟）
        if self._stop_flag.is_set():
            return
        pcm_next = self._sherpa.synthesize(sents[0])
        if self._stop_flag.is_set():
            return
        sd.stop()
        self._current_pcm = pcm_next
        sd.play(_apply_volume(pcm_next), self._sample_rate, blocking=False)

        # 流水线：提前合成下一句，再播当前句
        for i in range(1, len(sents)):
            if self._stop_flag.is_set():
                break
            # 提前合成下一句（在播放当前句期间进行）
            pcm = pcm_next
            pcm_next = self._sherpa.synthesize(sents[i])
            if self._stop_flag.is_set():
                break
            # 播当前句（blocking=True 等它播完，下一句已就绪）
            self._current_pcm = pcm
            sd.play(_apply_volume(pcm), self._sample_rate, blocking=True)

        # 等最后一句播完
        if not self._stop_flag.is_set():
            sd.wait()

    def speak_text(self, text: str):
        """朗读文本（阻塞直到读完）。默认按句流式，可配置整段。"""
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
            use_stream = getattr(config, "TTS_STREAM", True) and self._engine != "edge"
            if self._engine == "edge":
                mp3 = synthesize_edge(text)
                if self._stop_flag.is_set():
                    return
                pcm = _mp3_to_pcm(mp3)
            elif use_stream:
                self._speak_sherpa_stream(text)
                t_synth_end = time.time()
                if _debug_timing:
                    print(f"[timing] TTS首句合成 {t_synth_end - t_synth_start:.2f}s（流式）")
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
    return "sherpa（本地离线女声）" if config.TTS_ENGINE != "edge" else "edge（陕西小妮，需联网）"


if __name__ == "__main__":
    print("TTS 测试（整段朗读）：")
    print(f"引擎: {engine_name()}")
    t = TTS()
    t.speak_text("哎哟，就这车技还跑长途呢，回去重考本吧！")
    print("完成。")
