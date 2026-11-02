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

import ctypes
import os
import sys
import threading
import time

# 关键：WebView2 的透明背景色必须在创建窗口之前设置。
# 这是微软官方的透明机制：'00000000' = ARGB 全透明（前两位是 alpha）。
# pywebview 6.x 的 transparent=True 会同时把 WebView2 DefaultBackgroundColor
# 设为 Transparent，环境变量作为双保险。
os.environ["WEBVIEW2_DEFAULT_BACKGROUND_COLOR"] = "00000000"

import webview

# 关键：必须在任何会加载后端引擎前设置好，并显式指定 EdgeChromium（透明窗口支持最好）
# 注意：必须在 import webview 之后、create_window 之前设置

ROOT = os.path.dirname(os.path.abspath(__file__))

# 色键透明（Windows 上唯一稳定的透明窗口方案）：
#   pywebview 的 transparent=True 在 Windows 上官方文档明确「不支持」，
#   正确做法是「色键」：窗口背景和 HTML 背景都用品红色，
#   再用 Win32 分层窗口 API 把这个颜色的像素全部抠成透明（桌宠程序的 classic 做法）。
#   注意：品红只能用在背景，页面里不能有别的品红元素，否则会被一起抠掉。
CHROMA_HEX = "FE00FE"          # 品红（HTML 里用）
CHROMA_COLORREF = 0x00FE00FE   # Win32 COLORREF (0x00BBGGRR，品红 R/B 对称无需换算)


def _get_hwnd(window):
    """尽力拿到窗口的原生句柄（HWND），拿不到返回 None。"""
    # 方式 1：window.native 是 pywebview 的 BrowserView 包装对象，有 .Handle
    native = getattr(window, "native", None)
    if native is not None:
        handle = getattr(native, "Handle", None)
        if handle is not None:
            try:
                return int(handle)
            except (TypeError, ValueError):
                pass
            try:
                return handle.ToInt64()  # pythonnet System.IntPtr
            except Exception:
                pass
    # 方式 2：用 Win32 按窗口标题找句柄（兜底）
    try:
        user32 = ctypes.windll.user32
        title = "ETS2 AI 副驾·桌宠"
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            return hwnd
    except Exception:
        pass
    return None


def _apply_chroma_key(window):
    """给窗口启用色键透明（必须在窗口句柄就绪后调用）。"""
    user32 = ctypes.windll.user32
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    LWA_COLORKEY = 0x00000001
    hwnd = _get_hwnd(window)
    if not hwnd:
        print("[pet] 尚未拿到窗口句柄，稍后重试…")
        return False
    exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, exstyle | WS_EX_LAYERED)
    user32.SetLayeredWindowAttributes(hwnd, CHROMA_COLORREF, 0, LWA_COLORKEY)
    print("[pet] 色键透明已启用")
    return True


def _apply_chroma_key_retry(window, tries=20, interval=0.5):
    """反复尝试启用色键透明，直到成功或超时。

    窗口原生句柄（native/Handle）在 GUI 循环启动后是异步就绪的，
    所以这里轮询等待，而不是在回调里一次性硬取。
    """
    for i in range(tries):
        try:
            if _apply_chroma_key(window):
                return
        except Exception as e:
            if i == tries - 1:
                print(f"[pet] 色键透明设置失败（背景会显示为品红色）: {e}")
                return
        time.sleep(interval)
    print("[pet] 色键透明设置失败（重试超时，背景会显示为品红色）")


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

    # 窗口形态自动选择：
    #   1. 装了 PySide6 → Qt 后端透明窗口（Qt 的 WA_TranslucentBackground
    #      机制成熟可靠，是 Windows 上真透明的正解）
    #   2. 没装 → WinForms 后端深色圆角卡片窗（稳定兜底）
    try:
        import PySide6  # noqa: F401
        use_qt = True
    except ImportError:
        use_qt = False

    if use_qt:
        print("[pet] 使用 Qt 后端：透明窗口模式")
        mode = "transparent"
        window = webview.create_window(
            "ETS2 AI 副驾·桌宠",
            url=html_path + "?mode=" + mode,
            js_api=api,
            width=280,
            height=460,
            frameless=True,
            on_top=True,
            shadow=False,
            easy_drag=True,
            transparent=True,       # Qt 后端原生支持透明
            background_color="#000000",
            resizable=False,
        )
        webview.start(gui="qt")
    else:
        print("[pet] 未安装 PySide6，使用卡片窗模式（想要透明窗口请: pip install PySide6）")
        mode = "card"
        window = webview.create_window(
            "ETS2 AI 副驾·桌宠",
            url=html_path + "?mode=" + mode,
            js_api=api,
            width=280,
            height=460,
            frameless=True,
            on_top=True,
            shadow=False,
            easy_drag=True,
            transparent=False,
            background_color="#1B1B2A",
            resizable=False,
        )
        webview.start()


if __name__ == "__main__":
    main()
