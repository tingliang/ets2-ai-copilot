# ETS2 AI 副驾（小满）

一个给《欧洲卡车模拟 2》(ETS2) 用的 AI 副驾，用 **DeepSeek** 做对话大脑，支持**中文语音输入 / 中文语音输出**。

她叫**小满**——毒舌心软、爱卡车的陕西妹子。会陪你聊天、吐槽你的车技、主动报告路况事件（事故/超速/低油/罚款），还能记住你聊过的事。

> 原理类似 Ride Along，但架构是**一组简单 Python 脚本**（命令行运行），不依赖任何 UI 程序。

---

## 功能一览

- 🎤 **按住 Alt 说话**，松开即识别（本地 Whisper，中文）
- 🗣️ **中文语音回答**（edge-tts，陕西小妮音色，免费）
- 📡 **实时读取车况**（速度/档位/油量/限速/任务/坐标，通过官方 SCS Telemetry SDK）
- 🚨 **事件自动播报**：事故、超速、低油、罚款、收费站、任务开始/送达
- 💬 **主动闲聊**：长时间安静时小满会主动找话题（带冷却，不话痨）
- 🧠 **长期记忆**：记住你聊过的事、跑过的路线，重启不丢
- ⚙️ **可配置**：音量、热键、音色、语速都能在 `.env` 里改

---

## 环境要求

- **Windows**（遥测读共享内存依赖 Windows API）
- **Python 3.11**（不要用 3.12+，部分依赖兼容性差）
- **ffmpeg**（edge-tts 解码音频用，见下文安装）

---

## 安装步骤

### 1. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

> 如果 `pip` 命令找不到，用 `python -m pip install -r requirements.txt`

### 2. 安装 ffmpeg

edge-tts 合成完 mp3 后需要 ffmpeg 解码成能播放的音频。

- 推荐：`winget install ffmpeg`
- 或去 [ffmpeg.org](https://ffmpeg.org) 下载，解压后把 `bin` 目录加入系统 PATH
- 验证：命令行输入 `ffmpeg -version` 能出结果即可

### 3. 放置遥测插件（关键，决定能不能读车况）

需要把 `scs-telemetry.dll` 放到游戏目录的这个**精确路径**：

```
<Steam目录>\steamapps\common\Euro Truck Simulator 2\bin\win_x64\plugins\scs-telemetry.dll
```

- 如果 `plugins` 文件夹不存在，**手动新建**
- DLL 从官方仓库下载：[scs-sdk-plugin Releases](https://github.com/RenCloud/scs-sdk-plugin/releases)
- **放置后必须重启游戏**才会加载插件

### 4. 配置 API Key

复制模板为本地配置，填上你的 DeepSeek Key：

```bash
copy .env.example .env
```

然后编辑 `.env`，把 `DEEPSEEK_API_KEY=` 后面填上你的 key。

---

## 运行

```bash
python main.py
```

启动后：
- **按住 Alt 说话**，松开即识别并让小满回答
- **朗读中按 Alt** 会打断她
- **Ctrl+C** 退出

诊断模式（看车况是否正常读取）：

```bash
python main.py --debug-event
```

---

## 使用顺序说明

- **只要对话**：开脚本和开游戏的顺序无所谓
- **要事件播报**：建议**先开游戏、进驾驶界面，再开脚本**。不过现在支持**自动重连**，先开脚本后开游戏也会自动接上遥测，无需重启脚本。

---

## 配置项（`.env`）

| 配置 | 说明 | 默认 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek Key（**必填**） | 无 |
| `DEEPSEEK_MODEL` | 模型 | `deepseek-chat` |
| `WHISPER_MODEL` | 识别模型（越大越准越慢） | `base` |
| `PTT_KEY` | 按住说话热键 | `alt` |
| `TTS_VOICE` | 音色 | `zh-CN-shaanxi-XiaoniNeural` |
| `TTS_RATE` | 语速 | `+0%` |
| `TTS_VOLUME` | 音量 0~1 | `1.0` |

热键支持：`alt` / `ctrl` / `shift` / `space` / `enter` / `tab` / 单个字母（如 `f`）。

---

## 常见问题

**Q: 启动后 `[telemetry] 尚未连接`，事件不触发？**
A: 确认 `scs-telemetry.dll` 在 `bin\win_x64\plugins\` 下，且游戏已重启并进入驾驶界面。用 `--debug-event` 看 `sdk=` 是否为 True。

**Q: 报 `cublas64_12.dll` 找不到？**
A: 这是 faster-whisper 想用 GPU 但没装 CUDA。在 `.env` 里确保 `WHISPER_DEVICE=cpu`（默认就是 cpu）。

**Q: 报 `[WinError 2] 系统找不到指定的文件`？**
A: 缺 ffmpeg。按上面「安装 ffmpeg」步骤处理。

**Q: 语音识别不准 / 太慢？**
A: 把 `.env` 里 `WHISPER_MODEL` 从 `base` 改成 `small`（更准但更慢）或 `tiny`（更快但更糙）。

---

## 目录结构

```
ets2-ai-copilot/
├── main.py            # 主循环（录音、对话、事件、主动闲聊）
├── telemetry.py       # 读 ETS2 共享内存（Windows API）
├── events.py          # 事件检测（事故/超速/低油/罚款等）
├── llm.py             # DeepSeek 对话 + 记忆 + 车况快照
├── stt.py             # 本地中文语音识别（Whisper）
├── tts.py             # 中文朗读（edge-tts）+ 填充音
├── config.py          # 配置加载 + 热键解析
├── persona.md         # 小满人设
├── .env.example       # 配置模板（复制成 .env 使用）
└── memory.json        # 长期记忆（运行后自动生成，不进 git）
```

---

## 技术说明

- **遥测**：通过 SCS 官方 Telemetry SDK 插件，把车况写入 Windows 命名共享内存 `Local\SCSTelemetry`，Python 用 `ctypes` 调 `OpenFileMappingW` 读取。
- **对话**：DeepSeek 的 OpenAI 兼容接口，流式输出。
- **语音**：输入用本地 faster-whisper（不联网、不消耗 DeepSeek token）；输出用 edge-tts（免费微软在线 TTS）。
- **省 token**：上下文超限自动压缩成摘要；事件评论限制 20 字；主动闲聊带冷却。
