"""
桌宠窗口进程（阶段一）：透明置顶小窗 + Live2D 会动形象 + 说话张嘴 + 字幕气泡 + 换表情。

两种运行方式：
1. 由主程序 main.py 通过 PetBridge 拉起（推荐）：主程序往本进程 stdin 逐行写
   JSON 事件，本进程读 stdin 推给前端。事件类型见 stdin_reader()。
2. 独立手动运行（调试用）：
       python ui_pet/poc.py
   没有 stdin 输入，桌宠静默待命，可右键选动作/切换形象。

透明窗口依赖 PySide6 + qtpy（Qt 后端原生透明）；未安装时回退深色卡片窗。
"""
from __future__ import annotations

import ctypes
import json
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
    # 方式 1：window.native 是 pywebview 的 BrowserView 包装对象
    native = getattr(window, "native", None)
    if native is not None:
        # 1a. WinForms 后端：native.Handle（pythonnet System.IntPtr）
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
        # 1b. Qt 后端：native.winId() 直接返回 HWND（int）
        try:
            wid = native.winId()
            if wid:
                return int(wid)
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


def _keep_topmost(window, stop_event):
    """后台保活：把桌宠窗口持续钉在最顶层，且点击时不抢游戏焦点。

    场景：ETS2 用「无边框窗口」模式时，普通置顶窗口仍可能被游戏反复
    抢占；这里每秒用 SetWindowPos(HWND_TOPMOST) 重新置顶（用 SWP_NOACTIVATE
    标志避免每次置顶时抢走游戏焦点，但不动窗口的扩展样式，保证窗口正常显示）。

    注意：若游戏是「独占全屏(exclusive fullscreen)」，Windows 会把整块
    屏幕交给游戏独占渲染，任何窗口都无法盖上去——这种情况请把游戏显示
    模式改成「无边框窗口 / borderless windowed」。
    """
    user32 = ctypes.windll.user32
    HWND_TOPMOST = -1
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040

    # 等窗口句柄就绪（GUI 循环启动后异步可用）
    hwnd = None
    for _ in range(60):
        hwnd = _get_hwnd(window)
        if hwnd:
            break
        time.sleep(0.25)
    if not hwnd:
        print("[pet] 未拿到窗口句柄，置顶保活未启动")
        return

    print("[pet] 置顶保活已启动（每 1 秒重新置顶）")
    while not stop_event.is_set():
        try:
            # 只重钉最顶层 + 不抢激活，不再动窗口扩展样式
            user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
        except Exception:
            pass
        time.sleep(1.0)


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

    def quit_app(self):
        """右键菜单「退出」：关闭窗口。"""
        import webview as _w
        for w in _w.windows:
            try:
                w.destroy()
            except Exception:
                pass


def stdin_reader(api: PetAPI):
    """从 stdin 逐行读 JSON 事件（由主程序的 PetBridge 写入），推给前端。

    事件格式（每行一个 JSON 对象）：
        {"type": "speak_start", "text": "..."}  开始说话：张嘴 + 字幕
        {"type": "speak_stop"}                   说话结束：闭嘴
        {"type": "emotion", "value": "happy"}    切换表情
        {"type": "quit"}                         主程序要求退出

    独立手动运行（python ui_pet/poc.py）时没有 stdin 输入，就静默待命；
    本线程是守护线程，随进程退出，不会卡住窗口。
    """
    # 主程序（PetBridge）用 UTF-8 写 stdin，这里强制按 UTF-8 解码，
    # 避免 Windows 上默认 GBK 把中文事件读成乱码（进而字幕/表情全废）。
    if sys.platform == "win32":
        try:
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            print(f"[pet] 忽略非法事件行: {line[:80]}")
            continue
        if event.get("type") == "quit":
            print("[pet] 收到退出指令")
            api.quit_app()
            break
        api.push(event)


def main():
    api = PetAPI()

    # 后台线程：从 stdin 读事件（由主程序 PetBridge 推送；独立运行时静默待命）
    threading.Thread(target=stdin_reader, args=(api,), daemon=True).start()

    html_path = _asset("pet.html")
    stop_topmost = threading.Event()  # 置顶保活线程的停止开关（守护线程，随进程退出）

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
        # 置顶保活：盖在游戏上面 + 点击不抢游戏焦点
        threading.Thread(target=_keep_topmost,
                         args=(window, stop_topmost), daemon=True).start()
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
        # 置顶保活：盖在游戏上面 + 点击不抢游戏焦点
        threading.Thread(target=_keep_topmost,
                         args=(window, stop_topmost), daemon=True).start()
        webview.start()


if __name__ == "__main__":
    main()
