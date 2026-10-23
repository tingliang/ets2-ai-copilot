"""
M1 mock 测试：在非 Windows 环境（如本沙箱）验证事件检测逻辑。

telemetry.py 依赖 Windows 命名共享内存（mmap tagname），
沙箱是 Linux 读不到，所以这里用假的 TelemetryData 序列驱动 EventDetector，
验证事件触发是否正确。

运行：python mock_test.py
"""
from __future__ import annotations

from telemetry import TelemetryData
from events import EventDetector


def make(**kw) -> TelemetryData:
    d = TelemetryData()
    d.sdk_active = True
    for k, v in kw.items():
        setattr(d, k, v)
    return d


def main():
    det = EventDetector()
    prev = None

    print("=== M1 事件检测 mock 测试 ===\n")

    # 场景 1：正常行驶（不应触发任何事件）
    cur = make(speed_ms=80 / 3.6, speed_kmh=80, gear=12, fuel=300,
               fuel_capacity=800, fuel_warning=False)
    evs = det.detect(prev, cur)
    print(f"[正常行驶 80km/h] 触发 {len(evs)} 个事件（应为 0）")
    for e in evs:
        print("  ", e)
    prev = cur

    # 场景 2：突然碰撞（加速度突变）
    cur = make(speed_kmh=60, gear=10, fuel=290, fuel_capacity=800,
               accel_x=0.0, accel_y=0.0, accel_z=0.0)
    det.detect(prev, cur)  # 先稳定一帧
    cur2 = make(speed_kmh=55, gear=9, fuel=290, fuel_capacity=800,
                accel_x=7.5, accel_y=1.2, accel_z=0.3, user_brake=1.0)
    evs = det.detect(cur, cur2)
    print(f"\n[碰撞场景] 触发 {len(evs)} 个事件（应包含 accident）")
    for e in evs:
        print("  ", e)
    prev = cur2

    # 场景 3：低油
    cur = make(speed_kmh=70, gear=11, fuel=45, fuel_capacity=800,
               fuel_warning=True, fuel_range=120)
    evs = det.detect(prev, cur)
    print(f"\n[低油场景] 触发 {len(evs)} 个事件（应包含 low_fuel）")
    for e in evs:
        print("  ", e)
    prev = cur

    # 场景 4：超速
    cur = make(speed_kmh=95, gear=12, fuel=300, fuel_capacity=800,
               speed_limit=60)
    evs = det.detect(prev, cur)
    print(f"\n[超速场景 95km/h 限速60] 触发 {len(evs)} 个事件（应包含 speeding）")
    for e in evs:
        print("  ", e)
    prev = cur

    # 场景 5：任务开始
    cur = make(speed_kmh=0, gear=0, fuel=300, fuel_capacity=800,
               on_job=True, city_src="Berlin", city_dst="Prague",
               cargo="Potatoes", planned_distance_km=350, job_income=12800)
    evs = det.detect(prev, cur)
    print(f"\n[任务开始] 触发 {len(evs)} 个事件（应包含 job_started）")
    for e in evs:
        print("  ", e)
    prev = cur

    # 场景 6：任务送达 + 罚款
    cur = make(speed_kmh=0, gear=0, fuel=280, fuel_capacity=800,
               on_job=True, job_delivered=True, fined=True,
               fine_amount=1200, fine_offence="Speeding", cargo="Potatoes")
    evs = det.detect(prev, cur)
    print(f"\n[送达+罚款] 触发 {len(evs)} 个事件（应包含 job_delivered + fined）")
    for e in evs:
        print("  ", e)

    print("\n=== mock 测试结束 ===")


if __name__ == "__main__":
    main()
