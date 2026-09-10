"""EMS 主站编排器：数据库 + 通信进程 + 调度进程 +（默认）人机界面。

启动方式：
    python run_ems.py                    # 打开图形界面（默认内置仿真桩自启）
    python run_ems.py --headless --mock  # 无界面 + 内置仿真桩（控制台打印实时表格）
    python run_ems.py --headless --host 192.168.1.10   # 无界面连接真实电网模拟器
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import List, Optional

from . import config
from .database import Database
from .mock_grid import MockGrid
from .operator_core import OperatorCore
from .operator_io import OperatorIO


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_ems",
        description="南极考察站微电网智能调控系统 —— EMS 主站（学生 B）",
    )
    p.add_argument("--host", default=config.GRID_HOST,
                   help="电网模拟器地址（联调学生 A 时填其 IP，默认 127.0.0.1）")
    p.add_argument("--port", type=int, default=config.GRID_PORT,
                   help="电网模拟器端口（默认 8888）")
    p.add_argument("--db", default=config.DEFAULT_DB_PATH,
                   help="ems.db 路径")
    p.add_argument("--headless", action="store_true",
                   help="无界面运行（用于服务器/测试，控制台打印实时数据）")
    p.add_argument("--mock", action="store_true",
                   help="无界面模式下启用内置仿真桩（独立演示）")
    p.add_argument("--mock-port", type=int, default=0,
                   help="仿真桩监听端口（0 自动分配）")
    p.add_argument("--trace", action="store_true",
                   help="把每条收发报文全文写入运行日志，便于联调排障")
    return p.parse_args(argv)


def run(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    # 控制台输出统一 UTF-8 + 行缓冲，避免 Windows 下中文乱码与管道输出缓冲
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:
            pass

    db = Database(args.db)

    if not args.headless:
        # 图形界面（默认）：界面内自带连接控制（内置仿真桩 / 电网模拟器）
        from .hmi import EMSApp
        app = EMSApp(db)
        app.mainloop()
        return 0

    # 无界面模式
    grid: Optional[MockGrid] = None
    io = OperatorIO(db, host=args.host, port=args.port, trace=args.trace)
    if args.mock:
        grid = MockGrid(host=args.host, port=args.mock_port).start()
        io.host = grid.host
        io.port = grid.port
        print(f"[EMS] 内置电网模拟器仿真桩已启动：{grid.host}:{grid.port}")
    core = OperatorCore(db)

    io.start()
    core.start()
    print(f"[EMS] 通信/调度进程已启动，目标电网模拟器 {io.host}:{io.port}")
    print(_TABLE_HEADER)

    try:
        _headless_loop(db)
    except KeyboardInterrupt:
        pass
    finally:
        io.stop()
        core.stop()
        io.join(timeout=3)
        core.join(timeout=3)
        if grid is not None:
            grid.stop()
        print("[EMS] 已退出")
    return 0


_TABLE_HEADER = (
    f"{'t':>4s} | {'conn':>4s} | {'风速':>6s} | {'负荷':>6s} | "
    f"{'风机':>6s} | {'柴发':>6s} | {'风机设定':>8s} | {'柴发设定':>8s} | "
    f"{'可用风':>6s} | {'备用':>6s}"
)
_ROW = (
    "{t:>4d} | {conn:>4d} | {ws:>6.1f} | {lp:>6.1f} | {wtg:>6.1f} | {diesel:>6.1f} | "
    "{wset:>8.1f} | {dset:>8.1f} | {avail:>6.1f} | {reserve:>6.1f}"
)


def _headless_loop(db: Database) -> None:
    print("[EMS] 无界面模式运行中，Ctrl+C 退出")
    while True:
        cs = db.get_comm_status()
        yc = db.get_scada_yc()
        dev = db.get_device_realtime()
        params = db.get_device_params()
        t = int(yc.get("sim_time") or 0)
        reserve = (params.get("diesel_max") or 0) - (dev.get("diesel_power_set") or 0)
        print(_ROW.format(
            t=t, conn=int(cs.get("connected") or 0),
            ws=yc.get("wind_speed") or 0, lp=yc.get("load_power") or 0,
            wtg=yc.get("wtg_power") or 0, diesel=yc.get("diesel_power") or 0,
            wset=dev.get("wtg_power_set") or 0, dset=dev.get("diesel_power_set") or 0,
            avail=dev.get("available_wind") or 0, reserve=reserve,
        ))
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(run())
