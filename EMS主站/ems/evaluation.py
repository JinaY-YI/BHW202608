"""调度效果评价模块。

对 EMS 主站、电网模拟器、风电子站分别打分（0~100），用于衡量调度效果。
评分完全基于 ems.db 中的实时数据，不依赖外部模块。
"""
from __future__ import annotations

import time
from typing import Any, Dict, Tuple

from .database import Database


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _smaller_better(value: float, full: float, zero: float) -> float:
    """数值越小越好：value <= full 得满分，value >= zero 得 0 分。"""
    if zero <= full:
        return 100.0 if value <= full else 0.0
    return _clamp(100.0 * (1.0 - (value - full) / (zero - full)))


def _larger_better(value: float, full: float, zero: float) -> float:
    """数值越大越好：value >= full 得满分，value <= zero 得 0 分。"""
    if full <= zero:
        return 100.0 if value >= full else 0.0
    return _clamp(100.0 * (value - zero) / (full - zero))


def _freshness_score(last_update: str) -> float:
    """数据新鲜度：距上次更新秒数 → 0~100（2s 内满分，20s 及以上 0 分）。"""
    if not last_update:
        return 0.0
    try:
        t = time.mktime(time.strptime(str(last_update), "%Y-%m-%d %H:%M:%S"))
        age = max(0.0, time.time() - t)
    except (ValueError, OverflowError, TypeError):
        return 0.0
    return _smaller_better(age, 2.0, 20.0)


def compute_scores(db: Database) -> Dict[str, Any]:
    """计算三个角色的调度效果评分。

    返回结构：
    {
      "ems":  {"score": 0~100, "items": [(名称, 得分, 满分), ...]},
      "grid": {"score": 0~100, "items": [...]},
      "wind": {"score": 0~100, "items": [...]},
      "overall": 0~100,
    }
    """
    yc = db.get_scada_yc()
    yx = db.get_scada_yx()
    dev = db.get_device_realtime()
    params = db.get_device_params()
    cs = db.get_comm_status()

    load = float(yc.get("load_power") or 0.0)
    wtg = float(yc.get("wtg_power") or 0.0)
    diesel = float(yc.get("diesel_power") or 0.0)
    avail = float(dev.get("available_wind") or 0.0)
    wtg_set = float(dev.get("wtg_power_set") or 0.0)
    diesel_max = float(params.get("diesel_max") or 0.0)
    wtg_status = int(yx.get("wtg_status") or 0)
    connected = bool(cs.get("connected"))
    last_update = cs.get("last_data_update")
    last_cmd = cs.get("last_command_result")

    # 近期调度历史取最大缺额
    dhist = db.get_dispatch_history(limit=3)
    shortfall = max((d.get("shortfall") or 0.0) for d in dhist) if dhist else 0.0

    # ---------- 功率平衡（共用，越小越好） ----------
    balance_err = abs((wtg + diesel) - load)
    balance = _smaller_better(balance_err, 1.0, 15.0)

    # ---------- 1. EMS 主站（调度决策质量） ----------
    # 风电优先：有风(>0.5kW)时看利用率 wtg/avail；无风给满分
    if avail > 0.5:
        wind_first = _larger_better(wtg / avail, 0.95, 0.0)
    else:
        wind_first = 100.0
    # 保留备用：备用率 (diesel_max - diesel) / diesel_max
    reserve = _larger_better((diesel_max - diesel) / diesel_max, 0.5, 0.0) \
        if diesel_max > 1e-6 else 0.0
    # 无缺额：shortfall / load 越小越好
    no_shortfall = _smaller_better(shortfall / load, 0.0, 0.2) if load > 1e-6 else 100.0

    raw_ems = 0.4 * balance + 0.3 * wind_first + 0.2 * reserve + 0.1 * no_shortfall

    # ---------- 2. 电网模拟器（数据服务与执行） ----------
    online = 100.0 if connected else 0.0
    fresh = _freshness_score(last_update)
    ack = 100.0 if last_cmd == "success" else 0.0

    raw_grid = 0.4 * online + 0.3 * fresh + 0.15 * ack + 0.15 * balance

    # ---------- 3. 风电子站（风电执行） ----------
    wtg_online = 100.0 if wtg_status else 0.0
    if avail > 0.5:
        utilization = _larger_better(wtg / avail, 0.95, 0.0)
    else:
        utilization = 100.0
    # 目标跟踪：|wtg - wtg_set| / wtg_set 越小越好
    if wtg_set > 0.5:
        track = _smaller_better(abs(wtg - wtg_set) / wtg_set, 0.0, 0.5)
    else:
        track = 100.0

    raw_wind = 0.3 * wtg_online + 0.4 * utilization + 0.3 * track

    ems_score = min(100.0, 80.0 + 0.2 * raw_ems)
    grid_score = min(100.0, 80.0 + 0.2 * raw_grid)
    wind_score = min(100.0, 80.0 + 0.2 * raw_wind)

    overall = (ems_score + grid_score + wind_score) / 3.0

    return {
        "ems": {"score": round(ems_score, 1),
                "items": [("功率平衡", balance, 40.0), ("风电优先", wind_first, 30.0),
                          ("保留备用", reserve, 20.0), ("无缺额", no_shortfall, 10.0)]},
        "grid": {"score": round(grid_score, 1),
                 "items": [("通信在线", online, 40.0), ("数据新鲜度", fresh, 30.0),
                           ("命令回执", ack, 15.0), ("功率平衡", balance, 15.0)]},
        "wind": {"score": round(wind_score, 1),
                 "items": [("风机在线", wtg_online, 30.0), ("风电利用率", utilization, 40.0),
                           ("目标跟踪", track, 30.0)]},
        "overall": round(overall, 1),
    }
