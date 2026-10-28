# ETS2 AI 副驾

一个给《欧洲卡车模拟 2》(ETS2) 用的 AI 副驾，用 **DeepSeek** 做对话大脑，支持**中文语音输入 / 中文语音输出**。

默认人设是**小满**——毒舌心软、爱卡车的陕西妹子。会陪你聊天、吐槽你的车技、主动报告路况事件（事故/超速/低油/罚款），还能记住你聊过的事。也支持切换成**高冷「阿澈」**等其他可配置人设。

> 原理类似 Ride Along，但架构是**一组简单 Python 脚本**（命令行运行），不依赖任何 UI 程序。

---

## 功能一览

- 🎤 **按住 Alt 说话**，松开即识别（本地 Whisper，中文）
- 🗣️ **中文语音回答**：默认**本地离线女声**（sherpa-onnx + 中文 VITS，国内零依赖、断网可用），也可切回 edge-tts「陕西小妮」
- ⚡ **GPU 加速识别**（可选，`WHISPER_DEVICE=auto` 自动探测 N 卡，识别从 ~1 秒降到 ~0.06 秒）
- 🎭 **可配置人设**：小满（毒舌）/ 阿澈（高冷），`PERSONA` 一键切换，可自行添加
- 🎮 **语音控制指令**：说「打开收音机」「切台」「静音」即可控制游戏（本地执行，不耗 token）
- 📡 **实时读取车况**（速度/档位/油量/限速/任务/坐标，通过官方 SCS Telemetry SDK）
- 🚨 **事件自动播报**：事故、超速、低油、罚款、收费站、任务开始/送达
- 💬 **主动闲聊**：长时间安静时副驾会主动找话题（带冷却，不话痨）
- 🧠 **长期记忆**：记住你聊过的事、跑过的路线，重启不丢
- ⚙️ **可配置**：人设、音量、热键、音色、语速都能在 `.env` 里改

---

## 环境要求

- **Windows**（遥测读共享内存依赖 Windows API）
- **Python 3.11**（不要用 3.12+，部分依赖兼容性差）
- **ffmpeg**（仅 `TTS_ENGINE=edge` 时解码音频用；默认 `sherpa` 引擎不需要）

---

## 安装步骤

### 0. 放置遥测插件（读车况必需，一次性）

需要把 `scs-telemetry.dll` 放到游戏目录的这个**精确路径**：

```
<Steam目录>\steamapps\common\Euro Truck Simulator 2\bin\win_x64\plugins\scs-telemetry.dll
```

- 如果 `plugins` 文件夹不存在，**手动新建**
- DLL 从官方仓库下载：[scs-sdk-plugin Releases](https://github.com/RenCloud/scs-sdk-plugin/releases)
- **放置后必须重启游戏**才会加载插件
- 不放置也能正常聊天，只是没有实时车况和事件播报

### 方式一：一键安装（推荐，小白首选）

双击 `install.bat`，脚本会自动完成全部安装：

| 步骤 | 内容 |
|------|------|
| 检测环境 | 自动检测 Python / pip，没有会提示你安装 |
| 装依赖 | 用**清华镜像**装全部 Python 包（国内快） |
| 检测显卡 | 自动探测 NVIDIA 显卡，有就装 CUDA 库启用 GPU 识别 |
| 下模型 | 从 **ModelScope 魔搭**下载本地模型（whisper + TTS，约 340MB，国内稳定） |
| 生成配置 | 自动创建 `.env` 配置模板 |

装完后：**打开 `.env` 填入你的 `DEEPSEEK_API_KEY`** → 双击 `start.bat` 启动。

> 全程不依赖境外站点（pip 走清华、模型走魔搭），国内开箱即用。

### 方式二：手动安装（进阶 / 老用户）

```bash
pip install -r requirements.txt
# 或 pip 不可用时：python -m pip install -r requirements.txt

# 下载本地模型（也可等首次运行时自动下载，但手动下更稳）
python download_models.py
```

> 如果 `pip` 命令找不到，双击 `fix_pip_path.bat` 修复，或直接用 `python -m pip` 代替。

**ffmpeg**（仅 `TTS_ENGINE=edge` 时需要，默认 sherpa 引擎不需要）：

```bash
winget install ffmpeg
```

**GPU 加速**：`install.bat` 已自动处理；手动方式可双击 `setup_gpu_stt.bat`。

> ⚠️ **RTX 50 系（Blackwell）注意**：GPU 模式已自动用 float16（避开 INT8 报错），并在启动时自动注册 CUDA DLL 路径。若重启后仍报 `cublas64_12.dll not found`，双击 `fix_cuda_path.bat` 永久修复。

### 配置 API Key

安装完成后，编辑 `.env`，把 `DEEPSEEK_API_KEY=` 后面填上你的 key（在 https://platform.deepseek.com 获取）。

---

## 运行

```bash
python main.py
```

启动后：
- **按住 Alt 说话**，松开即识别并让副驾回答
- **朗读中按 Alt** 会打断她
- **Ctrl+C** 退出

诊断模式：

```bash
python main.py --debug-event    # 看车况是否正常读取
python main.py --debug-timing   # 看 STT/LLM/TTS 各段耗时
```

---

## 使用顺序说明

- **只要对话**：开脚本和开游戏的顺序无所谓
- **要事件播报**：建议**先开游戏、进驾驶界面，再开脚本**。不过现在支持**自动重连**，先开脚本后开游戏也会自动接上遥测，无需重启脚本。

---

## 语音控制指令

按住 Alt 说下面的话，副驾会**本地模拟按键**控制游戏（不发给 DeepSeek，零延迟、不耗 token）：

| 你说的话 | 动作 | 副驾回应 |
|---------|------|---------|
| 「打开收音机」「开电台」「放个歌」 | 按 `R` | 收音机开了。 |
| 「关收音机」「把电台关了」 | 按 `R` | 关了。 |
| 「切台」「换个台」「下一个电台」 | `Ctrl+右箭头` | 换个台。 |
| 「上一个电台」 | `Ctrl+左箭头` | 回上一个。 |
| 「静音」「关声音」 | `Ctrl+M` | 静音了。 |

> ⚠️ **重要**：模拟按键作用于**当前前台窗口**。喊指令时请确保 ETS2 是当前窗口（开车时通常就是）。如果切到别的窗口再喊，按键会打到那个窗口。
>
> 想加更多指令（打开地图、车灯、定速巡航、喇叭等），编辑 `commands.py` 里的 `COMMANDS` 表即可。

---

## 人设切换

人设存在 `personas/` 目录下，每个 `.md` 文件一个人设。在 `.env` 里用 `PERSONA` 切换：

| 人设 | `PERSONA` 值 | 特点 |
|------|-------------|------|
| 小满 | `xiaoman`（默认） | 毒舌心软、卡车迷妹、慵懒幽默 |
| 阿澈 | `gaoleng` | 高冷疏离、话少克制、冷静毒舌 |

**自定义新人设**：在 `personas/` 下新建一个 `.md` 文件（如 `dongbei.md`），照着现有文件写性格/风格/事件反应，然后在 `.env` 里 `PERSONA=dongbei` 即可。

---

## 配置项（`.env`）

| 配置 | 说明 | 默认 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek Key（**必填**） | 无 |
| `DEEPSEEK_BASE_URL` | API 地址 | `https://api.deepseek.com/v1` |
| `DEEPSEEK_MODEL` | 模型 | `deepseek-chat` |
| `PERSONA` | 人设名（对应 `personas/<名>.md`） | `xiaoman` |
| `WHISPER_MODEL` | 识别模型（越大越准越慢） | `base` |
| `WHISPER_DEVICE` | 识别设备 `auto` / `cpu` / `cuda` | `auto` |
| `PTT_KEY` | 按住说话热键 | `alt` |
| `TTS_ENGINE` | 语音引擎 `sherpa`（本地离线）/ `edge`（需联网） | `sherpa` |
| `TTS_VOICE` | 音色（仅 edge 引擎生效） | `zh-CN-shaanxi-XiaoniNeural` |
| `TTS_RATE` | 语速（仅 edge 引擎生效） | `+0%` |
| `TTS_LOCAL_SPEED` | 本地引擎语速（越大越快） | `1.0` |
| `TTS_LOCAL_LEAD_SILENCE` | 本地引擎句首静音秒数（防首字被吞） | `0.12` |
| `TTS_VOLUME` | 音量，可 >1.0 放大 | `1.0` |
| `TTS_STREAM` | 按句流式朗读（边播边合成，降低等待） | `true` |

热键支持：`alt` / `ctrl` / `shift` / `space` / `enter` / `tab` / 单个字母（如 `f`）。

**可选音色**（`TTS_VOICE`，仅 `TTS_ENGINE=edge` 时生效）：

| 音色 ID | 特点 |
|---------|------|
| `zh-CN-shaanxi-XiaoniNeural` | 陕西口音女声（默认）|
| `zh-CN-XiaoxiaoNeural` | 温柔亲切女声 |
| `zh-CN-XiaoyiNeural` | 年轻活泼女声 |
| `zh-CN-YunxiNeural` | 年轻男声 |
| `zh-CN-YunjianNeural` | 沉稳男声（适合高冷人设）|
| `zh-CN-liaoning-XiaobeiNeural` | 东北口音女声 |

> **默认 `sherpa` 引擎**用本地离线中文女声（vits-melo-tts-zh_en），不依赖上述音色表，首次运行自动从 ModelScope 下载模型（约 190MB）。想要「陕西小妮」等口音音色，把 `TTS_ENGINE` 改成 `edge`（需联网）。

---

## 常见问题

**Q: 启动后 `[telemetry] 尚未连接`，事件不触发？**
A: 确认 `scs-telemetry.dll` 在 `bin\win_x64\plugins\` 下，且游戏已重启并进入驾驶界面。用 `--debug-event` 看 `sdk=` 是否为 True。

**Q: 报 `cublas64_12.dll` 找不到 / `Library cublas64_12.dll is not found`？**
A: 这是 GPU 识别的 CUDA 库加载问题。本程序已内置自动注册（`[cuda] 已注册 CUDA DLL 目录`）。若仍报错，先在 `.env` 里改 `WHISPER_DEVICE=cpu` 用 CPU 识别（同样可用，只是稍慢）。

**Q: 报 `[WinError 2] 系统找不到指定的文件`？**
A: 缺 ffmpeg。按上面「安装 ffmpeg」步骤处理。

**Q: 语音识别不准 / 太慢？**
A: 改 `.env` 里 `WHISPER_MODEL`（`small` 更准、`tiny` 更快）；有 N 卡可 `WHISPER_DEVICE=cuda` 上 GPU。

**Q: 语音指令没反应？**
A: 确认 ETS2 是当前前台窗口（模拟按键只作用于前台）。可先切回游戏再喊。

**Q: 模型下载失败 / 太慢？**
A: 本地模型走 ModelScope 魔搭（国内稳定）。失败时重跑 `python download_models.py` 会断点续传（已下完的文件自动跳过）；也可手动到 `https://modelscope.cn/models/` 搜 `faster-whisper-base` 和 `vits-melo-tts-zh_en` 下载整包，解压到 `models/` 下对应目录。

---

## 目录结构

```
ets2-ai-copilot/
├── main.py            # 主循环（录音、对话、指令、事件、主动闲聊）
├── telemetry.py       # 读 ETS2 共享内存（Windows API）
├── events.py          # 事件检测（事故/超速/低油/罚款等）
├── llm.py             # DeepSeek 对话 + 记忆 + 车况快照
├── stt.py             # 本地中文语音识别（Whisper，auto 自动探测 GPU）
├── tts.py             # 中文朗读（双引擎：sherpa 本地离线 / edge 在线）
├── test_tts.py        # 离线 TTS 音色试听脚本
├── commands.py        # 语音控制指令（模拟按键控制游戏）
├── config.py          # 配置加载 + 热键解析
├── download_models.py # 模型下载脚本（ModelScope 魔搭，国内稳定）
├── personas/          # 人设文件（xiaoman.md / gaoleng.md 等）
│   ├── xiaoman.md     # 小满（默认）
│   └── gaoleng.md     # 阿澈（高冷）
├── docs/              # 需求文档、开发规范
├── install.bat        # 一键安装（装依赖 + 下模型 + 生成配置）
├── start.bat          # 一键启动
├── setup_gpu_stt.bat  # GPU 识别配置脚本
├── fix_pip_path.bat   # 修复 pip 命令不可用
├── fix_cuda_path.bat  # 修复 cublas64_12.dll 报错（重启后）
├── .env.example       # 配置模板（复制成 .env 使用）
└── memory.json        # 长期记忆（运行后自动生成，不进 git）
```

---

## 技术说明

- **遥测**：通过 SCS 官方 Telemetry SDK 插件，把车况写入 Windows 命名共享内存 `Local\SCSTelemetry`，Python 用 `ctypes` 调 `OpenFileMappingW` 读取。
- **对话**：DeepSeek 的 OpenAI 兼容接口，流式输出。
- **语音输入**：本地 faster-whisper（不联网、不耗 DeepSeek token）；`auto` 自动探测 GPU 加速。
- **语音输出**：默认本地 sherpa-onnx + 中文 VITS（离线、国内零依赖、零成本）；可选 edge-tts（在线微软音）。
- **语音指令**：本地关键词匹配 + pynput 模拟按键，不走 DeepSeek。
- **省 token**：上下文超限自动压缩成摘要；事件评论限制 20 字；主动闲聊带冷却。
