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

# 车况事件类型 → 桌宠动作组候选（按优先级取当前模型真实存在的第一个）。
# 不同模型动作组名差异很大：鲸鱼娘/大肥鱼有 8 个丰富动作，官方示例模型
# 只有 Idle/TapBody。所以用候选列表 + 前端「命中第一个存在的」策略，
# 找不到就退 TapBody → Idle（前端负责兜底）。
EVENT_ACTION_MAP = {
    # 事故：惊跳/夸张动作
    "accident": ["SelfieQuick", "TakeOff", "FingergunOut", "Hammer"],
    # 超速/罚款：生气动作
    "speeding": ["Hammer", "FingergunOut", "TakeOff"],
    "fined": ["Hammer", "FingergunOut", "TakeOff"],
    # 低油：沮丧动作
    "low_fuel": ["OpenCase", "DogtailBack", "RaiseHandBack"],
    # 任务开始/送达：开心动作
    "job_started": ["Selfie", "Ketchup", "DogtailOut", "FingergunOut"],
    "job_delivered": ["Selfie", "Ketchup", "DogtailOut", "FingergunOut"],
    # 过收费站/加油：不播动作，仅表情（calm）
    "tollgate": [],
    "refuel": [],
}


class PetBridge:
    """主程序侧：拉起桌宠子进程并推事件。启动失败自动降级为 no-op。"""

    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stdout_thread: threading.Thread | None = None
        self._on_command = None  # 收到桌宠上报命令时的回调：fn(command: dict)

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
            # stdin 用于主程序→桌宠推事件；stdout 用于桌宠→主程序上报命令（反向通道）。
            # stderr 继承：桌宠的 [pet] 日志直接打到主程序控制台，方便诊断。
            # 关键：必须 text=True + utf-8，否则 stdin 是二进制流，push() 写 str 会
            # 抛 TypeError（被 except 静默吞掉），事件一条都发不出去。
            self._proc = subprocess.Popen(
                [sys.executable, poc],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                cwd=root,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            # 启动反向通道监听：桌宠通过 stdout 上报命令（如切换人设）
            self._stdout_thread = threading.Thread(
                target=self._watch_stdout, args=(self._proc,), daemon=True
            )
            self._stdout_thread.start()
            print("[pet] 桌宠窗口已启动（右键可切换形象/人设/退出）")
            return True
        except Exception as e:
            print(f"[pet] 桌宠启动失败（不影响对话，可加 --no-pet 关闭）: {e}")
            self._proc = None
            return False

    def _watch_stdout(self, proc):
        """后台监听桌宠子进程 stdout，解析以 CMD: 前缀开头的命令行。"""
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("CMD:"):
                    try:
                        cmd = json.loads(line[4:])
                    except json.JSONDecodeError:
                        continue
                    if self._on_command:
                        try:
                            self._on_command(cmd)
                        except Exception:
                            pass
                # 其余日志行（[pet] 等）透传到主程序控制台
                else:
                    print(line, flush=True)
        except Exception:
            pass

    def set_command_handler(self, fn):
        """设置桌宠命令回调：fn(command: dict)。"""
        self._on_command = fn

    def stop(self):
        """关闭桌宠：先优雅发 quit，超时则强杀，最后确保进程句柄释放。

        注意顺序：terminate()/kill() 只是发出信号，必须再 wait() 才能
        让子进程被回收（否则会残留僵尸句柄）。所有操作都要 try 包裹，
        避免 Ctrl+C 退出时二次抛异常。
        """
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        try:
            self.push({"type": "quit"})
        except Exception:
            pass
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=3)
                except Exception:
                    pass

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

    def play_action(self, emotion: str, candidates: list[str]):
        """事件驱动动作：换表情 + 播放候选动作（前端取第一个存在的）。"""
        if emotion:
            self.push({"type": "emotion", "value": emotion})
        if candidates:
            self.push({"type": "action", "candidates": candidates})
