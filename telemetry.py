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

import ctypes
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


def _read_string(buf, offset: int, size: int = _STRING_SIZE) -> str:
    return _decode(bytes(buf[offset:offset + size]))


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
    """读 Windows 命名共享内存的遥测读取器。

    用 ctypes 调 Windows API（OpenFileMappingW + MapViewOfFile）打开游戏进程
    已经创建的命名共享内存 ``Local\\SCSTelemetry``。

    注意：不能用 Python 标准库 mmap 的 tagname 参数——那会在本进程里新建一块
    独立的空映射，读到的全是 0，连不上游戏写的那块共享内存。
    """

    def __init__(self, name: str = MMF_NAME, size: int = MMF_SIZE):
        self._name = name
        self._size = size
        self._map_handle = None
        self._view = None
        self._buf = None
        self._last_error = ""
        # 不强制连接成功：游戏可能还没启动，由调用方决定何时 connect()
        self.connect()

    # ---------- 连接 / 重连 ----------
    def connect(self) -> bool:
        """尝试打开共享内存。成功返回 True；失败（游戏未开等）返回 False，不抛异常。

        可在游戏启动后再调用本方法重连。
        """
        if self._buf is not None:
            return True  # 已连接
        try:
            self._open()
            return True
        except Exception as e:
            # 静默返回 False，由调用方决定是否打印/重试
            self._last_error = str(e)
            return False

    @property
    def is_connected(self) -> bool:
        return self._buf is not None

    # ---------- Windows API 打开共享内存 ----------
    def _open(self):
        import sys
        if sys.platform != "win32":
            raise RuntimeError("遥测读取仅支持 Windows")

        k32 = ctypes.windll.kernel32

        FILE_MAP_READ = 0x0004

        k32.OpenFileMappingW.restype = ctypes.c_void_p
        k32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_bool,
                                         ctypes.c_wchar_p]
        k32.MapViewOfFile.restype = ctypes.c_void_p
        k32.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                      ctypes.c_uint32, ctypes.c_uint32,
                                      ctypes.c_size_t]

        name_w = ctypes.c_wchar_p(self._name)
        handle = k32.OpenFileMappingW(FILE_MAP_READ, False, name_w)
        if not handle:
            err = ctypes.get_last_error()
            raise RuntimeError(
                f"打不开共享内存 {self._name}（错误码 {err}）。"
                "请确认：1) 游戏正在运行；2) scs-telemetry.dll 已放入 "
                "bin/win_x64/plugins/；3) 已进入驾驶界面。"
            )

        view = k32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, self._size)
        if not view:
            err = ctypes.get_last_error()
            k32.CloseHandle(handle)
            raise RuntimeError(f"映射共享内存失败（错误码 {err}）")

        self._map_handle = handle
        self._view = view
        # 把映射视图当作一个可随机读取的字节缓冲区（C 数组）
        self._buf = (ctypes.c_char * self._size).from_address(view)

    def _read_bytes(self, offset: int, size: int) -> bytes:
        return bytes(self._buf[offset:offset + size])

    def read(self) -> TelemetryData:
        d = TelemetryData()
        if self._buf is None:
            return d
        buf = self._buf

        # —— 第一区（offset 0）——
        d.sdk_active = struct.unpack_from("?", buf, 0)[0]
        d.paused = struct.unpack_from("?", buf, 4)[0]
        d.timestamp = struct.unpack_from("Q", buf, 8)[0]

        # game 标识在第二区 scs_values.game，偏移 = 40 + 4*3 = 52
        d.game = struct.unpack_from("I", buf, 52)[0]

        # —— 整数区：档位 gear 在 truck_i，位于 offset 500 区 + common_i(4) = 504 ——
        d.gear = struct.unpack_from("i", buf, 504)[0]

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
            v = struct.unpack_from("f", buf, truck_f_offset + idx * 4)[0]
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
        d.cruise_control_speed = f() * 3.6  # m/s → km/h
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
        d.speed_limit = f() * 3.6  # SDK 单位是 m/s，换算成 km/h

        d.speed_kmh = d.speed_ms * 3.6

        # —— 燃油容量 / 告警（config_f 里 fuelCapacity 是第 0 个 float）——
        d.fuel_capacity = struct.unpack_from("f", buf, 704)[0]

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
        b = lambda off: struct.unpack_from("?", buf, truck_b_offset + off)[0]
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
        d.accel_x = struct.unpack_from("f", buf, truck_fv_offset + 6 * 4)[0]   # accelerationX
        d.accel_y = struct.unpack_from("f", buf, truck_fv_offset + 7 * 4)[0]   # accelerationY
        d.accel_z = struct.unpack_from("f", buf, truck_fv_offset + 8 * 4)[0]   # accelerationZ

        # —— 坐标（第八区 truck_dp，offset 2200）——
        d.coordinate_x = struct.unpack_from("d", buf, 2200)[0]
        d.coordinate_y = struct.unpack_from("d", buf, 2208)[0]
        d.coordinate_z = struct.unpack_from("d", buf, 2216)[0]

        # —— 字符串区（第九区，offset 2300）——
        s = 2300
        # config_s 顺序：truckBrandId, truckBrand, truckId, truckName, cargoId,
        # cargo, cityDstId, cityDst, compDstId, compDst, citySrcId, citySrc,
        # compSrcId, compSrc, shifterType[16], truckLicensePlate,
        # truckLicensePlateCountryId, truckLicensePlateCountry, jobMarket[32]
        d.truck_brand = _read_string(buf, s + 1 * _STRING_SIZE)
        d.truck_name = _read_string(buf, s + 3 * _STRING_SIZE)
        d.cargo = _read_string(buf, s + 5 * _STRING_SIZE)
        d.city_dst = _read_string(buf, s + 7 * _STRING_SIZE)
        d.comp_dst = _read_string(buf, s + 9 * _STRING_SIZE)
        d.city_src = _read_string(buf, s + 11 * _STRING_SIZE)
        d.comp_src = _read_string(buf, s + 13 * _STRING_SIZE)

        # —— 任务收入（第十区 config_ull.jobIncome，offset 4000）——
        d.job_income = struct.unpack_from("Q", buf, 4000)[0]

        # —— 任务标记（special_b，offset 4300）——
        # 顺序：onJob, jobFinished, jobCancelled, jobDelivered, fined,
        # tollgate, ferry, train, refuel, refuelPayed
        sb = lambda off: struct.unpack_from("?", buf, 4300 + off)[0]
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
        d.fine_offence = _read_string(buf, fine_offence_offset, 32)

        # —— 罚款金额（gameplay_ll.fineAmount，第十一区 offset 4200）——
        # 顺序：jobCancelledPenalty, jobDeliveredRevenue, fineAmount, ...
        d.fine_amount = struct.unpack_from("q", buf, 4200 + 16)[0]

        # —— 计划里程（config_ui.plannedDistanceKm，第二区）——
        # config_ui 起始 = 40 + scs_values(6*4) + common_ui(4) = 40 + 24 + 4 = 68
        # config_ui 字段：gears, gears_reverse, retarderStepCount, truckWheelCount,
        #   selectorCount, time_abs_delivery, maxTrailerCount, unitCount, plannedDistanceKm
        d.planned_distance_km = struct.unpack_from("I", buf, 68 + 8 * 4)[0]

        return d

    def close(self):
        try:
            k32 = ctypes.windll.kernel32
            if self._view:
                k32.UnmapViewOfFile(self._view)
                self._view = None
            if self._map_handle:
                k32.CloseHandle(self._map_handle)
                self._map_handle = None
            self._buf = None
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
