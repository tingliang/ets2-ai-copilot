"""
语音指令模块：识别玩家说的话里的「控制指令」，模拟按键控制 ETS2。

设计：
- 指令用关键词匹配（不发给 DeepSeek，省 token + 零延迟）。
- 命中后模拟按下对应按键（pynput 的 Controller），并返回一句副驾确认语。
- 注意：模拟按键作用于「当前前台窗口」，因此只在 ETS2 是前台时生效。
  你开车时游戏本来就在前台，所以正常没问题。

指令（可自行扩展 COMMANDS 表）：
  打开收音机 / 开电台 / 放个歌   → 按 R
  关收音机 / 关电台              → 按 R（再按一次关闭）
  切台 / 换台 / 下一个电台        → Ctrl+右箭头
  上一个电台 / 回上个台           → Ctrl+左箭头
  静音 / 关声音                   → Ctrl+M
"""
from __future__ import annotations

import time
from typing import Optional

# 每个指令：关键词列表 + 要按的键（pynput Key 或字符）
# 结构：{ "关键词": [(按键组合), ...] }
# 匹配规则：玩家说的一句话里「包含」任意关键词即命中
COMMANDS = [
    {
        "keywords": ["打开收音机", "开收音机", "开电台", "打开电台", "放个歌", "放首歌", "开音乐"],
        "keys": [("r",)],
        "reply": "收音机开了。",
    },
    {
        "keywords": ["关收音机", "关掉收音机", "关电台", "关掉电台", "关音乐", "关掉音乐", "关了电台", "把电台关", "把收音机关"],
        "keys": [("r",)],
        "reply": "关了。",
    },
    {
        "keywords": ["切台", "换台", "下一个电台", "下个台", "换一个台", "换个台", "换电台", "换个电台"],
        "keys": [("ctrl", "right")],
        "reply": "换个台。",
    },
    {
        "keywords": ["上一个电台", "上个台", "回上个台", "前一个电台"],
        "keys": [("ctrl", "left")],
        "reply": "回上一个。",
    },
    {
        "keywords": ["静音", "关声音", "把声音关", "没声音"],
        "keys": [("ctrl", "m")],
        "reply": "静音了。",
    },
]


def _resolve_key(k: str):
    """把按键描述字符串转成 pynput 的 key 对象或字符。"""
    from pynput import keyboard
    special = {
        "ctrl": keyboard.Key.ctrl,
        "ctrl_l": keyboard.Key.ctrl_l,
        "ctrl_r": keyboard.Key.ctrl_r,
        "shift": keyboard.Key.shift,
        "alt": keyboard.Key.alt,
        "enter": keyboard.Key.enter,
        "space": keyboard.Key.space,
        "left": keyboard.Key.left,
        "right": keyboard.Key.right,
        "up": keyboard.Key.up,
        "down": keyboard.Key.down,
        "esc": keyboard.Key.esc,
        "tab": keyboard.Key.tab,
    }
    return special.get(k.lower(), k)


def match_command(text: str) -> Optional[dict]:
    """判断玩家说的这句话是不是一个指令。是则返回对应指令 dict，否则 None。"""
    t = text.strip()
    for cmd in COMMANDS:
        for kw in cmd["keywords"]:
            if kw in t:
                return cmd
    return None


def execute_command(cmd: dict) -> None:
    """模拟按下指令对应的按键组合。"""
    from pynput import keyboard
    controller = keyboard.Controller()
    for combo in cmd["keys"]:
        keys = [_resolve_key(k) for k in combo]
        # 先按下修饰键，再按主键，最后逆序释放
        for k in keys:
            controller.press(k)
        time.sleep(0.05)
        for k in reversed(keys):
            controller.release(k)
        time.sleep(0.05)


def run_command(text: str) -> Optional[str]:
    """完整流程：匹配指令 → 执行 → 返回副驾确认语。不是指令返回 None。"""
    cmd = match_command(text)
    if cmd is None:
        return None
    try:
        execute_command(cmd)
        print(f"[指令] {cmd['reply']}")
        return cmd["reply"]
    except Exception as e:
        print(f"[指令] 执行失败: {e}")
        return "按不动，你自己来吧。"
