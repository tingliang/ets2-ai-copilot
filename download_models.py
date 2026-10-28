"""
模型下载脚本：把 STT / TTS 本地模型一次性下到本地 models/。

- whisper-base（STT 识别）：Systran/faster-whisper-base 的 ModelScope 镜像，约 145MB
- vits-melo-tts-zh_en（TTS 合成）：liaowenbin/vits-melo-tts-zh_en 的 ModelScope 镜像，约 190MB

全部走 ModelScope 魔搭（阿里 CDN），国内稳定高速，不依赖 HuggingFace。

用法：
    python download_models.py             # 下载全部
    python download_models.py --whisper   # 只下 STT 模型
    python download_models.py --tts       # 只下 TTS 模型
    python download_models.py --check     # 只校验完整性，不下载

设计：
- 纯 requests 实现，带进度条、断点续传（已存在且大小一致则跳过）、3 次重试。
- 不 import 项目其它模块，避免触发 sounddevice / sherpa 等依赖。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

try:
    import requests
except ImportError:
    print("[错误] 缺少 requests，请先执行：pip install requests")
    sys.exit(1)

BASE = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE, "models")

# ---------------- 模型清单 ----------------
# whisper-base（STT）：faster-whisper 官方仓库的魔搭镜像
WHISPER_ID = "Systran/faster-whisper-base"
WHISPER_FILES = [
    "model.bin",
    "config.json",
    "tokenizer.json",
    "vocabulary.txt",
    "configuration.json",
]

# vits-melo-tts-zh_en（TTS）：需与 tts.py 的 _MODEL_FILES 保持一致
TTS_ID = "liaowenbin/vits-melo-tts-zh_en"
TTS_FILES = [
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


def _ms_url(repo_id: str, path: str) -> str:
    return f"https://modelscope.cn/models/{repo_id}/resolve/master/{path}"


def _remote_size(url: str) -> int:
    """用 GET 只取响应头拿到 Content-Length（ModelScope 的 HEAD 不返回该字段）。"""
    try:
        with requests.get(url, stream=True, timeout=20) as r:
            return int(r.headers.get("Content-Length", 0))
    except Exception:
        return 0


def _download(url: str, dest: str, desc: str) -> bool:
    """下载单个文件。已存在且大小一致则跳过；失败重试 3 次。"""
    if os.path.exists(dest):
        total = _remote_size(url)
        if total and os.path.getsize(dest) == total:
            print(f"    [跳过] {desc}（已存在且完整）")
            return True
        # 大小不一致或无法校验 → 删除重下（.part 原子替换保证存在的文件都是完整下载的）
        os.remove(dest)

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    for attempt in range(3):
        try:
            r = requests.get(url, stream=True, timeout=60)
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0))
            done = 0
            tmp = dest + ".part"
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = done * 100 // total
                        print(f"\r    [{desc}] {pct}%", end="", flush=True)
            os.replace(tmp, dest)
            print(f"\r    [{desc}] 完成    ", flush=True)
            return True
        except Exception as e:
            print(f"\r    [{desc}] 失败({attempt + 1}/3): {e}", flush=True)
            time.sleep(1.5)
    return False


def _download_group(repo_id: str, files: list, dest_dir: str, label: str) -> bool:
    print(f"\n== 下载 {label}（{len(files)} 个文件）到 {dest_dir} ==")
    ok = True
    for path in files:
        url = _ms_url(repo_id, path)
        dest = os.path.join(dest_dir, path.replace("/", os.sep))
        if not _download(url, dest, desc=path):
            ok = False
            print(f"    [失败] {path}，可手动下载：https://modelscope.cn/models/{repo_id}")
    return ok


def _check_group(files: list, dest_dir: str, label: str) -> bool:
    missing = [p for p in files if not os.path.exists(os.path.join(dest_dir, p.replace("/", os.sep)))]
    if missing:
        print(f"  [不完整] {label} 缺 {len(missing)} 个文件：{missing[:3]} ...")
        return False
    print(f"  [完整] {label}（{len(files)} 个文件）")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="下载 ETS2 副驾所需的本地模型（STT + TTS）")
    ap.add_argument("--whisper", action="store_true", help="只下载 whisper-base（STT）")
    ap.add_argument("--tts", action="store_true", help="只下载 vits-melo-tts（TTS）")
    ap.add_argument("--check", action="store_true", help="只校验模型完整性，不下载")
    args = ap.parse_args()

    do_whisper = args.whisper or (not args.tts and not args.check)
    do_tts = args.tts or (not args.whisper and not args.check)

    if args.check:
        print("== 校验模型完整性 ==")
        w = _check_group(WHISPER_FILES, os.path.join(MODELS_DIR, "whisper-base"), "whisper-base")
        t = _check_group(TTS_FILES, os.path.join(MODELS_DIR, "vits-melo-tts-zh_en"), "vits-melo-tts-zh_en")
        print("\n结果：" + ("全部完整 ✓" if (w and t) else "有缺失，请运行 python download_models.py 补全"))
        return 0 if (w and t) else 1

    ok = True
    if do_whisper:
        ok &= _download_group(WHISPER_ID, WHISPER_FILES,
                              os.path.join(MODELS_DIR, "whisper-base"), "whisper-base")
    if do_tts:
        ok &= _download_group(TTS_ID, TTS_FILES,
                              os.path.join(MODELS_DIR, "vits-melo-tts-zh_en"), "vits-melo-tts-zh_en")

    print("\n" + ("== 全部模型下载完成 ✓ ==" if ok else "== 有模型下载失败，可重新运行本脚本重试 =="))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
