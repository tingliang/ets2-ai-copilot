# CosyVoice2 自然情感语音 — 安装说明

> 本功能是可选的**第三 TTS 引擎**，让你的副驾声音更自然、更有情感（开心/生气/难过等语气）。默认仍是 `sherpa` 本地女声，装好 CosyVoice 后手动切换，失败会自动回退，不影响原功能。

## 一、这是什么 / 为什么这么装

| 项 | 说明 |
|----|------|
| **作用** | 用 CosyVoice2（阿里通义开源，Apache 2.0）替换默认的 VITS 女声，自然度更高、支持情绪语气控制 |
| **为什么独立环境** | CosyVoice2 依赖重（torch + transformers 锁版本 + pynini），会和主项目的 faster-whisper / sherpa-onnx 冲突，所以装在独立 `venv_cosyvoice/` 里，通过子进程通信 |
| **成本** | 磁盘约 7~8GB（依赖 2~3GB + 模型 4.5GB），一次性 |

## 二、前置条件

- Windows + NVIDIA 显卡（推荐，你有 RTX 5080 完美；没显卡也能 CPU 跑但慢）
- Python 3.11（和主项目一致；3.12/3.13 部分依赖可能没预编译包）
- 磁盘剩余空间 ≥ 10GB

## 三、安装步骤

> 最省事的方式：直接双击 `setup_cosyvoice.bat`，它会自动跑完「建 venv → 装依赖 → 下模型」。
> 下面的手动步骤是给脚本装失败时对照用的。

### 第 1 步：创建独立虚拟环境

打开 CMD，进入项目目录：

```cmd
cd /d 你的项目路径
python -m venv venv_cosyvoice
venv_cosyvoice\Scripts\activate.bat
```

激活后命令行前面会出现 `(venv_cosyvoice)`。

### 第 2 步：装 PyTorch（CUDA 版，关键）

有 N 卡必须装 CUDA 版，否则退 CPU 会很慢：

```cmd
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
```

> ⚠️ 这一步约 2~3GB，最耗时。装完验证（必须看到 `True`）：
> ```cmd
> python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
> ```

### 第 3 步：装 CosyVoice2 依赖（版本锁定，勿改）

CosyVoice 官方对版本卡得很死，必须按下面来：

```cmd
pip install transformers==4.40.1
pip install diffusers==0.29.0
pip install librosa==0.10.2
pip install soundfile
pip install onnxruntime
pip install hyperpyyaml==1.2.2
pip install inflect==7.3.1
pip install WeTextProcessing --no-deps
```

### 第 4 步：装 pynini（Windows 最容易卡的一步）

`pynini` 是文本正则化用的，Windows 无官方 pip wheel。按顺序试：

**办法 A（推荐）**：装预编译 wheel
```cmd
pip install pynini==2.1.5 --find-links https://download.pytorch.org/whl/cpu
```

**办法 B**：用 conda（若有 Anaconda/Miniconda）
```cmd
conda install -c conda-forge pynini==2.1.5
```

**办法 C**：先跳过。缺 pynini 时 WeTextProcessing 会降级，中文基本能合成，只是数字/符号读法略不准。

### 第 5 步：下载模型（约 4.5GB）

```cmd
pip install modelscope
python -c "from modelscope import snapshot_download; snapshot_download('iic/CosyVoice2-0.5B', local_dir='models/CosyVoice2-0.5B')"
```

> 走 ModelScope 魔搭（阿里 CDN），国内快。

### 第 6 步：启用

1. 退出独立环境：`deactivate`
2. 编辑项目 `.env`，改一行：
   ```
   TTS_ENGINE=cosyvoice
   ```
3. 重启 `start.bat`

主程序启动时会自动拉起 `cosyvoice_server.py` 子进程加载模型（首次几秒~十几秒），失败自动回退 sherpa。

## 四、相关配置（.env）

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `TTS_ENGINE` | `sherpa` | 改成 `cosyvoice` 启用 |
| `COSYVOICE_HOST` | `127.0.0.1` | 子进程地址，一般不用改 |
| `COSYVOICE_PORT` | `9860` | 子进程端口 |
| `COSYVOICE_EMOTION` | `true` | 是否启用情绪语气控制（推荐开） |
| `COSYVOICE_DEVICE` | `auto` | auto/cuda/cpu，一般 auto 即可 |

## 五、常见问题

**Q：装完启动没反应，还是 sherpa 的声音？**
看控制台日志，搜 `[tts]` 开头的行。会打印是「CosyVoice 就绪」还是「回退到 sherpa」以及原因。多数是模型目录不对或 pynini 缺失。

**Q：报 `inference_instruct2` 参数错误？**
CosyVoice 官方仓库不同版本的 API 签名略有差异，把完整报错发我调。

**Q：CPU 跑太慢？**
正常。CosyVoice 是 0.5B 模型，CPU 上合成比实时还慢（一句话 2~3 秒）。强烈建议 GPU（CUDA 版 torch）。

**Q：想换回原来的声音？**
`.env` 里 `TTS_ENGINE` 改回 `sherpa` 即可，随时可切。

## 六、情绪联动（自动生效）

启用 CosyVoice 后，副驾说话会带上情绪语气，和桌宠表情同步：

| 情绪标签 | 语音语气 | 桌宠表情 |
|---------|---------|---------|
| `[开心]` | 开心、轻快、上扬 | 爱心眼/笑脸 |
| `[生气]` | 生气、不耐烦、急促 | 屑屑眼/生气 |
| `[难过]` | 温柔、低落、放慢 | 流泪/黑脸 |
| `[惊讶]` | 惊讶、好奇、上扬 | 圈圈眼/星星眼 |
| `[平静]` | 平静、自然 | 默认 |
