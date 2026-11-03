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


def _asset(name: str) -> str:
    return os.path.join(ROOT, name)


# 一个全局事件总线：后端线程往里推事件，前端通过 js_api 轮询/回调
class PetAPI:
    """暴露给前端 JS 的 API（pywebview 会把实例方法映射到 window.pywebview.api.*）。"""

    def __init__(self):
        self._events: list[dict] = []
        self._lock = threading.Lock()
        self.personas: list[dict] = []  # 主程序推送的人设清单 [{name, display, emoji, current}]

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

    def switch_persona(self, name: str) -> dict:
        """前端右键菜单「切换人设」：通过 stdout 上报命令给主程序。

        桌宠是独立子进程，人设由主程序（llm.py）管理，这里只能「上报请求」，
        主程序监听 stdout 的 CMD: 行来实际切换。返回 {ok, name} 供前端提示。
        """
        try:
            print(f"CMD:{json.dumps({'type': 'switch_persona', 'name': name}, ensure_ascii=False)}", flush=True)
            return {"ok": True, "name": name}
        except Exception as e:
            return {"ok": False, "name": name, "error": str(e)}

    def list_personas(self) -> list:
        """返回可用人设清单（主程序启动时通过 stdin 推送过来，这里只是兜底）。"""
        return self.personas

    def quit_app(self):
        """右键菜单「退出」：通知主程序整体退出，并关闭本窗口。

        通过 stdout 上报 CMD:{quit_app} 给主程序（主程序会优雅地停 TTS、
        关事件线程、释放资源）。同时立即关闭本窗口——即使独立运行（无主程序
        监听）也能正常退出，上报被忽略即可。
        """
        try:
            print(f"CMD:{json.dumps({'type': 'quit_app'})}", flush=True)
        except Exception:
            pass
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
        if event.get("type") == "personas":
            # 主程序推送可用人设清单：既存到 api 供前端主动拉取，也转发给前端事件队列
            api.personas = event.get("personas", [])
            api.push(event)
            continue
        api.push(event)


def main():
    api = PetAPI()

    # 后台线程：从 stdin 读事件（由主程序 PetBridge 推送；独立运行时静默待命）
    threading.Thread(target=stdin_reader, args=(api,), daemon=True).start()

    html_path = _asset("pet.html")
    # 防缓存：Qt WebEngine 会缓存本地文件，加时间戳强制每次启动加载最新版
    html_url = html_path + "?v=" + str(int(time.time()))

    # 窗口形态自动选择：
    #   1. 装了 PySide6 → Qt 后端透明窗口（Qt 的 WA_TranslucentBackground
    #      机制成熟可靠，是 Windows 上真透明的正解）
    #   2. 没装 → WinForms 后端深色圆角卡片窗（稳定兜底）
    try:
        import PySide6  # noqa: F401
        use_qt = True
    except ImportError:
        use_qt = False

    def on_ready():
        # 窗口真正创建并显示后，才启动「置顶保活」线程。
        # 关键：不能在 webview.start() 之前就起线程——那时窗口还没创建，
        # 反复 SetWindowPos 会干扰窗口初始化，导致窗口不显示。
        from win32helpers import ensure_topmost  # 见下方，同目录工具函数
        try:
            ensure_topmost(window)
        except Exception as e:
            print(f"[pet] 置顶设置失败（不影响显示）: {e}")

    if use_qt:
        print("[pet] 使用 Qt 后端：透明窗口模式")
        mode = "transparent"
        window = webview.create_window(
            "ETS2 AI 副驾·桌宠",
            url=html_url + "&mode=" + mode,
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
        webview.start(on_ready, gui="qt")
    else:
        print("[pet] 未安装 PySide6，使用卡片窗模式（想要透明窗口请: pip install PySide6）")
        mode = "card"
        window = webview.create_window(
            "ETS2 AI 副驾·桌宠",
            url=html_url + "&mode=" + mode,
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
        webview.start(on_ready)


if __name__ == "__main__":
    main()
