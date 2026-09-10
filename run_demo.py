"""独立演示脚本：内置仿真桩 + 快速场景，控制台输出实时数据表格与调度历史摘要。

无需学生 A/C 参与，运行约 40 秒，依次演示 无风/适宜风/高风高负荷/低负荷/中等风
五种工况下的闭环调度（风电优先、柴发补偿、保留备用）。

用法：
    python run_demo.py
"""
import os
import sys
import time

from ems import mock_grid
from ems.database import Database
from ems.operator_core import OperatorCore
from ems.operator_io import OperatorIO

_DEMO_DB = "ems_demo.db"

# 每个工况持续 8 秒，快速遍历全部典型场景
_PHASES = [(0.0, 50.0), (12.0, 50.0), (20.0, 100.0), (15.0, 20.0), (8.0, 60.0)]
_PHASE_NAMES = ["无风", "适宜风(额定)", "高风·高负荷", "低负荷·充足风", "中等风速"]


def demo_scenario(t: float):
    return _PHASES[int(t) // 8 % len(_PHASES)]


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:
            pass

    # 每次演示都从干净数据库开始，避免上次运行的陈旧数据
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(_DEMO_DB + suffix)
        except OSError:
            pass

    db = Database(_DEMO_DB)
    db.set_sched_params(collect_period=1, decision_period=2, ctrl_mode=1)

    grid = mock_grid.MockGrid(port=0, scenario=demo_scenario).start()
    io = OperatorIO(db, host=grid.host, port=grid.port)
    core = OperatorCore(db)
    io.start()
    core.start()

    header = (f"{'t':>4s} | {'风速':>6s} | {'负荷':>6s} | {'风机出力':>8s} | "
              f"{'柴发出力':>8s} | {'风机设定':>8s} | {'柴发设定':>8s} | {'可用风':>6s} | {'备用':>6s}")
    print("=" * 100)
    print("EMS 主站独立闭环演示（内置仿真桩）")
    print("=" * 100)
    print(header)
    print("-" * 100)

    try:
        t0 = time.time()
        while time.time() - t0 < 40:
            time.sleep(2)
            yc = db.get_scada_yc()
            dev = db.get_device_realtime()
            params = db.get_device_params()
            reserve = (params.get("diesel_max") or 0) - (dev.get("diesel_power_set") or 0)
            print(
                f"{int(yc.get('sim_time') or 0):>4d} | "
                f"{(yc.get('wind_speed') or 0):>6.1f} | "
                f"{(yc.get('load_power') or 0):>6.1f} | "
                f"{(yc.get('wtg_power') or 0):>8.1f} | "
                f"{(yc.get('diesel_power') or 0):>8.1f} | "
                f"{(dev.get('wtg_power_set') or 0):>8.1f} | "
                f"{(dev.get('diesel_power_set') or 0):>8.1f} | "
                f"{(dev.get('available_wind') or 0):>6.1f} | {reserve:>6.1f}"
            )
    except KeyboardInterrupt:
        pass
    finally:
        io.stop()
        core.stop()
        grid.stop()
        # 等待线程完全退出，避免其访问已关闭的数据库连接
        io.join(timeout=3)
        core.join(timeout=3)

    # 调度历史摘要（按工况聚合，展示收敛后的设定值）
    print("-" * 100)
    print("调度历史摘要（每工况最近一次决策）：")
    print(f"{'工况':<16s} | {'风速':>6s} | {'负荷':>6s} | {'风机设定':>8s} | "
          f"{'柴发设定':>8s} | {'新能源占比':>8s} | {'缺额':>6s}")
    seen = set()
    for d in db.get_dispatch_history()[::-1]:
        phase = _PHASE_NAMES[int((d["sim_time"] or 0)) // 8 % len(_PHASES)]
        if phase in seen:
            continue
        seen.add(phase)
        rs = d.get("renewable_share") or 0
        print(f"{phase:<16s} | {d['wind_speed']:>6.1f} | {d['load_power']:>6.1f} | "
              f"{d['wtg_power_set']:>8.1f} | {d['diesel_power_set']:>8.1f} | "
              f"{rs*100:>7.1f}% | {d['shortfall']:>6.1f}")
    db.close()
    print("=" * 100)
    print("演示结束。运行 `python run_ems.py` 打开图形界面查看曲线/历史/日志。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
