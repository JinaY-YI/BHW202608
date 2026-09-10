"""
波形生成函数（独立模块，无外部依赖）

包含：
- generate_sine_wave: 正弦波
- generate_constant: 恒定值
- generate_square_wave: 方波
- generate_noise_wave: 平滑噪声波（随机游走 + 均值回归）
- generate_sine_with_noise_wave: 正弦波 + 随机噪声
"""
import math
import random


def generate_sine_wave(peak, valley, period, duration=10000):
    """
    生成正弦波

    参数:
        peak: 峰值
        valley: 谷值
        period: 周期（秒）
        duration: 数据点数量

    返回:
        list: 正弦波数据
    """
    amplitude = (peak - valley) / 2
    offset = (peak + valley) / 2
    return [round(offset + amplitude * math.sin(2 * math.pi * t / period), 2) for t in range(duration)]


def generate_constant(value, duration=10000):
    """
    生成恒定值

    参数:
        value: 恒定值
        duration: 数据点数量

    返回:
        list: 恒定值列表
    """
    return [round(value, 2)] * duration


def generate_square_wave(high_val, low_val, period, duty_cycle, duration=10000):
    """
    生成方波

    参数:
        high_val: 高电平值
        low_val: 低电平值
        period: 周期（秒）
        duty_cycle: 占空比（%）
        duration: 数据点数量

    返回:
        list: 方波数据
    """
    result = []
    high_duration = int(period * duty_cycle / 100)
    low_duration = period - high_duration
    for t in range(duration):
        cycle_pos = t % period
        if cycle_pos < high_duration:
            result.append(round(high_val, 2))
        else:
            result.append(round(low_val, 2))
    return result


def generate_noise_wave(mean_val, amplitude, step_size=0.5, seed=None, duration=10000):
    """
    生成平滑噪声波（随机游走 + 均值回归）

    原理:
        1. 从 mean_val 开始
        2. 每一步在前一步基础上加上随机增量 [-step_size, step_size]
        3. 向均值方向回归 2%
        4. 裁剪到 [mean_val - amplitude, mean_val + amplitude]

    参数:
        mean_val: 均值
        amplitude: 振幅（±）
        step_size: 每步最大变化量，值越小曲线越平滑
        seed: 随机种子（用于可复现）
        duration: 数据点数量

    返回:
        list: 噪声波数据
    """
    if seed is not None:
        random.seed(seed)
    result = []
    current = mean_val
    regress_strength = 0.02  # 每步向均值回归 2%
    for _ in range(duration):
        step = random.uniform(-step_size, step_size)
        current += step
        current += (mean_val - current) * regress_strength
        current = max(mean_val - amplitude, min(mean_val + amplitude, current))
        result.append(round(current, 2))
    return result


def generate_sine_with_noise_wave(peak, valley, period, noise_amplitude, seed=None, duration=10000):
    """
    生成正弦波 + 随机噪声（正弦趋势 + 白噪声扰动）

    公式: value(t) = 正弦基础值(t) + uniform(-noise_amplitude, +noise_amplitude)

    参数:
        peak: 正弦峰值
        valley: 正弦谷值
        period: 正弦周期（秒）
        noise_amplitude: 噪声振幅（±），0 表示无噪声
        seed: 随机种子（用于可复现）
        duration: 数据点数量

    返回:
        list: 正弦+噪声数据
    """
    if seed is not None:
        random.seed(seed)
    amplitude = (peak - valley) / 2
    offset = (peak + valley) / 2
    result = []
    for t in range(duration):
        base = offset + amplitude * math.sin(2 * math.pi * t / period)
        noise = random.uniform(-noise_amplitude, noise_amplitude) if noise_amplitude > 0 else 0.0
        result.append(round(base + noise, 2))
    return result