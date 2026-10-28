# ETS2 AI 副驾 — 需求文档

> 本文档记录项目的核心需求与**硬性工程规范**。
> 所有后续开发（含 AI 协作、新功能、打包）都必须遵守，否则视为不符合需求。

---

## 一、项目定位

给《欧洲卡车模拟 2》(ETS2) 做的 AI 副驾，核心目标是**「国内玩家充一点 DeepSeek 钱，就能简单跑起来」**：

- 对话大脑：DeepSeek（OpenAI 兼容接口，玩家自己的 Key）
- 语音输入：本地 faster-whisper（中文）
- 语音输出：默认本地离线女声，可切换在线音色
- 目标用户：小黑盒 / 国内社区的普通玩家（**非程序员**）

---

## 二、核心需求

| 编号 | 需求 | 说明 |
|------|------|------|
| R1 | 开箱即用 | 下载 → 改一个 Key → 双击就能跑，10 分钟内出第一句语音 |
| R2 | 国内稳定 | 默认路径不依赖境外接口（GitHub、HuggingFace、edge-tts 均不可作默认） |
| R3 | 兼容主流配置 | 不强制 GPU；有 N 卡自动加速、无 N 卡纯 CPU 也能跑 |
| R4 | 自然女声 | 默认音色为自然中文女声，副驾人设（小满/阿澈）可配置 |
| R5 | 语音控制 | 说「打开收音机」等指令本地执行（模拟按键），不耗 token |
| R6 | 车况感知 | 读取 ETS2 遥测（速度/油量/任务/事件），主动播报 |
| R7 | 低成本 | 除 DeepSeek 外不产生额外费用 |

---

## 三、硬性工程规范（必须遵守）

### 规范 1：所有 `.bat` 脚本必须纯 ASCII（防乱码）⚠️ 最高优先级

**问题背景**：Windows `cmd` 默认用 GBK 解析 `.bat` 文件，而代码仓库里的文件多为 UTF-8 编码。若 `.bat` 里写了中文（echo 或 rem 注释），在中文 Windows 上会显示为乱码（如 `鍓笯`）。此问题已多次出现。

**强制要求**：

1. **所有 `.bat` / `.cmd` 文件的全部内容（含 `echo` 输出、`rem`/`::` 注释、变量、提示文字）必须使用纯 ASCII 字符（英文、数字、标点）。**
2. **禁止在 `.bat` 里出现任何中文、全角标点、emoji。** 需要提示用户的内容一律用英文，或引导用户去看 README（README 是 Markdown，可放心用中文）。
3. 文件编码用 **UTF-8 无 BOM** 或 **GBK 均可**，但因内容纯 ASCII，两者等价、都不乱码。**不要依赖 `chcp 65001`**（不同 Windows 版本行为不一致，不可靠）。
4. 写完任何 `.bat` 后，必须自检：文件中不存在任何非 ASCII 字节。检查命令（Linux/macOS）：

   ```bash
   LC_ALL=C grep -P '[^\x00-\x7F]' *.bat
   ```

   无输出 = 通过；有输出 = 必须把对应字符改成 ASCII。

5. **例外**：`.ps1`（PowerShell 脚本）和 `.py` 文件不受此限制（它们能正确处理 UTF-8），中文照常使用。

**适用范围**：`start.bat`、`install.bat`、`fix_cuda_path.bat`、`fix_pip_path.bat`、`setup_gpu_stt.bat` 及今后所有新增的批处理脚本。

### 规范 2：敏感信息不入库

- `.env`（含 DeepSeek Key）、`memory.json`（个人偏好）、`__pycache__/` 必须被 `.gitignore` 忽略。
- 任何打包（zip）都必须排除 `.env`；git 历史中不得出现真实 API Key。

### 规范 3：默认配置面向小白

- `.env.example` 只要求填 `DEEPSEEK_API_KEY` 一个必填项，其余给合理默认值。
- 默认引擎选择「国内稳定优先」：TTS 默认 `sherpa`（本地离线），STT 默认 `auto`（有 N 卡自动加速、无则 CPU）。
- 模型下载默认走 **ModelScope 魔搭**（国内 CDN），不得默认依赖 HuggingFace（国内访问不稳定）。

### 规范 4：容错优先，不崩溃

- 无麦克风也能启动（语音接入后自动恢复）。
- GPU/CUDA 加载失败自动回退 CPU。
- 本地 TTS 模型缺失或加载失败，自动回退在线引擎，不中断主流程。

---

## 四、技术选型（已定）

| 环节 | 方案 | 备注 |
|------|------|------|
| 对话 LLM | DeepSeek `deepseek-chat` | OpenAI 兼容流式 |
| 语音识别 STT | faster-whisper `base` | `auto` 探测 GPU，失败回落 CPU |
| 语音合成 TTS | sherpa-onnx + `vits-melo-tts-zh_en`（默认） | 本地离线，模型从 ModelScope 下载 |
| TTS 备选 | edge-tts「陕西小妮」 | `TTS_ENGINE=edge` 切换，需联网 |
| 模型下载 | ModelScope 魔搭 | 国内稳定 |
| 依赖安装 | pip（清华镜像） | 一键脚本里指定国内源 |

---

## 五、待办 / Roadmap

- [ ] `install.bat` 一键安装（装依赖 + 下模型 + 生成 `.env`）
- [ ] `download_models.py` 或整合进 install.bat
- [ ] 发布打包（Gitee + 网盘多镜像分发）
- [ ] 发布帖 FAQ（常见问题跟帖内容）
