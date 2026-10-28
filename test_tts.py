#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第一关：离线 TTS 音色试听脚本（sherpa-onnx + 中文 VITS 女声）

作用：在你本地机器上合成几句副驾场景的中文，让你先听离线女声
     到底自不自然、喜不喜欢，再决定后续要不要正式接入。

用法（在项目根目录）：
    python test_tts.py                 # 自动下载模型 + 合成 5 句测试 + 播放
    python test_tts.py "自定义文本"     # 只合成你给的这一句
    python test_tts.py --no-play       # 只生成 wav，不自动播放

依赖（会自动检查，缺了会提示）：
    pip install sherpa-onnx numpy requests

模型：vits-melo-tts-zh_en（MeloTTS 中文女声，ONNX 版，约 190MB）
下载源：ModelScope 魔搭（国内 CDN，稳定高速）
"""

import argparse
import os
import sys
import time

# ==================== 依赖检查 ====================

def check_deps():
    """检查必需依赖，返回缺失列表。"""
    missing = []
    try:
        import numpy  # noqa: F401
    except ImportError:
        missing.append("numpy")
    try:
        import requests  # noqa: F401
    except ImportError:
        missing.append("requests")
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        missing.append("sherpa-onnx")
    return missing


# ==================== 模型下载 ====================

MODEL_ID = "liaowenbin/vits-melo-tts-zh_en"   # ModelScope 仓库 ID
MODEL_BASE_URL = f"https://modelscope.cn/models/{MODEL_ID}/resolve/master"

# 完整文件清单（缺失 dict/ 会导致中文分词失败、发音错误）
MODEL_FILES = [
    "model.onnx",                              # 170 MB
    "lexicon.txt",                             # 6.8 MB
    "tokens.txt",
    "phone.fst",
    "date.fst",
    "number.fst",
    "new_heteronym.fst",
    "dict/hmm_model.utf8",
    "dict/idf.utf8",
    "dict/jieba.dict.utf8",                    # 中文分词词典，必须有
    "dict/stop_words.utf8",
    "dict/user.dict.utf8",
    "dict/pos_dict/char_state_tab.utf8",
    "dict/pos_dict/prob_emit.utf8",
    "dict/pos_dict/prob_start.utf8",
    "dict/pos_dict/prob_trans.utf8",
]


def download_file(url, dest, desc=""):
    """下载单个文件，带进度显示；已存在且大小一致则跳过。"""
    import requests

    if os.path.exists(dest):
        # 用远端 Content-Length 校验大小，一致就跳过
        try:
            head = requests.head(url, timeout=15)
            total = int(head.headers.get("Content-Length", 0))
            if total and os.path.getsize(dest) == total:
                print(f"    [跳过] {desc}（已存在）")
                return True
        except Exception:
            pass

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print(f"    [下载] {desc} ...", end=" ", flush=True)
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
                        print(f"\r    [下载] {desc} {pct}%", end="", flush=True)
            print(" 完成")
            return True
        except Exception as e:
            print(f" 失败({attempt+1}/3): {e}")
            time.sleep(1)
    return False


def ensure_model(model_dir):
    """确保模型目录完整。返回 (model_dir, 是否完整)。"""
    need_download = False
    # 关键文件检查：model.onnx + lexicon.txt + tokens.txt + dict/jieba.dict.utf8
    key_files = [
        os.path.join(model_dir, "model.onnx"),
        os.path.join(model_dir, "lexicon.txt"),
        os.path.join(model_dir, "tokens.txt"),
        os.path.join(model_dir, "dict", "jieba.dict.utf8"),
    ]
    if all(os.path.exists(f) for f in key_files):
        print(f"模型已就绪：{model_dir}")
        return model_dir

    print(f"模型缺失，开始从 ModelScope 下载到：{model_dir}")
    print(f"（首次约 190MB，请耐心等待）")
    ok = True
    for path in MODEL_FILES:
        url = f"{MODEL_BASE_URL}/{path}"
        dest = os.path.join(model_dir, path.replace("/", os.sep))
        if not download_file(url, dest, desc=path):
            ok = False
            print(f"    [失败] {path} 下载失败")
            break
    if not ok:
        print("\n[!] 模型下载失败。可手动从浏览器下载整包：")
        print(f"    https://modelscope.cn/models/{MODEL_ID}")
        print("    下载后解压到 models/vits-melo-tts-zh_en/ 目录")
        return None
    print("模型下载完成 ✓")
    return model_dir


# ==================== 合成 ====================

def build_tts(model_dir):
    import sherpa_onnx

    root = model_dir
    model_path = os.path.join(root, "model.onnx")
    lexicon_path = os.path.join(root, "lexicon.txt")
    tokens_path = os.path.join(root, "tokens.txt")

    # 自动收集 rule fsts（数字/日期/多音字处理），按官方推荐顺序
    fst_order = ["phone.fst", "date.fst", "number.fst", "new_heteronym.fst"]
    fsts = [os.path.join(root, f) for f in fst_order if os.path.exists(os.path.join(root, f))]
    # 追加目录里其他 .fst（若有）
    for name in sorted(os.listdir(root)):
        if name.endswith(".fst") and name not in fst_order:
            fsts.append(os.path.join(root, name))

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

    return sherpa_onnx.OfflineTts(tts_config)


def synthesize(tts, text, speed=1.0):
    """合成一句，返回 (samples_float32, sample_rate, 耗时秒)。"""
    import sherpa_onnx

    start = time.time()
    # 兼容新旧版 sherpa-onnx 的调用方式
    try:
        gen = sherpa_onnx.GenerationConfig()
        gen.speed = speed
        audio = tts.generate(text, gen)
    except (AttributeError, TypeError):
        audio = tts.generate(text, sid=0, speed=speed)
    elapsed = time.time() - start
    return audio, elapsed


def save_wav(samples, sample_rate, out_path):
    """用标准库 + numpy 写 16bit 单声道 wav，避免额外依赖 soundfile。"""
    import numpy as np
    import wave

    samples = np.asarray(samples, dtype=np.float32)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(out_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())


def play_wav(out_path):
    """播放 wav：优先 winsound（Windows 标准库），否则尝试 sounddevice。"""
    try:
        import winsound
        winsound.PlaySound(out_path, winsound.SND_FILENAME)
        return
    except ImportError:
        pass
    except Exception:
        return  # winsound 播放失败就静默跳过，不中断流程
    try:
        import sounddevice as sd
        import soundfile as sf
        data, sr = sf.read(out_path)
        sd.play(data, sr)
        sd.wait()
    except Exception:
        pass


# ==================== 主流程 ====================

TEST_SENTENCES = [
    "你好呀，我是小满，你的卡车副驾。",
    "前面三公里有服务区，要不要进去加个油？",
    "哎，又堵车了，慢慢等吧。",
    "转弯注意点，别把货甩了。",
    "今天开得真不错，稳得很。",
]


def main():
    parser = argparse.ArgumentParser(description="离线 TTS 音色试听")
    parser.add_argument("text", nargs="?", default="", help="自定义合成文本")
    parser.add_argument("--speed", type=float, default=1.0, help="语速（默认 1.0，越小越慢）")
    parser.add_argument("--no-play", action="store_true", help="只生成 wav 不播放")
    parser.add_argument("--model-dir", default="", help="模型目录（默认 models/vits-melo-tts-zh_en）")
    args = parser.parse_args()

    # 1. 依赖检查
    missing = check_deps()
    if missing:
        print("[!] 缺少依赖，请先安装：")
        print(f"    pip install {' '.join(missing)}")
        sys.exit(1)

    # 2. 模型准备
    base = os.path.dirname(os.path.abspath(__file__))
    model_dir = args.model_dir or os.path.join(base, "models", "vits-melo-tts-zh_en")
    model_dir = ensure_model(model_dir)
    if not model_dir:
        sys.exit(1)

    # 3. 加载引擎（计时）
    print("\n正在加载 TTS 引擎 ...")
    t0 = time.time()
    tts = build_tts(model_dir)
    print(f"引擎加载完成，耗时 {time.time()-t0:.2f}s\n")

    # 4. 合成
    out_dir = os.path.join(base, "tts_test_out")
    os.makedirs(out_dir, exist_ok=True)

    if args.text:
        sentences = [(args.text, "custom")]
    else:
        sentences = [(s, f"sample{i+1}") for i, s in enumerate(TEST_SENTENCES)]

    print("=" * 60)
    for text, name in sentences:
        audio, elapsed = synthesize(tts, text, speed=args.speed)
        if audio is None or len(audio.samples) == 0:
            print(f"[失败] 合成无音频：{text}")
            continue
        dur = len(audio.samples) / audio.sample_rate
        rtf = elapsed / dur if dur else 0
        out_path = os.path.join(out_dir, f"{name}.wav")
        save_wav(audio.samples, audio.sample_rate, out_path)
        print(f"[{name}] {text}")
        print(f"    音频时长 {dur:.2f}s | 合成耗时 {elapsed:.3f}s | RTF {rtf:.3f}")
        print(f"    已保存 → {out_path}")
        if not args.no_play:
            print("    （播放中 ...）")
            play_wav(out_path)
        print("-" * 60)

    print("\n试听完成。请确认：")
    print("  1. 音色是否自然、是否喜欢？")
    print("  2. 数字（三公里）、语气词（哎）、口语表达是否正常？")
    print("  3. 语速是否合适？（可用 --speed 0.9 / 1.1 微调）")
    print(f"\n生成的 wav 在：{out_dir}")


if __name__ == "__main__":
    main()
