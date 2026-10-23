"""
ETS2 / ATS 遥测读取器（Windows 版）。

从 scs-sdk-plugin 写入的命名共享内存 ``Local\\SCSTelemetry`` 中，
用 mmap + struct 解析出车况与任务数据。

字段偏移严格对照官方头文件 scs-telemetry-common.hpp
（RenCloud / alxwk 的 scs-sdk-plugin，PLUGIN_REVID 12）。

用法：
    from telemetry import Ets2Telemetry
    t = Ets2Telemetry()
    while True:
        data = t.read()
        print(data.speed_kmh, data.gear, data.fuel, ...)

注意：本模块只在 Windows 上工作（依赖命名共享内存）。
"""
from __future__ import annotations

import mmap
import struct
from dataclasses import dataclass, field

# 共享内存名（Windows 命名共享内存，注意反斜杠在 Python 字符串里要转义）
MMF_NAME = "Local\\SCSTelemetry"
# 映射区大小（头文件 SCS_PLUGIN_MMF_SIZE = 32KB，多取一点保险）
MMF_SIZE = 32 * 1024

_STRING_SIZE = 64


def _decode(buf: bytes) -> str:
    """把 C 字符串（UTF-8，遇 \\0 截断）解成 Python str。"""
    return buf.split(b"\0", 1)[0].decode("utf-8", errors="replace").strip()


def _read_string(mm: mmap.mmap, offset: int, size: int = _STRING_SIZE) -> str:
    return _decode(mm[offset:offset + size])


@dataclass
class TelemetryData:
    """一次读取的遥测快照。"""
    # —— 基础状态 ——
    sdk_active: bool = False
    paused: bool = False
    timestamp: int = 0
    game: int = 0  # 1=ETS2, 2=ATS, 0=未知

    # —— 行驶状态 ——
    speed_ms: float = 0.0          # 米/秒
    speed_kmh: float = 0.0         # 千米/时（换算）
    engine_rpm: float = 0.0
    gear: int = 0                  # -1=倒档, 0=空挡, 1..N=前进档
    cruise_control_speed: float = 0.0
    speed_limit: float = 0.0       # 当前路段限速（km/h 或 0）

    # —— 燃油 ——
    fuel: float = 0.0              # 升
    fuel_capacity: float = 0.0
    fuel_warning: bool = False
    fuel_range: float = 0.0        # 剩余里程

    # —— 输入 ——
    user_throttle: float = 0.0
    user_brake: float = 0.0
    user_steer: float = 0.0

    # —— 灯光 / 雨刷 / 转向 ——
    wipers: bool = False
    blinker_left_on: bool = False
    blinker_right_on: bool = False
    lights_beam_low: bool = False
    lights_beam_high: bool = False

    # —— 车辆 / 磨损 ——
    wear_engine: float = 0.0
    wear_transmission: float = 0.0
    wear_cabin: float = 0.0
    wear_chassis: float = 0.0
    truck_odometer: float = 0.0

    # —— 位置 ——
    coordinate_x: float = 0.0
    coordinate_y: float = 0.0
    coordinate_z: float = 0.0

    # —— 加速度（用于事故检测）——
    accel_x: float = 0.0
    accel_y: float = 0.0
    accel_z: float = 0.0

    # —— 任务信息 ——
    on_job: bool = False
    truck_brand: str = ""
    truck_name: str = ""
    cargo: str = ""
    city_src: str = ""
    city_dst: str = ""
    comp_src: str = ""
    comp_dst: str = ""
    planned_distance_km: int = 0
    route_distance: float = 0.0
    cargo_damage: float = 0.0
    job_income: int = 0

    # —— 事件标记（special_b 区）——
    job_finished: bool = False
    job_delivered: bool = False
    job_cancelled: bool = False
    fined: bool = False
    tollgate: bool = False
    ferry: bool = False
    train: bool = False
    refuel: bool = False
    fine_offence: str = ""
    fine_amount: int = 0


class Ets2Telemetry:
    """读 Windows 命名共享内存的遥测读取器。"""

    def __init__(self, name: str = MMF_NAME, size: int = MMF_SIZE):
        self._name = name
        self._size = size
        self._mm = mmap.mmap(-1, size, tagname=name, access=mmap.ACCESS_READ)

    def read(self) -> TelemetryData:
        d = TelemetryData()
        mm = self._mm
        mm.seek(0)

        # —— 第一区（offset 0）——
        d.sdk_active = struct.unpack_from("?", mm, 0)[0]
        d.paused = struct.unpack_from("?", mm, 4)[0]
        d.timestamp = struct.unpack_from("Q", mm, 8)[0]

        # game 标识在第二区 scs_values.game，偏移 = 40 + 4*3 = 52
        d.game = struct.unpack_from("I", mm, 52)[0]

        # —— 整数区：档位 gear 在 truck_i，位于 offset 500 区 + common_i(4) = 504 ——
        d.gear = struct.unpack_from("i", mm, 504)[0]

        # —— 浮点区（offset 700）——
        # truck_f 起始偏移：700 + 4(scale) + config_f 大小
        # config_f: 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1
        #           + 16(wheelRadius) + 24(ratiosF) + 8(ratiosR) + 1(unitMass)
        #         = 12 + 16 + 24 + 8 + 1 = 61 个 float
        config_f_size = 61
        truck_f_offset = 700 + 4 + config_f_size * 4  # = 700 + 4 + 244 = 948

        # truck_f 字段顺序（头文件 288-326 行）：
        # speed, engineRpm, userSteer, userThrottle, userBrake, userClutch,
        # gameSteer, gameThrottle, gameBrake, gameClutch, cruiseControlSpeed,
        # airPressure, brakeTemperature, fuel, fuelAvgConsumption, fuelRange,
        # adblue, oilPressure, oilTemperature, waterTemperature, batteryVoltage,
        # lightsDashboard, wearEngine, wearTransmission, wearCabin, wearChassis,
        # wearWheels, truckOdometer, routeDistance, routeTime, speedLimit,
        # (然后 16*5 个 wheel 数组)
        idx = 0
        def f():
            nonlocal idx
            v = struct.unpack_from("f", mm, truck_f_offset + idx * 4)[0]
            idx += 1
            return v

        d.speed_ms = f()
        d.engine_rpm = f()
        d.user_steer = f()
        d.user_throttle = f()
        d.user_brake = f()
        f()  # userClutch
        f()  # gameSteer
        f()  # gameThrottle
        f()  # gameBrake
        f()  # gameClutch
        d.cruise_control_speed = f()
        f()  # airPressure
        f()  # brakeTemperature
        d.fuel = f()
        f()  # fuelAvgConsumption
        d.fuel_range = f()
        f()  # adblue
        f()  # oilPressure
        f()  # oilTemperature
        f()  # waterTemperature
        f()  # batteryVoltage
        f()  # lightsDashboard
        d.wear_engine = f()
        d.wear_transmission = f()
        d.wear_cabin = f()
        d.wear_chassis = f()
        f()  # wearWheels
        d.truck_odometer = f()
        d.route_distance = f()
        f()  # routeTime
        d.speed_limit = f()

        d.speed_kmh = d.speed_ms * 3.6

        # —— 燃油容量 / 告警（config_f 里 fuelCapacity 是第 0 个 float）——
        d.fuel_capacity = struct.unpack_from("f", mm, 704)[0]

        # —— 布尔区（offset 1500）——
        # truck_b 起始：1500 + config_b 大小
        # config_b: 16*4(wheel 数组) + isCargoLoaded + specialJob = 66 个 bool
        config_b_size = 66
        truck_b_offset = 1500 + config_b_size  # = 1566
        # truck_b 字段顺序（头文件 353-385 行）：
        # parkBrake, motorBrake, airPressureWarning, airPressureEmergency,
        # fuelWarning, adblueWarning, oilPressureWarning, waterTemperatureWarning,
        # batteryVoltageWarning, electricEnabled, engineEnabled, wipers,
        # blinkerLeftActive, blinkerRightActive, blinkerLeftOn, blinkerRightOn,
        # lightsParking, lightsBeamLow, lightsBeamHigh, lightsBeacon, lightsBrake,
        # lightsReverse, lightsHazard, cruiseControl, (wheelOnGround[16]),
        # shifterToggle[2], differentialLock, liftAxle, liftAxleIndicator,
        # trailerLiftAxle, trailerLiftAxleIndicator
        b = lambda off: struct.unpack_from("?", mm, truck_b_offset + off)[0]
        d.fuel_warning = b(4)
        d.wipers = b(11)
        d.blinker_left_on = b(14)
        d.blinker_right_on = b(15)
        d.lights_beam_low = b(17)
        d.lights_beam_high = b(18)

        # —— 加速度（第六区 truck_fv，offset 1640 起始）——
        # config_fv: cabinPosition(3) + headPosition(3) + hookPosition(3)
        #            + wheelPosition[16]*3 = 3+3+3+48 = 57 个 float
        config_fv_size = 57
        truck_fv_offset = 1640 + config_fv_size * 4  # = 1640 + 228 = 1868
        d.accel_x = struct.unpack_from("f", mm, truck_fv_offset + 6 * 4)[0]   # accelerationX
        d.accel_y = struct.unpack_from("f", mm, truck_fv_offset + 7 * 4)[0]   # accelerationY
        d.accel_z = struct.unpack_from("f", mm, truck_fv_offset + 8 * 4)[0]   # accelerationZ

        # —— 坐标（第八区 truck_dp，offset 2200）——
        d.coordinate_x = struct.unpack_from("d", mm, 2200)[0]
        d.coordinate_y = struct.unpack_from("d", mm, 2208)[0]
        d.coordinate_z = struct.unpack_from("d", mm, 2216)[0]

        # —— 字符串区（第九区，offset 2300）——
        s = 2300
        # config_s 顺序：truckBrandId, truckBrand, truckId, truckName, cargoId,
        # cargo, cityDstId, cityDst, compDstId, compDst, citySrcId, citySrc,
        # compSrcId, compSrc, shifterType[16], truckLicensePlate,
        # truckLicensePlateCountryId, truckLicensePlateCountry, jobMarket[32]
        d.truck_brand = _read_string(mm, s + 1 * _STRING_SIZE)
        d.truck_name = _read_string(mm, s + 3 * _STRING_SIZE)
        d.cargo = _read_string(mm, s + 5 * _STRING_SIZE)
        d.city_dst = _read_string(mm, s + 7 * _STRING_SIZE)
        d.comp_dst = _read_string(mm, s + 9 * _STRING_SIZE)
        d.city_src = _read_string(mm, s + 11 * _STRING_SIZE)
        d.comp_src = _read_string(mm, s + 13 * _STRING_SIZE)

        # —— 任务收入（第十区 config_ull.jobIncome，offset 4000）——
        d.job_income = struct.unpack_from("Q", mm, 4000)[0]

        # —— 任务标记（special_b，offset 4300）——
        # 顺序：onJob, jobFinished, jobCancelled, jobDelivered, fined,
        # tollgate, ferry, train, refuel, refuelPayed
        sb = lambda off: struct.unpack_from("?", mm, 4300 + off)[0]
        d.on_job = sb(0)
        d.job_finished = sb(1)
        d.job_cancelled = sb(2)
        d.job_delivered = sb(3)
        d.fined = sb(4)
        d.tollgate = sb(5)
        d.ferry = sb(6)
        d.train = sb(7)
        d.refuel = sb(8)

        # —— 罚款信息（gameplay_s，位于字符串区之后）——
        # config_s 大小 = 18*64 + 16(shifterType) + 32(jobMarket)
        #   truckBrandId..compSrc = 14 个 64 字节字符串? 按头文件数：
        #   实际 config_s 有 17 个 stringsize 字段 + shifterType[16] + jobMarket[32]
        # 这里只读 fineOffence（gameplay_s 第一个字段）
        # config_s 字段数：truckBrandId,truckBrand,truckId,truckName,cargoId,cargo,
        #   cityDstId,cityDst,compDstId,compDst,citySrcId,citySrc,compSrcId,compSrc
        #   = 14 个 + shifterType(16) + truckLicensePlate + truckLicensePlateCountryId
        #   + truckLicensePlateCountry + jobMarket(32)
        #   = 17 个 stringsize + 16 + 32 = 17*64 + 48 = 1136
        config_s_size = 17 * 64 + 16 + 32
        fine_offence_offset = 2300 + config_s_size
        d.fine_offence = _read_string(mm, fine_offence_offset, 32)

        # —— 罚款金额（gameplay_ll.fineAmount，第十一区 offset 4200）——
        # 顺序：jobCancelledPenalty, jobDeliveredRevenue, fineAmount, ...
        d.fine_amount = struct.unpack_from("q", mm, 4200 + 16)[0]

        # —— 计划里程（config_ui.plannedDistanceKm，第二区）——
        # config_ui 起始 = 40 + scs_values(6*4) + common_ui(4) = 40 + 24 + 4 = 68
        # config_ui 字段：gears, gears_reverse, retarderStepCount, truckWheelCount,
        #   selectorCount, time_abs_delivery, maxTrailerCount, unitCount, plannedDistanceKm
        d.planned_distance_km = struct.unpack_from("I", mm, 68 + 8 * 4)[0]

        return d

    def close(self):
        try:
            self._mm.close()
        except Exception:
            pass


if __name__ == "__main__":
    import time
    t = Ets2Telemetry()
    print("正在读取 ETS2 遥测（按 Ctrl+C 退出）...")
    try:
        while True:
            d = t.read()
            if d.sdk_active:
                print(
                    f"速度 {d.speed_kmh:5.1f}km/h | 档位 {d.gear:>2} | "
                    f"油量 {d.fuel:5.1f}/{d.fuel_capacity:5.1f}L | 转速 {d.engine_rpm:6.0f} | "
                    f"任务 {d.city_src or '-'}→{d.city_dst or '-'} | 货物 {d.cargo or '-'}"
                )
            else:
                print("等待 SDK 激活（游戏未运行或插件未加载）...")
            time.sleep(1)
    except KeyboardInterrupt:
        t.close()
        print("\n已退出。")
