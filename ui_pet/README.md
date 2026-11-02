# 桌宠 UI —— 阶段 0 POC 验证说明

> 本阶段目标：验证「透明置顶小窗 + Live2D 会动形象 + 说话张嘴 + 字幕气泡」这条技术路是否走得通。
> **独立脚本，不碰现有 main.py，验证期间零风险，可随时删除。**

## 怎么跑

```bash
python ui_pet/poc.py
```

运行后会弹出一个**透明、无边框、置顶**的小窗，默认形象是 **DS 鲸鱼娘**（DeepSeek 鲸鱼娘二创），它会呼吸、眨眼、待机摆动，并循环模拟「说话」：嘴巴张合 + 头顶弹出字幕气泡。

> 这个 POC 里还没有接真实的 DeepSeek 对话和 TTS，是用 `demo_thread` 模拟的说话事件，目的只是验证「窗口 + 形象 + 嘴 + 字幕」能跑通。

## 内置形象（9 个，右键菜单可切换）

**右键窗口 → 「切换形象」**，即可实时切换，无需改代码。动作菜单会随当前形象自动适配。

| 形象 | 目录 | 来源与许可 |
|---|---|---|
| DS 鲸鱼娘（默认） | `ui_pet/models/WhaleGirl/` | B站@氵六青 制作，CC BY-NC-SA 4.0，**署名·非商业·相同方式共享**；角色链：上善无形 → ZipZipPipe → 氵六青 |
| Mao 猫娘 | `ui_pet/models/Mao/` | Live2D 官方免费示例 |
| Natori | `ui_pet/models/Natori/` | Live2D 官方免费示例 |
| Hiyori 桃濑日和 | `ui_pet/models/Hiyori/` | Live2D 官方免费示例 |
| Haru | `ui_pet/models/Haru/` | Live2D 官方免费示例 |
| Ren | `ui_pet/models/Ren/` | Live2D 官方免费示例 |
| Rice 米团子（Q版） | `ui_pet/models/Rice/` | Live2D 官方免费示例 |
| Wanko 小狗（Q版） | `ui_pet/models/Wanko/` | Live2D 官方免费示例 |
| Mark | `ui_pet/models/Mark/` | Live2D 官方免费示例 |

> 8 个 Live2D 官方示例模型均来自 [Live2D/CubismWebSamples](https://github.com/Live2D/CubismWebSamples)，随官方 SDK 免费提供。

## 验证清单（请逐条确认）

| # | 检查项 | 预期 | 通过？ |
|---|---|---|---|
| 1 | 窗口是否透明 | 能看到桌面壁纸，不是品红/白底/黑底方块 | |
| 2 | 是否置顶 | 切到游戏/浏览器，小人仍在最上面 | |
| 3 | 形象是否动 | 有呼吸、眨眼、头发摆动 | |
| 4 | 说话张嘴 | 字幕弹出时，嘴巴在张合 | |
| 5 | 字幕气泡 | 白色气泡出现在头顶，几秒后淡出 | |
| 6 | 能否拖动 | 鼠标按住能拖动窗口位置 | |

## 依赖说明

- **Python 依赖**：`pywebview`，`pip install pywebview`
- **前端依赖**：已全部下载到 `ui_pet/vendor/`（pixi.js + Live2D Cubism Core + pixi-live2d-display），**本地加载，不依赖 CDN**
- **模型**：见上表，已全部下载到 `ui_pet/models/`

## 窗口形态

- **默认（装 PySide6）**：Qt 后端**透明窗口**，`WA_TranslucentBackground` 机制，Windows 上真透明正解
- **兜底（未装 PySide6）**：深色渐变圆角卡片窗

```bash
# 想要透明窗口，先装 Qt 后端：
pip install PySide6 qtpy
```

## 已知风险 / 可能的问题

1. **模型画布比例不同**：换模型后若人物过大/过小/位置偏，调 `ui_pet/pet.html` 里 `layoutModel()` 的布局参数（`bubbleSpace` / `anchor` / `model.x` / `model.y`）
2. **模型版权**：DS 鲸鱼娘是社区二创（非商业可用、需署名）；公开发布前记得在 README 里保留署名。商业分发需要联系原作者授权
3. **部分动作播放失败**：个别模型的动作文件加载偶发失败（已加诊断），POC 阶段可接受，后续阶段再排查

## 下一步（阶段 1）

POC 通过后：
- 把 `demo_thread` 替换成真实事件总线，接上 `llm.chat()` 流式输出
- 副驾回复 → 桌宠出字幕 + 换表情（鲸鱼娘自带 44 个表情可用）
- 接真实 TTS 的「说话状态」驱动张嘴

---

**请把你跑出来的结果告诉我**（尤其第 1 项透明窗口是否正常，以及有没有报错截图），我再决定是继续阶段 1 还是先修透明问题。
