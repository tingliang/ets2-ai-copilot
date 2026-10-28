"""
DeepSeek 对话模块：流式输出 + 小满人设 + 上下文压缩（省 token）。

设计：
- system prompt = persona.md（人设）+ 可选的当前车况快照（临时注入，不进历史）
- 历史保留最近 HISTORY_KEEP 轮；超过时把更早的对话压成一段摘要（一次额外调用），
  之后作为长期记忆放在 system prompt 尾部，控制 token 增长。
- max_tokens 限制回答长度（省 token + 减延迟 + 方便 TTS）。
"""
from __future__ import annotations

import json
import os
from typing import Generator, Optional

from openai import OpenAI

import config

HISTORY_KEEP = 12          # 保留最近 12 条消息（user+assistant 各算一条）
SUMMARY_TRIGGER = 24       # 历史超过这么多条时触发压缩

_PERSONAS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "personas")
_LEGACY_PERSONA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona.md")
_MEMORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")


def load_persona() -> str:
    """根据 config.PERSONA 读取对应人设文件。

    优先找 personas/<name>.md；找不到则回退到旧的 persona.md；
    再找不到用内置兜底（小满），保证程序不崩。
    """
    name = getattr(config, "PERSONA", "xiaoman") or "xiaoman"
    # 1) 优先 personas/<name>.md
    path = os.path.join(_PERSONAS_DIR, f"{name}.md")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    # 2) 回退旧 persona.md
    if os.path.exists(_LEGACY_PERSONA_PATH):
        with open(_LEGACY_PERSONA_PATH, encoding="utf-8") as f:
            return f.read().strip()
    # 3) 兜底（理论上不会走到）
    return "你是 ETS2 里陪玩家开车聊天的副驾，说简体中文，简短回复。"



class CopilotLLM:
    def __init__(self):
        self.client = OpenAI(
            api_key=config.DEEPSEEK_API_KEY,
            base_url=config.DEEPSEEK_BASE_URL,
        )
        self.model = config.DEEPSEEK_MODEL
        self.persona = load_persona()
        self.history: list[dict] = []   # [{"role","content"}]
        self.memory: str = ""           # 压缩后的长期摘要
        self._load_memory()

    # ---------- 长期记忆落盘 ----------
    def _load_memory(self):
        try:
            with open(_MEMORY_PATH, encoding="utf-8") as f:
                data = json.load(f)
            self.memory = data.get("memory", "") or ""
            if self.memory:
                print(f"[llm] 已加载长期记忆（{len(self.memory)} 字）")
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[llm] 加载记忆失败（忽略）: {e}")

    def _save_memory(self):
        try:
            with open(_MEMORY_PATH, "w", encoding="utf-8") as f:
                json.dump({"memory": self.memory}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[llm] 保存记忆失败（忽略）: {e}")

    # ---------- system prompt ----------
    def _system(self, telemetry_snapshot: Optional[str]) -> str:
        parts = [self.persona]
        if self.memory:
            parts.append("【之前的对话记忆（摘要）】\n" + self.memory)
        if telemetry_snapshot:
            parts.append("【当前车况（临时参考，勿复述）】\n" + telemetry_snapshot)
        return "\n\n".join(parts)

    # ---------- 上下文压缩 ----------
    def _maybe_compress(self):
        if len(self.history) < SUMMARY_TRIGGER:
            return
        old = self.history[:-HISTORY_KEEP]
        self.history = self.history[-HISTORY_KEEP:]
        text = "\n".join(f"{m['role']}: {m['content']}" for m in old)
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content":
                        "把下面一段用户和副驾的对话压缩成不超过 100 字的中文摘要，"
                        "保留关键事实（聊过的话题、去过的地方、用户偏好），不要发挥。"},
                    {"role": "user", "content": text},
                ],
                max_tokens=150,
                temperature=0.3,
            )
            new_summary = resp.choices[0].message.content.strip()
            self.memory = (self.memory + "\n" + new_summary).strip() if self.memory else new_summary
            self._save_memory()
        except Exception as e:
            # 压缩失败不影响主流程，只是丢掉最老的历史
            print(f"[llm] 上下文压缩失败（忽略）: {e}")

    # ---------- 主入口 ----------
    def chat(self, user_text: str,
             telemetry_snapshot: Optional[str] = None) -> Generator[str, None, None]:
        """流式对话：yield 一段段回复文本。"""
        self._maybe_compress()
        messages = [{"role": "system", "content": self._system(telemetry_snapshot)}]
        messages += self.history
        messages.append({"role": "user", "content": user_text})

        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=config.LLM_MAX_TOKENS,
            temperature=0.9,
            stream=True,
        )
        reply = ""
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                reply += delta
                yield delta

        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": reply})

    def comment_on_event(self, event_text: str) -> str:
        """针对一个车况事件，生成一句小满风格的口头评论（简短、非流式）。"""
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.persona},
                    {"role": "user", "content":
                        f"刚才发生了一件事：{event_text}\n"
                        "请用你的口吻，随口点评一句（1 句，20 字以内，简体中文，"
                        "不要提问、不要复述事件本身）。"},
                ],
                max_tokens=40,
                temperature=0.9,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"[llm] 事件评论生成失败: {e}")
            return ""

    def proactive_chat(self, telemetry_snapshot: Optional[str] = None) -> str:
        """主动找话题：根据当前车况，随口说一句闲话（1 句，别啰嗦）。"""
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system(telemetry_snapshot)},
                    {"role": "user", "content":
                        "现在一路平稳，你主动找个话题跟我聊一句吧。"
                        "可以聊聊路况、风景、这趟货、或者随便吐槽。"
                        "要求：1 句话，30 字以内，简体中文，自然口语，不要提问、不要复述车况。"},
                ],
                max_tokens=60,
                temperature=0.9,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"[llm] 主动闲聊生成失败: {e}")
            return ""

    def reset(self):
        self.history.clear()
        self.memory = ""
        self._save_memory()


# ---------- 车况快照（省 token：只挑关键字段，一行内联） ----------
def telemetry_snapshot(d) -> str:
    """把 TelemetryData 压成一行中文，供 LLM 参考。"""
    if d is None or not getattr(d, "sdk_active", False):
        return "（游戏未运行）"
    speed = f"{d.speed_kmh:.0f}km/h"
    gear = {-1: "R", 0: "N"}.get(d.gear, str(d.gear))
    fuel_pct = f"{d.fuel / d.fuel_capacity * 100:.0f}%" if d.fuel_capacity else "?"
    parts = [f"速度{speed}", f"档位{gear}", f"油量{fuel_pct}"]

    if d.fuel_warning:
        parts.append("油量告警")

    if d.speed_limit:
        parts.append(f"限速{d.speed_limit:.0f}km/h")
    else:
        parts.append("限速未知（无导航/无限制路段）")

    # 任务信息：接任务时给出起点→终点、货物、全程/剩余/已跑、收入
    if d.on_job:
        route = f"{d.city_src}→{d.city_dst}"
        parts.append(f"任务{route} 运{d.cargo or '货物'}")
        if d.planned_distance_km:
            parts.append(f"全程{d.planned_distance_km}km")
        # route_distance 是「导航剩余距离」（米），换算成 km 才是剩余路程
        remaining_km = d.route_distance / 1000.0 if d.route_distance else 0.0
        if d.route_distance:
            parts.append(f"剩余{remaining_km:.0f}km")
        if d.planned_distance_km and d.route_distance:
            done_km = d.planned_distance_km - remaining_km
            if done_km >= 0:
                parts.append(f"已跑{done_km:.0f}km")
        if d.job_income:
            parts.append(f"收入¥{d.job_income}")
    else:
        parts.append("未接任务")

    # 车辆里程表总里程（和上面的「任务剩余/全程里程」区分开，避免 LLM 混淆）
    if d.truck_odometer:
        parts.append(f"车辆里程表{d.truck_odometer:.0f}km")

    return " ".join(parts)
