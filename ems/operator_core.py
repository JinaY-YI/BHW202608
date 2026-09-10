"""EMS 调度策略进程 operator_core。

不直接与电网模拟器通信，计算所需数据来自本地 ems.db，结果也写回 ems.db，
由 operator_io 负责发送调度指令。周期完成：
1. 调度参数获取（1s 周期：采集周期/决策周期/开闭环模式）
2. 运行断面获取（采集周期到达时：SCADA → 环境表/设备表）
3. 调度决策计算（决策周期到达时：风电优先、柴发补偿）
4. 调度指令生成（闭环模式：写遥调表，仅在设定值变化时写）
5. 调度历史与日志
"""
from __future__ import annotations

import threading
import time

from . import config, strategy
from .database import Database

_EPS = 0.01  # 设定值变化判定阈值 (kW)


class OperatorCore(threading.Thread):
    """调度策略进程（线程实现，独立 DB 连接）。"""

    def __init__(self, db: Database):
        super().__init__(daemon=True, name="operator_core")
        self.db = db
        self._stop = threading.Event()
        self._last_collect = 0.0
        self._last_decision = 0.0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self.db.add_log("INFO", "operator_core", "调度策略进程启动")
        while not self._stop.is_set():
            try:
                self._step()
            except Exception as exc:  # 调度异常不应杀死进程
                self.db.add_log("ERROR", "operator_core", f"调度异常: {exc}")
            self._stop.wait(1.0)
        self.db.add_log("INFO", "operator_core", "调度策略进程退出")

    # ------------------------------------------------------------------
    def _step(self) -> None:
        params = self.db.get_sched_params()
        collect_period = float(params.get("data_collect_period", config.DEFAULT_COLLECT_PERIOD))
        decision_period = float(params.get("decision_period", config.DEFAULT_DECISION_PERIOD))
        ctrl_mode = int(params.get("ctrl_mode_ems", config.DEFAULT_CTRL_MODE))
        now = time.time()

        if now - self._last_collect >= collect_period:
            self._last_collect = now
            self._collect_snapshot()

        if now - self._last_decision >= decision_period:
            self._last_decision = now
            self._dispatch(ctrl_mode)

    # ------------------------------------------------------------------
    def _collect_snapshot(self) -> None:
        """运行断面获取：SCADA 实时数据 → 环境表/设备表 + 历史表。"""
        yc = self.db.get_scada_yc()
        yx = self.db.get_scada_yx()
        sim_time = yc.get("sim_time")
        wind_speed = yc.get("wind_speed")
        load_power = yc.get("load_power")
        wtg_power = yc.get("wtg_power")
        diesel_power = yc.get("diesel_power")

        self.db.set_env_realtime(
            sim_time=sim_time, wind_speed=wind_speed, load_power=load_power
        )
        self.db.set_device_realtime(
            sim_time=sim_time,
            wtg_status=yx.get("wtg_status"),
            diesel_status=yx.get("diesel_status"),
            wtg_power=wtg_power,
            diesel_power=diesel_power,
        )
        self.db.add_history(
            sim_time=sim_time, wind_speed=wind_speed, load_power=load_power,
            wtg_power=wtg_power, diesel_power=diesel_power,
            pitch_angle_set=self.db.get_device_realtime().get("pitch_angle_set"),
        )

    # ------------------------------------------------------------------
    def _dispatch(self, ctrl_mode: int) -> None:
        """调度决策计算与指令生成。"""
        yc = self.db.get_scada_yc()
        params = self.db.get_device_params()
        prev = self.db.get_device_realtime()

        wind_speed = yc.get("wind_speed") or 0.0
        load_power = yc.get("load_power") or 0.0
        wtg_power = yc.get("wtg_power") or 0.0
        diesel_power = yc.get("diesel_power") or 0.0
        sim_time = yc.get("sim_time")

        decision = strategy.scheduling_decision(
            wind_speed=wind_speed,
            load_power=load_power,
            diesel_max=params.get("diesel_max", config.DEFAULT_DEVICE_PARAMS["diesel_max"]),
            diesel_min=params.get("diesel_min", config.DEFAULT_DEVICE_PARAMS["diesel_min"]),
            rated_power=params.get("rated_power", config.DEFAULT_DEVICE_PARAMS["rated_power"]),
            cut_in_wind=params.get("cut_in_wind", config.DEFAULT_DEVICE_PARAMS["cut_in_wind"]),
            cut_out_wind=params.get("cut_out_wind", config.DEFAULT_DEVICE_PARAMS["cut_out_wind"]),
            rated_wind=params.get("rated_wind", config.DEFAULT_DEVICE_PARAMS["rated_wind"]),
        )

        # 更新设备实时表中的设定值（供 HMI 展示）
        self.db.set_device_realtime(
            wtg_power_set=decision.wtg_power_set,
            diesel_power_set=decision.diesel_power_set,
            available_wind=decision.available_wind,
        )

        # 调度历史
        self.db.add_dispatch_history(
            sim_time=sim_time, wind_speed=wind_speed, load_power=load_power,
            wtg_power=wtg_power, diesel_power=diesel_power,
            available_wind=decision.available_wind,
            wtg_power_set=decision.wtg_power_set,
            diesel_power_set=decision.diesel_power_set,
            diesel_reserve=decision.diesel_reserve,
            renewable_share=decision.renewable_share,
            balance_error=decision.balance_error,
            shortfall=decision.shortfall,
        )

        # 调度指令生成（仅闭环，且设定值发生变化才写遥调表）
        if ctrl_mode == 1:
            prev_wtg = prev.get("wtg_power_set")
            prev_diesel = prev.get("diesel_power_set")
            if prev_wtg is None or abs(prev_wtg - decision.wtg_power_set) > _EPS:
                self.db.add_command("scada_yt", config.TARGET_WTG_POWER, decision.wtg_power_set)
            if prev_diesel is None or abs(prev_diesel - decision.diesel_power_set) > _EPS:
                self.db.add_command("scada_yt", config.TARGET_DIESEL_POWER, decision.diesel_power_set)
            self.db.add_log(
                "INFO", "operator_core",
                f"闭环调度: wtg_set={decision.wtg_power_set:.2f}kW, "
                f"diesel_set={decision.diesel_power_set:.2f}kW, "
                f"avail_wind={decision.available_wind:.2f}kW, "
                f"reserve={decision.diesel_reserve:.2f}kW"
                + (f", shortfall={decision.shortfall:.2f}kW" if decision.shortfall > 0 else ""),
            )
        else:
            self.db.add_log(
                "INFO", "operator_core",
                f"开环计算: wtg_set={decision.wtg_power_set:.2f}kW, "
                f"diesel_set={decision.diesel_power_set:.2f}kW（不下发）",
            )
