"""
事件检测模块。

从连续两帧 TelemetryData 之间判断「值得副驾开口」的事件。
M1 阶段只做检测 + 打印日志，不接 DeepSeek。

事件类型：
    - accident      事故/碰撞（加速度突变 + 减速剧烈）
    - low_fuel      油量过低（低于告警阈值）
    - speeding      超速（超过当前限速）
    - job_started   任务开始（onJob 从 False→True）
    - job_delivered 任务送达
    - fined         被罚款
    - tollgate      过收费站
    - refuel        加油

用法：
    from events import EventDetector
    det = EventDetector()
    prev = None
    while ...:
        d = t.read()
        for e in det.detect(prev, d):
            print(e)
        prev = d
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from telemetry import TelemetryData

# 事故检测阈值（可调）
ACCEL_IMPACT_THRESHOLD = 6.0      # 合成加速度瞬时突变（m/s²），超过视为撞击
BRAKE_SPIKE_THRESHOLD = 0.95      # 刹车踏板瞬间打满
FUEL_LOW_LITERS = 50.0            # 油量低于此值（升）触发低油提醒
SPEEDING_MARGIN_KMH = 5.0         # 超过限速 + 此余量才算超速
SPEEDING_FALLBACK_KMH = 130.0     # 无导航/无限速时的纯速度兜底阈值
SPEEDING_COOLDOWN = 30.0          # 超速播报冷却（秒），期间不重复报
ACCIDENT_COOLDOWN = 10.0          # 事故播报冷却（秒）


@dataclass
class Event:
    type: str
    message: str
    detail: dict = field(default_factory=dict)

    def __str__(self):
        return f"[事件:{self.type}] {self.message}"


class EventDetector:
    def __init__(self):
        self._on_job_prev = False
        # 一次性事件（罚款/收费站/加油/送达等）的上一帧布尔状态，
        # 只在 False→True 的上升沿触发一次，避免标志持续为 True 时反复播报烧 token。
        self._prev_flags: dict[str, bool] = {}
        # 持续型事件（超速/事故）的冷却时间戳，避免短时间反复刷屏
        self._last_speeding = 0.0
        self._last_accident = 0.0

    def _edge(self, key: str, cur: bool) -> bool:
        """返回当前帧是否是该布尔标记的上升沿（False→True）。"""
        was = self._prev_flags.get(key, False)
        self._prev_flags[key] = cur
        return cur and not was

    def detect(self, prev: Optional[TelemetryData], cur: TelemetryData) -> list[Event]:
        """对比上一帧 prev 和当前帧 cur，返回新触发的事件列表。"""
        if not cur.sdk_active:
            # 游戏断开/暂停时清空边沿状态，避免重连后误报旧事件
            self._prev_flags.clear()
            self._on_job_prev = False
            return []

        events: list[Event] = []

        # —— 事故/碰撞：加速度从低到高的上升沿突变 ——
        # 用「当前帧合成加速度 > 阈值，且上一帧低于阈值」判断上升沿，
        # 避免碰撞发生后加速度残留导致连续误报；再加冷却，同一事故只报一次。
        if prev is not None:
            cur_mag = abs(cur.accel_x) + abs(cur.accel_y) + abs(cur.accel_z)
            prev_mag = abs(prev.accel_x) + abs(prev.accel_y) + abs(prev.accel_z)
            hard_brake = cur.user_brake > BRAKE_SPIKE_THRESHOLD
            now = time.time()
            if now - self._last_accident >= ACCIDENT_COOLDOWN and (
                (cur_mag > ACCEL_IMPACT_THRESHOLD and prev_mag <= ACCEL_IMPACT_THRESHOLD)
                or (cur_mag > 3.0 and prev_mag <= 3.0 and hard_brake)
            ):
                self._last_accident = now
                events.append(Event(
                    "accident",
                    f"疑似碰撞！加速度 {cur_mag:.1f} m/s²" + ("，急刹" if hard_brake else ""),
                    {"accel_mag": cur_mag, "hard_brake": hard_brake},
                ))

        # —— 低油（上升沿：只在进入低油状态的瞬间报一次）——
        _low_fuel = cur.fuel_warning or (cur.fuel_capacity > 0 and cur.fuel < FUEL_LOW_LITERS)
        if self._edge("low_fuel", _low_fuel):
            events.append(Event(
                "low_fuel",
                f"油量偏低：{cur.fuel:.0f}L / {cur.fuel_capacity:.0f}L，剩余里程约 {cur.fuel_range:.0f}km",
                {"fuel": cur.fuel, "capacity": cur.fuel_capacity},
            ))

        # —— 超速 ——
        # 注意：ETS2 的 speed_limit（navigation_speed_limit）在没有导航路线、
        # 德国高速无限制路段、或关闭 Route Advisor 时恒为 0。
        # 所以只有「读到真实限速值」时才做严格超速判断；
        # 否则退化为纯速度兜底（>130km/h 才提醒），避免完全静默。
        # 超速是持续状态，加冷却时间，避免连续跑高速时反复刷屏烧 token。
        _speeding = False
        if cur.speed_limit > 0:
            _speeding = cur.speed_kmh > cur.speed_limit + SPEEDING_MARGIN_KMH
        else:
            _speeding = cur.speed_kmh > SPEEDING_FALLBACK_KMH
        if _speeding and time.time() - self._last_speeding >= SPEEDING_COOLDOWN:
            self._last_speeding = time.time()
            if cur.speed_limit > 0:
                events.append(Event(
                    "speeding",
                    f"超速：{cur.speed_kmh:.0f}km/h（限速 {cur.speed_limit:.0f}）",
                    {"speed": cur.speed_kmh, "limit": cur.speed_limit},
                ))
            else:
                events.append(Event(
                    "speeding",
                    f"速度很快：{cur.speed_kmh:.0f}km/h（当前路段未提供限速）",
                    {"speed": cur.speed_kmh, "limit": 0},
                ))

        # —— 任务开始（onJob 上升沿）——
        if cur.on_job and not self._on_job_prev:
            events.append(Event(
                "job_started",
                f"新任务：{cur.city_src}→{cur.city_dst}，货物 {cur.cargo}，"
                f"计划 {cur.planned_distance_km}km，收入 ¥{cur.job_income}",
                {"src": cur.city_src, "dst": cur.city_dst, "cargo": cur.cargo},
            ))
        self._on_job_prev = cur.on_job

        # —— 任务送达（上升沿）——
        if self._edge("job_delivered", cur.job_delivered):
            events.append(Event(
                "job_delivered",
                f"任务送达！货物 {cur.cargo}",
                {"cargo": cur.cargo},
            ))

        # —— 罚款（上升沿，只报一次）——
        if self._edge("fined", cur.fined):
            events.append(Event(
                "fined",
                f"被罚款 ¥{cur.fine_amount}（{cur.fine_offence or '违规'}）",
                {"amount": cur.fine_amount, "offence": cur.fine_offence},
            ))

        # —— 收费站（上升沿）——
        if self._edge("tollgate", cur.tollgate):
            events.append(Event("tollgate", "通过收费站"))

        # —— 加油（上升沿）——
        if self._edge("refuel", cur.refuel):
            events.append(Event("refuel", "正在加油"))

        return events
