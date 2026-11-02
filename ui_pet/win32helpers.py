"""Windows 窗口工具函数（桌宠专用）。

只做一件事：把桌宠窗口可靠地钉在最顶层，同时不干扰窗口创建/显示。
所有操作都 try 包裹，失败不影响桌宠正常显示。
"""
from __future__ import annotations

import ctypes
import threading
import time


def _get_hwnd(window) -> int | None:
    """尽力拿到窗口原生句柄（HWND）。Qt 后端用 winId()，WinForms 用 Handle。"""
    native = getattr(window, "native", None)
    if native is None:
        return None
    # Qt 后端：native.winId() 直接返回 HWND
    try:
        wid = native.winId()
        if wid:
            return int(wid)
    except Exception:
        pass
    # WinForms 后端：native.Handle（pythonnet System.IntPtr）
    handle = getattr(native, "Handle", None)
    if handle is not None:
        try:
            return int(handle)
        except (TypeError, ValueError):
            pass
        try:
            return handle.ToInt64()
        except Exception:
            pass
    return None


def ensure_topmost(window, interval: float = 1.0) -> threading.Thread:
    """后台线程持续把窗口钉在最顶层（每 interval 秒重钉一次）。

    只调用 SetWindowPos(HWND_TOPMOST)，不修改任何窗口扩展样式，
    避免 WS_EX_NOACTIVATE 等标志导致窗口不渲染/不可见。

    返回守护线程（随进程退出），可忽略。
    """
    def _loop():
        user32 = ctypes.windll.user32
        HWND_TOPMOST = -1
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOACTIVATE = 0x0010
        SWP_SHOWWINDOW = 0x0040

        # 等句柄就绪（窗口创建后回调里调用，通常已就绪，但保险起见轮询）
        hwnd = None
        for _ in range(40):
            hwnd = _get_hwnd(window)
            if hwnd:
                break
            time.sleep(0.25)
        if not hwnd:
            return

        while True:
            try:
                user32.SetWindowPos(
                    hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                    SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
                )
            except Exception:
                pass
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t
