"""
阶段 0 POC：验证「透明置顶桌宠窗口 + Live2D 会动形象 + 说话张嘴 + 字幕气泡」技术可行性。

独立脚本，不依赖 main.py 的任何逻辑，可单独运行：
    python ui_pet/poc.py

验证内容：
1. pywebview 能否开透明、无边框、置顶的小窗
2. Live2D 模型（官方示例 Haru）能否渲染、呼吸眨眼
3. Python -> JS 双向通信（事件总线）是否通
4. 说话时嘴巴张合 + 字幕气泡

用到的模型：Live2D 官方示例模型 Haru（免费，随 SDK 提供），
首次运行会自动从 CDN 下载，若 CDN 不通会提示手动放置。

⚠️ 透明窗口是最大风险点：pywebview 的 transparent=True 依赖后端引擎
（Windows 上默认 EdgeChromium），个别系统/驱动下可能不生效，届时回退为
「不透明圆角窗口」作为兜底。
"""
from __future__ import annotations

import os
import sys
import threading
import time

import webview

# 关键：必须在任何会加载后端引擎前设置好，并显式指定 EdgeChromium（透明窗口支持最好）
# 注意：必须在 import webview 之后、create_window 之前设置

ROOT = os.path.dirname(os.path.abspath(__file__))


def _asset(name: str) -> str:
    return os.path.join(ROOT, name)


# 一个全局事件总线：后端线程往里推事件，前端通过 js_api 轮询/回调
class PetAPI:
    """暴露给前端 JS 的 API（pywebview 会把实例方法映射到 window.pywebview.api.*）。"""

    def __init__(self):
        self._events: list[dict] = []
        self._lock = threading.Lock()

    def push(self, event: dict):
        with self._lock:
            self._events.append(event)

    # ---- 前端轮询用的方法 ----
    def drain_events(self):
        """前端定时调用，取走积压的事件。"""
        with self._lock:
            evs = self._events
            self._events = []
            return evs

    def hello(self, name: str) -> str:
        return f"你好，{name}！我是你的桌宠。"


def demo_thread(api: PetAPI):
    """模拟「副驾在说话」的场景：循环推字幕 + 情绪 + 说话状态。"""
    lines = [
        ("哎哟，就这车技还跑长途呢，回去重考本吧！", "angry"),
        ("前面有辆 Scania R730，帅啊。", "happy"),
        ("油不多了，前面服务区加一下。", "calm"),
        ("行，知道了。", "idle"),
    ]
    i = 0
    while True:
        text, emotion = lines[i % len(lines)]
        api.push({"type": "emotion", "value": emotion})
        api.push({"type": "speak_start", "text": text})
        time.sleep(2.5)
        api.push({"type": "speak_stop"})
        time.sleep(1.5)
        i += 1


def main():
    api = PetAPI()

    # 后台模拟线程（POC 阶段没有真对话，先模拟说话事件）
    threading.Thread(target=demo_thread, args=(api,), daemon=True).start()

    html_path = _asset("pet.html")

    # 打开本地 HTML。透明窗口关键参数：
    #   frameless=True      无边框
    #   on_top=True         置顶
    #   transparent=True    透明背景（EdgeChromium 后端会直接把窗口背景设为 Transparent）
    #   easy_drag=True      可直接拖动
    #   background_color    仅作为「页面加载前」的占位色，必须是 6 位 hex（#RRGGBB），
    #                       不能带 alpha（8 位），否则 pywebview 6.x 会抛 ValueError
    window = webview.create_window(
        "ETS2 AI 副驾·桌宠",
        url=html_path,
        js_api=api,
        width=380,
        height=520,
        frameless=True,
        on_top=True,
        transparent=True,
        easy_drag=True,
        background_color="#000000",
        resizable=False,
    )

    webview.start()


if __name__ == "__main__":
    main()
