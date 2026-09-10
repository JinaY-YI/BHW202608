"""EMS 调度决策算法与调度结果评价。

遵循协议"风电优先、柴发补偿、保留备用"原则：
1. 由实时风速与风机参数估计当前可用风功率 available_wind；
2. 风电优先：目标风机出力尽量取可用风功率，但不超过负荷；
3. 柴发补偿：负荷与风机出力之差由柴发补足，并限幅于 [diesel_min, diesel_max]；
4. 保留备用：当柴发补偿量低于最小出力时，柴发维持在 diesel_min 作为旋转备用，
   同时削减风机出力以维持功率平衡；当补偿量超过最大出力时，柴发顶格运行，
   记录缺额 shortfall（新能源不足场景）。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict


def clamp(value: float, lo: float, hi: float) -> float:
    """将 value 限制在 [lo, hi] 内。"""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def estimate_available_power(wind_speed: float, cut_in_wind: float,
                             cut_out_wind: float, rated_wind: float,
                             rated_power: float) -> float:
    """根据风速与风机参数估计理论最大可发功率（标准功率曲线，三次插值）。

    - 风速 < 切入风速 或 > 切出风速 → 0
    - 切入风速 ≤ 风速 < 额定风速 → 额定功率 * ((v-v_in)/(v_rated-v_in))^3
    - 风速 ≥ 额定风速 → 额定功率
    """
    v = float(wind_speed)
    v_in = float(cut_in_wind)
    v_out = float(cut_out_wind)
    v_rated = float(rated_wind)
    p_rated = float(rated_power)

    if v < v_in or v > v_out:
        return 0.0
    if v >= v_rated:
        return p_rated
    if v_rated <= v_in:
        # 参数异常时退化为线性，避免除零
        return p_rated * clamp((v - v_in) / 1.0, 0.0, 1.0)
    ratio = (v - v_in) / (v_rated - v_in)
    return p_rated * (ratio ** 3)


@dataclass
class Decision:
    """一次调度决策的结果。"""
    wind_speed: float
    load_power: float
    available_wind: float
    wtg_power_set: float
    diesel_power_set: float
    diesel_reserve: float       # 柴发备用容量 = diesel_max - diesel_power_set
    shortfall: float            # 柴发顶格仍缺的功率 (>0 表示供电缺口)
    renewable_share: float      # 新能源占比 = wtg_power_set / load_power
    balance_error: float        # 功率平衡误差 = (wtg+diesel) - load
    curtailed: float            # 因柴油最小出力而被削减的风电

    def as_dict(self) -> Dict[str, float]:
        return asdict(self)


def scheduling_decision(wind_speed: float, load_power: float,
                        diesel_max: float, diesel_min: float,
                        rated_power: float, cut_in_wind: float,
                        cut_out_wind: float, rated_wind: float) -> Decision:
    """执行一次"风电优先、柴发补偿、保留备用"调度决策。

    返回 Decision，其中 wtg_power_set/diesel_power_set 已保证在能力范围内：
    - 0 ≤ wtg_power_set ≤ rated_power
    - diesel_min ≤ diesel_power_set ≤ diesel_max
    """
    wind_speed = float(wind_speed)
    load_power = max(0.0, float(load_power))
    diesel_max = float(diesel_max)
    diesel_min = max(0.0, float(diesel_min))
    rated_power = float(rated_power)

    available = estimate_available_power(
        wind_speed, cut_in_wind, cut_out_wind, rated_wind, rated_power
    )

    # 1) 风电优先：先让风机承担尽量多的负荷（不超过可用风功率与额定功率）
    wtg_set = min(available, load_power, rated_power)

    # 2) 柴发补偿负荷缺口
    diesel_req = load_power - wtg_set
    shortfall = 0.0
    curtailed = 0.0

    if diesel_req < diesel_min:
        # 3) 保留备用：柴发以最小出力作为旋转备用，削减风机以维持平衡
        diesel_set = diesel_min
        wtg_set = max(0.0, load_power - diesel_min)
        curtailed = available - wtg_set if available > wtg_set else 0.0
    elif diesel_req > diesel_max:
        # 柴发顶格仍不足 → 供电缺口
        diesel_set = diesel_max
        shortfall = diesel_req - diesel_max
    else:
        diesel_set = diesel_req

    # 最终限幅，保证目标落在能力范围（协议第七部分：运行合理性）
    wtg_set = clamp(wtg_set, 0.0, rated_power)
    diesel_set = clamp(diesel_set, diesel_min, diesel_max)

    reserve = diesel_max - diesel_set
    renewable_share = (wtg_set / load_power) if load_power > 1e-9 else 0.0
    balance_error = (wtg_set + diesel_set) - load_power

    return Decision(
        wind_speed=wind_speed,
        load_power=load_power,
        available_wind=available,
        wtg_power_set=round(wtg_set, 4),
        diesel_power_set=round(diesel_set, 4),
        diesel_reserve=round(reserve, 4),
        shortfall=round(shortfall, 4),
        renewable_share=round(min(max(renewable_share, 0.0), 1.0), 4),
        balance_error=round(balance_error, 4),
        curtailed=round(max(curtailed, 0.0), 4),
    )


def evaluate_dispatch(load_power: float, wtg_power: float, diesel_power: float,
                      available_wind: float, diesel_max: float) -> Dict[str, float]:
    """调度结果评价指标（供 HMI 展示）。

    - balance_error : 实际功率平衡误差（wtg+diesel-load）
    - renewable_share: 新能源实际占比
    - wind_utilization: 风机利用率（wtg_power/available_wind）
    - diesel_reserve_ratio: 柴发备用率
    """
    load = max(0.0, float(load_power))
    wtg = float(wtg_power)
    diesel = float(diesel_power)
    avail = float(available_wind)
    dmax = float(diesel_max)

    balance_error = (wtg + diesel) - load
    renewable_share = (wtg / load) if load > 1e-9 else 0.0
    wind_utilization = (wtg / avail) if avail > 1e-9 else (1.0 if wtg <= 1e-9 else 0.0)
    diesel_reserve_ratio = ((dmax - diesel) / dmax) if dmax > 1e-9 else 0.0

    return {
        "balance_error": round(balance_error, 4),
        "renewable_share": round(min(max(renewable_share, 0.0), 1.0), 4),
        "wind_utilization": round(min(max(wind_utilization, 0.0), 1.0), 4),
        "diesel_reserve_ratio": round(min(max(diesel_reserve_ratio, 0.0), 1.0), 4),
    }
