# CosyVoice2 Natural Emotional TTS — Setup Guide

> This is an **optional third TTS engine** that makes the copilot's voice more natural
> and expressive (happy / angry / sad tones). The default is still `sherpa`; once
> CosyVoice is set up you switch to it in `.env`. If it fails to start, it auto-falls
> back to sherpa — nothing else breaks.

## 0. What this does & why it is set up this way

| Item | Notes |
|------|-------|
| **Purpose** | Use CosyVoice2 (Alibaba's Tongyi open-source model, Apache 2.0) to replace the default VITS voice with a more natural, emotion-controllable one. |
| **Isolated venv** | CosyVoice2 pins torch + transformers, which conflict with the main program's faster-whisper / sherpa-onnx. It lives in its own `venv_cosyvoice/` and communicates via a subprocess. |
| **Source repo required** | `from cosyvoice.cli.cosyvoice import AutoModel` needs the CosyVoice **source code** (the `cosyvoice` package), NOT just the model files. The script clones it to `models/CosyVoice/`. |
| **Reference audio required** | CosyVoice2 is a **zero-shot** model — it has no built-in speaker voice. Every synthesis needs a reference `prompt_wav`. The official repo ships one at `asset/zero_shot_prompt.wav`, cloned together with the repo. |
| **Disk** | ~7–8 GB total (deps 2–3 GB + model 4.5 GB), one-time. |

## 1. Prerequisites

- Windows + NVIDIA GPU (recommended; CPU works but is slow)
- Python 3.10 or 3.11 (matches the pinned torch 2.3.1 / transformers 4.51.3 stack)
- `git` installed and on PATH (needed to clone the repo)
- ≥ 10 GB free disk space

## 2. Install (one command)

Double-click `setup_cosyvoice.bat`, or run:

```cmd
python setup_cosyvoice.py
```

It performs the full flow automatically:

1. **Clone** the CosyVoice source repo → `models/CosyVoice/` (with `--recursive` for the `third_party/Matcha-TTS` submodule)
2. **Create** the isolated venv `venv_cosyvoice/`
3. **Install** torch 2.3.1 + torchaudio 2.3.1 (CUDA 12.1 if an NVIDIA GPU is detected, else CPU)
4. **Install** the official CosyVoice `requirements.txt` (conformer, hydra-core, omegaconf, pyworld, transformers, etc. — `wetext`/pynini are intentionally skipped, see below)
5. **Install** `openai-whisper` with a legacy build (`--no-build-isolation` + `setuptools<81`)
6. **Download** the model → `models/CosyVoice2-0.5B/` (HuggingFace first, ModelScope fallback)

> Individual steps can be re-run separately:
> `--repo-only`, `--venv-only`, `--deps-only`, `--model-only`, `--check`.

## 3. Enable

1. Edit `.env`:
   ```
   TTS_ENGINE=cosyvoice
   ```
2. Restart `start.bat`.

The main program auto-starts `cosyvoice_server.py` (loading takes a few seconds the
first time) and auto-falls back to sherpa if it fails.

## 4. Configuration (.env)

| Key | Default | Notes |
|-----|---------|-------|
| `TTS_ENGINE` | `sherpa` | set to `cosyvoice` to enable |
| `COSYVOICE_HOST` | `127.0.0.1` | subprocess address, usually unchanged |
| `COSYVOICE_PORT` | `9860` | subprocess port |
| `COSYVOICE_EMOTION` | `true` | emotion tone control (recommended) |
| `COSYVOICE_DEVICE` | `auto` | `auto`/`cuda`/`cpu` |

## 5. Emotion mapping (automatic)

When CosyVoice is enabled, speech carries emotion aligned with the pet's expression:

| Emotion tag | Voice tone | Pet expression |
|-------------|-----------|----------------|
| `[开心]` / happy | cheerful, bright, rising | heart eyes / smile |
| `[生气]` / angry | annoyed, impatient, fast | glare / angry |
| `[难过]` / sad | gentle, low, slow | teary / dark |
| `[惊讶]` / surprised | surprised, curious, rising | round eyes / sparkle |
| `[平静]` / calm | calm, natural | default |

## 6. Troubleshooting

**Q: Setup finished but the voice is still sherpa?**
Check the console for `[tts]` lines — it prints either "CosyVoice ready" or the
fallback reason. Common causes: model dir wrong, or the repo submodule incomplete.

**Q: `ModuleNotFoundError: No module named 'cosyvoice'`?**
The source repo wasn't cloned. Run `python setup_cosyvoice.py --repo-only`.

**Q: `ModuleNotFoundError: No module named 'whisper'` (or `conformer`, `hydra`, etc.)?**
Missing official requirements. Run `python setup_cosyvoice.py --deps-only`.

**Q: `openai-whisper` fails to build with `No module named 'pkg_resources'`?**
This is a known issue: the `openai-whisper` sdist imports `pkg_resources`, which
was removed in setuptools ≥ 81. The setup script already handles this by
installing `setuptools<81` and building with `--no-build-isolation`. If you hit it
manually, run:

```cmd
venv_cosyvoice\Scripts\python.exe -m pip install "setuptools<81" wheel
venv_cosyvoice\Scripts\python.exe -m pip install --no-build-isolation openai-whisper==20231117
```

**Q: Do I need pynini / WeTextProcessing?**
No. CosyVoice treats them as an **optional** text frontend — when they are missing
it falls back automatically and synthesis still works. They are skipped on purpose
because `pynini` has no Windows wheel (it needs an OpenFst C++ toolchain). The only
side effect is that Chinese digits/units (e.g. "123") are not expanded to words,
which is negligible for casual conversation.

**Q: CPU is too slow?**
Expected. CosyVoice is a 0.5B model; on CPU synthesis is slower than real-time
(2–3 s per sentence). Use GPU (CUDA torch) if possible.

**Q: Switch back to the old voice?**
Set `TTS_ENGINE=sherpa` in `.env`. You can switch any time.
