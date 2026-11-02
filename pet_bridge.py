"""
桌宠桥接模块：主程序在独立子进程里跑桌宠窗口，通过 stdin 管道推事件。

这样把桌宠和主程序解耦：
- 桌宠崩了不影响驾驶/对话主流程
- 避免 Qt GUI 必须占主线程的冲突（桌宠的 webview.start() 在子进程里跑）
- 不碰 main.py 的主循环结构

事件类型（与 ui_pet/poc.py 的 stdin_reader 对应）：
    {"type": "speak_start", "text": "..."}   开始说话：张嘴 + 字幕
    {"type": "speak_stop"}                   说话结束：闭嘴
    {"type": "emotion", "value": "happy"}    切换表情（key 见 EMOTIONS）
    {"type": "quit"}                         主程序要求退出
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

# 桌宠表情 key（前端 pet.html 的 EMOTION_MAP 用这些 key 映射到具体模型表情）
EMOTIONS = {
    "happy": "happy",
    "angry": "angry",
    "sad": "sad",
    "surprised": "surprised",
    "calm": "calm",
    "idle": "idle",
}

# 车况事件类型 → 桌宠表情 key
EVENT_EMOTION_MAP = {
    "accident": "surprised",
    "low_fuel": "sad",
    "speeding": "angry",
    "job_started": "happy",
    "job_delivered": "happy",
    "fined": "angry",
    "tollgate": "calm",
    "refuel": "calm",
}


class PetBridge:
    """主程序侧：拉起桌宠子进程并推事件。启动失败自动降级为 no-op。"""

    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    # ---------- 生命周期 ----------
    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        """启动桌宠子进程。成功返回 True，失败（缺依赖等）返回 False。"""
        if not self._enabled:
            return False
        root = os.path.dirname(os.path.abspath(__file__))
        poc = os.path.join(root, "ui_pet", "poc.py")
        try:
            # stdout/stderr 继承：桌宠的 [pet] 日志直接打到主程序控制台，方便诊断
            # 关键：必须 text=True + utf-8，否则 stdin 是二进制流，push() 写 str 会
            # 抛 TypeError（被 except 静默吞掉），事件一条都发不出去。
            self._proc = subprocess.Popen(
                [sys.executable, poc],
                stdin=subprocess.PIPE,
                cwd=root,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            print("[pet] 桌宠窗口已启动（右键可切换形象/退出）")
            return True
        except Exception as e:
            print(f"[pet] 桌宠启动失败（不影响对话，可加 --no-pet 关闭）: {e}")
            self._proc = None
            return False

    def stop(self):
        """关闭桌宠：先优雅发 quit，超时则强杀。"""
        if self._proc is None:
            return
        try:
            self.push({"type": "quit"})
            try:
                self._proc.stdin.close()
            except Exception:
                pass
        except Exception:
            pass
        try:
            self._proc.wait(timeout=3)
        except Exception:
            try:
                self._proc.terminate()
            except Exception:
                pass
        self._proc = None

    # ---------- 事件推送 ----------
    def push(self, event: dict):
        """推一个事件给桌宠（一行 JSON）。桌宠未运行/已退出时静默忽略。"""
        if self._proc is None or self._proc.poll() is not None:
            return
        try:
            with self._lock:
                self._proc.stdin.write(json.dumps(event, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
        except Exception:
            pass

    def speak_start(self, text: str, emotion: str | None = None):
        """副驾开始说话：先换表情，再张嘴 + 出字幕。"""
        if emotion:
            self.push({"type": "emotion", "value": emotion})
        self.push({"type": "speak_start", "text": text or ""})

    def speak_stop(self):
        """副驾说完：闭嘴。"""
        self.push({"type": "speak_stop"})

    def set_emotion(self, emotion: str):
        """只换表情，不说话。"""
        self.push({"type": "emotion", "value": emotion})
