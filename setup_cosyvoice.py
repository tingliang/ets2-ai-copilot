# -*- coding: utf-8 -*-
"""
CosyVoice2 (natural emotional TTS) one-click setup script.

Creates an isolated venv (venv_cosyvoice/) under the project, installs
CosyVoice2 dependencies, and downloads the model from ModelScope to
models/CosyVoice2-0.5B.

Why an isolated venv: CosyVoice2 pins torch + transformers + pynini /
WeTextProcessing, which conflict with the main program's faster-whisper /
sherpa-onnx, so they must be kept separate.

Usage:
    python setup_cosyvoice.py                 # full: venv + deps + model
    python setup_cosyvoice.py --venv-only     # only venv + deps
    python setup_cosyvoice.py --model-only    # only download model
    python setup_cosyvoice.py --check         # only check readiness

After setup, set TTS_ENGINE=cosyvoice in .env to enable (auto-fallback to sherpa).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(BASE, "venv_cosyvoice")
MODEL_DIR = os.path.join(BASE, "models", "CosyVoice2-0.5B")
# Where the CosyVoice source repo (the `cosyvoice` python package + third_party submodules) lives.
COSYVOICE_REPO_DIR = os.path.join(BASE, "models", "CosyVoice")

# ModelScope repo id
MODEL_ID = "iic/CosyVoice2-0.5B"

# CosyVoice source repo. Official is FunAudioLLM/CosyVoice (now QwenAudio/CosyVoice);
# ModelScope has a mirror that is faster from inside China.
REPO_URL = "https://github.com/FunAudioLLM/CosyVoice.git"
REPO_MIRROR = "https://www.modelscope.cn/models/FunAudioLLM/CosyVoice.git"

# Core packages that must match the CosyVoice official requirements closely.
# The full official requirements.txt (minus linux-only / torch entries) is fetched
# and installed separately in setup_venv() to avoid missing packages like
# openai-whisper, conformer, hydra-core, etc.
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"

# torch is version-pinned to the official requirements (torch 2.3.1 / torchaudio 2.3.1).
TORCH_VERSION = "2.3.1"
TORCHAUDIO_VERSION = "2.3.1"

# Minimal extras for our own cosyvoice_server.py HTTP wrapper (independent of CosyVoice).
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
    """Clone the CosyVoice source repo (contains the `cosyvoice` package).

    The model download alone is NOT enough: `from cosyvoice.cli.cosyvoice import
    CosyVoice2` needs the source code, and it also needs third_party submodules
    (Matcha-TTS) on PYTHONPATH.
    """
    if os.path.exists(os.path.join(COSYVOICE_REPO_DIR, "cosyvoice")):
        print(f"[repo] already exists: {COSYVOICE_REPO_DIR}")
        return True

    print(f"[repo] cloning CosyVoice source to {COSYVOICE_REPO_DIR} ...")
    # Try the mirror first (faster in China), then the official URL.
    for url in (REPO_MIRROR, REPO_URL):
        if _run(["git", "clone", "--recursive", url, COSYVOICE_REPO_DIR], f"clone {url}"):
            return True
        # A partial/failed clone dir would block the next attempt; remove it.
        if os.path.exists(COSYVOICE_REPO_DIR):
            import shutil
            shutil.rmtree(COSYVOICE_REPO_DIR, ignore_errors=True)
    return False


def update_submodules() -> bool:
    """Ensure third_party submodules are present (idempotent)."""
    if not os.path.isdir(COSYVOICE_REPO_DIR):
        return clone_repo()
    if os.path.isdir(os.path.join(COSYVOICE_REPO_DIR, "third_party", "Matcha-TTS")):
        return True
    print("[repo] fetching third_party submodules ...")
    if not _run(["git", "submodule", "update", "--init", "--recursive"], "update submodules"):
        print("[WARN] submodule update failed; see notes below")
        return False
    return True


def _install_deps(py: str) -> None:
    """Install torch + CosyVoice requirements into the venv (idempotent)."""
    # Install torch (cuda if NVIDIA GPU is detected, otherwise cpu).
    # Pin to the official version so transformers/models load correctly.
    print("\n>>> detecting GPU ...")
    if _has_cuda():
        print("    NVIDIA GPU detected, installing torch + torchaudio (CUDA 12.1 build)")
        _run([py, "-m", "pip", "install",
              f"torch=={TORCH_VERSION}", f"torchaudio=={TORCHAUDIO_VERSION}",
              "--index-url", "https://download.pytorch.org/whl/cu121"], "install torch (CUDA, large, be patient)")
    else:
        print("    no GPU detected, installing torch + torchaudio (CPU build)")
        _run([py, "-m", "pip", "install",
              f"torch=={TORCH_VERSION}", f"torchaudio=={TORCHAUDIO_VERSION}",
              "-i", PIP_MIRROR], "install torch (CPU, large, be patient)")

    # Install the official CosyVoice requirements (fetched from the repo we cloned),
    # filtering out linux-only and torch entries we already handled.
    req_file = os.path.join(COSYVOICE_REPO_DIR, "requirements.txt")
    if os.path.exists(req_file):
        print("\n>>> installing official CosyVoice requirements (filtered) ...")
        with open(req_file, encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
        skip = []
        for l in lines:
            low = l.lower()
            if l.startswith("--"):
                skip.append(l); continue
            if "sys_platform" in low:
                if ("win32" in low) != (sys.platform == "win32"):
                    skip.append(l); continue
            if low.startswith("torch==") or low.startswith("torchaudio==") or low.startswith("deepspeed"):
                skip.append(l); continue
            if "onnxruntime-gpu" in low:
                skip.append(l); continue
            if "tensorrt" in low:
                skip.append(l); continue
        to_install = [l for l in lines if l not in skip]
        if to_install:
            _run([py, "-m", "pip", "install", *to_install, "-i", PIP_MIRROR],
                 f"install {len(to_install)} CosyVoice requirement packages")
    else:
        print(f"[WARN] requirements.txt not found at {req_file}; falling back to manual list")
        _run([py, "-m", "pip", "install",
              "transformers", "diffusers==0.29.0", "librosa==0.10.2", "soundfile",
              "onnxruntime", "fastapi", "uvicorn", "pydantic", "hyperpyyaml",
              "inflect==7.3.1", "openai-whisper", "conformer", "hydra-core",
              "omegaconf", "onnx", "pyworld", "rich", "wetext", "-i", PIP_MIRROR],
             "install CosyVoice packages (fallback list)")

    # pynini (on Windows may need conda or a prebuilt wheel; try pip, warn on failure)
    print("\n>>> installing pynini (may need conda on Windows, see notes below)")
    if not _run([py, "-m", "pip", "install", "pynini==2.1.5", "-i", PIP_MIRROR], "install pynini"):
        print("[WARN] pynini install failed. If WeTextProcessing errors, install via conda:")
        print("        conda install -c conda-forge pynini==2.1.5")

    # WeTextProcessing / wetext for text normalization (Chinese)
    _run([py, "-m", "pip", "install", "WeTextProcessing", "--no-deps", "-i", PIP_MIRROR],
         "install WeTextProcessing")

    # Our own HTTP wrapper's minimal extras
    for pkg in EXTRA_PACKAGES:
        _run([py, "-m", "pip", "install", pkg, "-i", PIP_MIRROR], f"install {pkg}")


def setup_venv(force_deps: bool = False) -> bool:
    """Create the isolated venv (if missing) and install dependencies."""
    py = _venv_python()
    if not os.path.exists(py):
        print(f"[venv] creating isolated environment {VENV_DIR} ...")
        if not _run([sys.executable, "-m", "venv", VENV_DIR], "create venv"):
            print("[ERROR] venv creation failed; make sure Python supports the venv module")
            return False
        # Upgrade pip
        _run([py, "-m", "pip", "install", "--upgrade", "pip", "-i", PIP_MIRROR], "upgrade pip")
    else:
        print(f"[venv] already exists: {py}")

    if force_deps:
        print("[venv] (re)installing dependencies (--deps-only)")
    _install_deps(py)

    print("\n[venv] dependencies installed OK")
    return True


def download_model() -> bool:
    """Download the CosyVoice2-0.5B model from ModelScope."""
    if os.path.isdir(MODEL_DIR) and os.path.exists(os.path.join(MODEL_DIR, "cosyvoice.yaml")):
        print(f"[model] already exists: {MODEL_DIR}")
        return True

    print(f"[model] downloading {MODEL_ID} from ModelScope (~4.5GB, be patient) ...")
    py = _venv_python() if os.path.exists(_venv_python()) else sys.executable

    # Install modelscope SDK first (may not be present).
    _run([py, "-m", "pip", "install", "modelscope", "-i", PIP_MIRROR], "install modelscope SDK")

    # Pass the model dir via an environment variable to avoid any backslash
    # escaping issues in the `-c` inline code on Windows.
    env = dict(os.environ)
    env["COSYVOICE_MODEL_DIR"] = MODEL_DIR
    code = (
        "import os\n"
        "from modelscope import snapshot_download\n"
        "model_dir = os.environ['COSYVOICE_MODEL_DIR']\n"
        "snapshot_download('" + MODEL_ID + "', local_dir=model_dir)\n"
        "print('model downloaded to', model_dir)\n"
    )
    r = subprocess.run([py, "-c", code], cwd=BASE, env=env)
    if r.returncode != 0:
        print("[ERROR] model download failed; you can download and extract manually from:")
        print(f"        https://www.modelscope.cn/models/{MODEL_ID}")
        print(f"    then extract to {MODEL_DIR}")
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
    if not (os.path.isdir(MODEL_DIR) and os.path.exists(os.path.join(MODEL_DIR, "cosyvoice.yaml"))):
        print(f"[X] model not ready ({MODEL_DIR})")
        ok = False
    else:
        print(f"[OK] model: {MODEL_DIR}")
    if ok:
        print("\nEnvironment ready! Set TTS_ENGINE=cosyvoice in .env to enable.")
    return ok


def main():
    ap = argparse.ArgumentParser(description="CosyVoice2 one-click setup")
    ap.add_argument("--venv-only", action="store_true", help="only venv + deps")
    ap.add_argument("--deps-only", action="store_true", help="only (re)install deps into existing venv")
    ap.add_argument("--model-only", action="store_true", help="only download model")
    ap.add_argument("--repo-only", action="store_true", help="only clone CosyVoice source repo")
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
