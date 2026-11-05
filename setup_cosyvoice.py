# -*- coding: utf-8 -*-
"""
CosyVoice2 (natural emotional TTS) one-click setup script.

Creates an isolated venv (venv_cosyvoice/) under the project, clones the
CosyVoice source repo, installs all dependencies, and downloads the
CosyVoice2-0.5B model.

Why an isolated venv: CosyVoice2 pins torch + transformers, which conflict
with the main program's faster-whisper / sherpa-onnx, so they must be kept
separate. (pynini / WeTextProcessing are NOT required and are skipped.)

Usage:
    python setup_cosyvoice.py                 # full: repo + venv + deps + model
    python setup_cosyvoice.py --repo-only     # only clone CosyVoice source repo
    python setup_cosyvoice.py --venv-only     # only create venv + install deps
    python setup_cosyvoice.py --deps-only     # only (re)install deps into existing venv
    python setup_cosyvoice.py --model-only    # only download model
    python setup_cosyvoice.py --check         # only check readiness

After setup, set TTS_ENGINE=cosyvoice in .env to enable (auto-fallback to sherpa).

Network note: this targets a Singapore / overseas environment. It uses the
default PyPI index and the official GitHub / HuggingFace sources (no China
mirrors).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(BASE, "venv_cosyvoice")
MODEL_DIR = os.path.join(BASE, "models", "CosyVoice2-0.5B")
# Where the CosyVoice source repo (the `cosyvoice` package + third_party submodules) lives.
COSYVOICE_REPO_DIR = os.path.join(BASE, "models", "CosyVoice")

# CosyVoice source repo (official GitHub).
REPO_URL = "https://github.com/FunAudioLLM/CosyVoice.git"

# Model: official HuggingFace repo id (Singapore-friendly). ModelScope id kept
# as a fallback for users who prefer it.
HF_MODEL_ID = "FunAudioLLM/CosyVoice2-0.5B"
MS_MODEL_ID = "iic/CosyVoice2-0.5B"

# torch / torchaudio version pinned to the official requirements.
TORCH_VERSION = "2.3.1"
TORCHAUDIO_VERSION = "2.3.1"

# Minimal extras for our own cosyvoice_server.py HTTP wrapper.
EXTRA_PACKAGES = [
    "flask",
    "requests",
]


def _venv_python() -> str:
    """Return the python executable path inside the isolated venv."""
    if sys.platform == "win32":
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python")


def _run(cmd: list[str], desc: str) -> bool:
    print(f"\n>>> {desc}")
    print("    " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=BASE)
    return r.returncode == 0


def _has_cuda() -> bool:
    """Detect whether an NVIDIA GPU is available."""
    try:
        import ctypes
        ctypes.windll.kernel32.LoadLibraryW("nvcuda.dll")
        return True
    except Exception:
        try:
            return subprocess.run(
                ["nvidia-smi"], capture_output=True, timeout=5
            ).returncode == 0
        except Exception:
            return False


def clone_repo() -> bool:
    """Clone the CosyVoice source repo (contains the `cosyvoice` package)."""
    if os.path.exists(os.path.join(COSYVOICE_REPO_DIR, "cosyvoice")):
        print(f"[repo] already exists: {COSYVOICE_REPO_DIR}")
        return True

    print(f"[repo] cloning CosyVoice source to {COSYVOICE_REPO_DIR} ...")
    ok = _run(["git", "clone", "--recursive", REPO_URL, COSYVOICE_REPO_DIR], f"clone {REPO_URL}")
    if not ok and os.path.exists(COSYVOICE_REPO_DIR):
        import shutil
        shutil.rmtree(COSYVOICE_REPO_DIR, ignore_errors=True)
    return ok


def update_submodules() -> bool:
    """Ensure third_party submodules are present (idempotent)."""
    if not os.path.isdir(COSYVOICE_REPO_DIR):
        return clone_repo()
    if os.path.isdir(os.path.join(COSYVOICE_REPO_DIR, "third_party", "Matcha-TTS")):
        return True
    print("[repo] fetching third_party submodules ...")
    if not _run(["git", "submodule", "update", "--init", "--recursive"], "update submodules"):
        print("[WARN] submodule update failed; re-run with --repo-only")
        return False
    return True


def _install_deps(py: str) -> None:
    """Install torch + CosyVoice requirements into the venv (idempotent)."""
    # torch (cuda if NVIDIA GPU detected, otherwise cpu), pinned to official version.
    print("\n>>> detecting GPU ...")
    if _has_cuda():
        print("    NVIDIA GPU detected, installing torch + torchaudio (CUDA 12.1 build)")
        _run([py, "-m", "pip", "install",
              f"torch=={TORCH_VERSION}", f"torchaudio=={TORCHAUDIO_VERSION}",
              "--index-url", "https://download.pytorch.org/whl/cu121"],
             "install torch (CUDA, large, be patient)")
    else:
        print("    no GPU detected, installing torch + torchaudio (CPU build)")
        _run([py, "-m", "pip", "install",
              f"torch=={TORCH_VERSION}", f"torchaudio=={TORCHAUDIO_VERSION}"],
             "install torch (CPU, large, be patient)")

    # Install the official CosyVoice requirements (from the cloned repo),
    # filtering out linux-only / torch / onnxruntime-gpu entries, plus the two
    # Windows-problematic packages (wetext, openai-whisper) which are handled
    # separately below.
    req_file = os.path.join(COSYVOICE_REPO_DIR, "requirements.txt")
    if os.path.exists(req_file):
        print("\n>>> installing official CosyVoice requirements (filtered) ...")
        with open(req_file, encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
        skip = []
        for l in lines:
            low = l.lower()
            if l.startswith("--"):
                skip.append(l)
                continue
            if "sys_platform" in low:
                # Keep only entries matching this platform.
                if ("win32" in low) != (sys.platform == "win32"):
                    skip.append(l)
                    continue
            if low.startswith("torch==") or low.startswith("torchaudio==") or low.startswith("deepspeed"):
                skip.append(l)
                continue
            if "onnxruntime-gpu" in low or "tensorrt" in low:
                skip.append(l)
                continue
            # wetext is OPTIONAL: CosyVoice's frontend falls back gracefully when
            # it is missing, and it pulls in pynini which has no Windows wheel
            # (needs an OpenFst C++ toolchain). Skipping it only disables Chinese
            # digit/unit normalization, which is negligible for conversation.
            if low.startswith("wetext"):
                skip.append(l)
                continue
            # openai-whisper is installed separately with --no-build-isolation
            # (its sdist imports pkg_resources, removed in setuptools>=81).
            if low.startswith("openai-whisper"):
                skip.append(l)
                continue
        to_install = [l for l in lines if l not in skip]
        if to_install:
            _run([py, "-m", "pip", "install", *to_install],
                 f"install {len(to_install)} CosyVoice requirement packages")
    else:
        print(f"[WARN] requirements.txt not found at {req_file}; falling back to manual list")
        _run([py, "-m", "pip", "install",
              "transformers==4.51.3", "diffusers==0.29.0", "librosa==0.10.2",
              "soundfile==0.12.1", "onnxruntime", "fastapi", "uvicorn", "pydantic",
              "hyperpyyaml", "inflect==7.3.1", "conformer", "hydra-core",
              "omegaconf", "onnx", "pyworld", "rich", "x-transformers"],
             "install CosyVoice packages (fallback list)")

    # openai-whisper: the 20231117 sdist imports pkg_resources at build time,
    # which setuptools>=81 removed. Install an older setuptools and build WITHOUT
    # isolation so the legacy setup.py uses the venv's setuptools (not the fresh
    # isolated one). Pure-Python package, so no C++ toolchain is involved.
    print("\n>>> installing openai-whisper (legacy build, needs setuptools<81)")
    _run([py, "-m", "pip", "install", "setuptools<81", "wheel"], "install setuptools<81 + wheel")
    _run([py, "-m", "pip", "install", "--no-build-isolation", "openai-whisper==20231117"],
         "install openai-whisper")

    # NOTE: WeTextProcessing / pynini are intentionally NOT installed. They are
    # optional in CosyVoice (the text frontend falls back gracefully), and pynini
    # has no Windows wheel (requires an OpenFst C++ toolchain). Skipping them only
    # disables Chinese digit/unit normalization, negligible for casual chat.

    # Our own HTTP wrapper's minimal extras.
    for pkg in EXTRA_PACKAGES:
        _run([py, "-m", "pip", "install", pkg], f"install {pkg}")


def setup_venv(force_deps: bool = False) -> bool:
    """Create the isolated venv (if missing) and install dependencies."""
    py = _venv_python()
    if not os.path.exists(py):
        print(f"[venv] creating isolated environment {VENV_DIR} ...")
        if not _run([sys.executable, "-m", "venv", VENV_DIR], "create venv"):
            print("[ERROR] venv creation failed; make sure Python supports the venv module")
            return False
        _run([py, "-m", "pip", "install", "--upgrade", "pip"], "upgrade pip")
    else:
        print(f"[venv] already exists: {py}")

    if force_deps:
        print("[venv] (re)installing dependencies (--deps-only)")
    _install_deps(py)

    print("\n[venv] dependencies installed OK")
    return True


def download_model() -> bool:
    """Download the CosyVoice2-0.5B model (HuggingFace first, then ModelScope)."""
    if os.path.isfile(os.path.join(MODEL_DIR, "cosyvoice2.yaml")):
        print(f"[model] already exists: {MODEL_DIR}")
        return True

    py = _venv_python() if os.path.exists(_venv_python()) else sys.executable

    # Try HuggingFace first (Singapore-friendly), then ModelScope.
    # Pass paths via env vars to avoid backslash-escape issues in `-c` code.
    env = dict(os.environ)
    env["COSYVOICE_MODEL_DIR"] = MODEL_DIR
    env["HF_MODEL_ID"] = HF_MODEL_ID
    env["MS_MODEL_ID"] = MS_MODEL_ID

    code = (
        "import os, sys\n"
        "model_dir = os.environ['COSYVOICE_MODEL_DIR']\n"
        "hf_id = os.environ['HF_MODEL_ID']\n"
        "ms_id = os.environ['MS_MODEL_ID']\n"
        "try:\n"
        "    from huggingface_hub import snapshot_download as hf_snap\n"
        "    hf_snap(hf_id, local_dir=model_dir)\n"
        "    print('model downloaded from HuggingFace to', model_dir)\n"
        "except Exception as e:\n"
        "    print('HuggingFace download failed:', e)\n"
        "    from modelscope import snapshot_download as ms_snap\n"
        "    ms_snap(ms_id, local_dir=model_dir)\n"
        "    print('model downloaded from ModelScope to', model_dir)\n"
    )

    # Ensure both SDKs are available.
    _run([py, "-m", "pip", "install", "huggingface_hub"], "install huggingface_hub")
    _run([py, "-m", "pip", "install", "modelscope"], "install modelscope")

    print(f"[model] downloading {HF_MODEL_ID} (~4.5GB, be patient) ...")
    r = subprocess.run([py, "-c", code], cwd=BASE, env=env)
    if r.returncode != 0:
        print("[ERROR] model download failed. Download manually from:")
        print(f"        https://huggingface.co/{HF_MODEL_ID}")
        print(f"    or  https://modelscope.cn/models/{MS_MODEL_ID}")
        print(f"    then place the files in {MODEL_DIR}")
        return False
    return True


def check() -> bool:
    """Check whether the environment is ready."""
    ok = True
    py = _venv_python()
    if not os.path.exists(py):
        print("[X] venv not found (venv_cosyvoice)")
        ok = False
    else:
        print(f"[OK] venv: {py}")
    if not os.path.exists(os.path.join(COSYVOICE_REPO_DIR, "cosyvoice")):
        print(f"[X] CosyVoice source repo not found ({COSYVOICE_REPO_DIR})")
        ok = False
    else:
        print(f"[OK] CosyVoice source: {COSYVOICE_REPO_DIR}")
    if not os.path.isfile(os.path.join(MODEL_DIR, "cosyvoice2.yaml")):
        print(f"[X] model not ready ({MODEL_DIR})")
        ok = False
    else:
        print(f"[OK] model: {MODEL_DIR}")
    if not os.path.isfile(os.path.join(COSYVOICE_REPO_DIR, "asset", "zero_shot_prompt.wav")):
        print("[WARN] reference audio (asset/zero_shot_prompt.wav) missing; instruct/zero-shot will fail")
    else:
        print("[OK] reference audio: asset/zero_shot_prompt.wav")
    if ok:
        print("\nEnvironment ready! Set TTS_ENGINE=cosyvoice in .env to enable.")
    return ok


def main():
    ap = argparse.ArgumentParser(description="CosyVoice2 one-click setup")
    ap.add_argument("--repo-only", action="store_true", help="only clone CosyVoice source repo")
    ap.add_argument("--venv-only", action="store_true", help="only create venv + install deps")
    ap.add_argument("--deps-only", action="store_true", help="only (re)install deps into existing venv")
    ap.add_argument("--model-only", action="store_true", help="only download model")
    ap.add_argument("--check", action="store_true", help="only check readiness")
    args = ap.parse_args()

    if args.check:
        sys.exit(0 if check() else 1)
    if args.model_only:
        sys.exit(0 if download_model() else 1)
    if args.repo_only:
        sys.exit(0 if (clone_repo() and update_submodules()) else 1)
    if args.deps_only:
        sys.exit(0 if setup_venv(force_deps=True) else 1)
    if args.venv_only:
        sys.exit(0 if setup_venv() else 1)

    # Default: full flow
    if not clone_repo():
        sys.exit(1)
    if not update_submodules():
        print("[WARN] continuing anyway; if import fails, re-run with --repo-only")
    if not setup_venv():
        sys.exit(1)
    if not download_model():
        sys.exit(1)
    print("\n============================================")
    print("  CosyVoice2 setup complete")
    print("  Next steps:")
    print("    1. edit .env, set TTS_ENGINE=cosyvoice")
    print("    2. restart start.bat")
    print("  (auto-falls back to sherpa on startup failure)")
    print("============================================")


if __name__ == "__main__":
    main()
