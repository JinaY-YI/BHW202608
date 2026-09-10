"""电网模拟器仿真桩（Mock Grid Simulator）。

用途：在暂时无法与"学生 A 电网模拟器 / 学生 C 风机控制器"联调时，本桩按 V3
协议扮演电网模拟器角色，让 EMS 主站可独立启动并完成"测量-决策-执行-反馈"
闭环测试。本桩同时模拟了电网模拟器的功率平衡计算（数据源唯一：wtg_power /
diesel_power 由"电网模拟器"侧计算）。

支持：
- 场景曲线（风速/负荷随仿真时刻变化）
- 增量/变位遥测遥信（seq + change_log），支持断点补传
- 遥调命令接收执行（更新 wtg_power_set / diesel_power_set）
- hello / heartbeat / param_query 应答
"""
from __future__ import annotations

import json
import math
import socket
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from . import protocol
from .strategy import clamp, estimate_available_power

DEFAULT_DEVICE_PARAMS = {
    "cut_in_wind": 3.0,
    "cut_out_wind": 25.0,
    "rated_wind": 12.0,
    "rated_power": 100.0,
    "diesel_max": 150.0,
    "diesel_min": 10.0,
}


def default_scenario(sim_time: float) -> Tuple[float, float]:
    """默认场景：风速/负荷随时间正弦波动，持续变化、永不静止。

    风速周期 50s、范围约 1~15 m/s（覆盖切入/额定/满发区间）；
    负荷周期 30s、范围约 20~60 kW。
    """
    wind = 8.0 + 7.0 * math.sin(2 * math.pi * sim_time / 50.0)
    load = 40.0 + 20.0 * math.sin(2 * math.pi * sim_time / 30.0)
    return max(0.0, wind), max(0.0, load)


class MockGrid:
    """电网模拟器仿真桩。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 0,
                 scenario: Optional[Callable[[float], Tuple[float, float]]] = None,
                 device_params: Optional[Dict[str, float]] = None,
                 tick_period: float = 1.0):
        self.host = host
        self.port = port
        self.scenario = scenario or default_scenario
        self.params = dict(DEFAULT_DEVICE_PARAMS)
        if device_params:
            self.params.update(device_params)
        self.tick_period = tick_period

        # 仿真状态
        self.sim_time = 0.0
        self.seq = 0                      # 变位序号
        self.cmd_seq = 0                  # 命令回执序号
        self.change_log: Dict[int, Dict[str, List[str]]] = {}
        self.yc: Dict[str, float] = {
            "sim_time": 0.0, "wind_speed": 0.0, "load_power": 0.0,
            "wtg_power": 0.0, "diesel_power": 0.0,
        }
        self.yx: Dict[str, int] = {"wtg_status": 0, "diesel_status": 0}
        self.wtg_power_set = 0.0
        self.diesel_power_set = 0.0

        self._stop = threading.Event()
        self._srv_sock: Optional[socket.socket] = None
        self._threads: List[threading.Thread] = []
        self._clients: List[socket.socket] = []

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> "MockGrid":
        self._srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv_sock.bind((self.host, self.port))
        self._srv_sock.listen(8)
        self.port = self._srv_sock.getsockname()[1]
        self._stop.clear()
        threading.Thread(target=self._accept_loop, daemon=True, name="mock-accept").start()
        threading.Thread(target=self._tick_loop, daemon=True, name="mock-tick").start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._srv_sock:
            try:
                self._srv_sock.close()
            except OSError:
                pass
            self._srv_sock = None
        for c in list(self._clients):
            try:
                c.close()
            except OSError:
                pass
        self._clients.clear()

    @property
    def address(self) -> Tuple[str, int]:
        return (self.host, self.port)

    # ------------------------------------------------------------------
    # 服务端
    # ------------------------------------------------------------------
    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self._srv_sock.accept()
            except OSError:
                return
            self._clients.append(conn)
            t = threading.Thread(target=self._client_loop, args=(conn, addr),
                                 daemon=True, name=f"mock-client-{addr[1]}")
            t.start()
            self._threads.append(t)

    def _client_loop(self, conn: socket.socket, addr) -> None:
        conn.settimeout(0.5)
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = conn.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                self._handle(conn, msg)
        try:
            conn.close()
        except OSError:
            pass
        if conn in self._clients:
            self._clients.remove(conn)

    def _send(self, conn: socket.socket, mtype: str, data: Dict, dst: str) -> None:
        try:
            conn.sendall(protocol.encode_message(mtype, data, dst=dst, src="grid_simulator"))
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 报文处理
    # ------------------------------------------------------------------
    def _handle(self, conn: socket.socket, msg: Dict) -> None:
        mtype = msg.get("type")
        data = msg.get("data") or {}
        dst = msg.get("src", "ems")

        if mtype == protocol.TYPE_HELLO:
            self._send(conn, protocol.TYPE_HELLO_ACK, {}, dst)
        elif mtype == protocol.TYPE_HEARTBEAT:
            self._send(conn, protocol.TYPE_HEARTBEAT, {}, dst)
        elif mtype == protocol.TYPE_YC_YX_REQUEST:
            self._handle_yc_yx_request(conn, data, dst)
        elif mtype == protocol.TYPE_COMMAND:
            self._handle_command(conn, data, dst)
        elif mtype == protocol.TYPE_PARAM_QUERY:
            self._send(conn, protocol.TYPE_PARAM_NOTIFY, dict(self.params), dst)

    def _handle_yc_yx_request(self, conn: socket.socket, data: Dict, dst: str) -> None:
        last_seq = int(data.get("last_seq", 0))
        need_retransmit = bool(data.get("need_retransmit", False))

        if need_retransmit or last_seq <= 0:
            resp = {"seq": self.seq, "yc": dict(self.yc), "yx": dict(self.yx),
                    "retransmit": need_retransmit}
        elif last_seq >= self.seq:
            # 无变位：仅附带当前仿真时刻（时刻每周期变化，但不计入变位序号）
            resp = {"seq": self.seq, "yc": {"sim_time": self.yc["sim_time"]}, "yx": {}}
        else:
            # 只返回当前 seq 的最新变位（增量机制），并始终附带当前仿真时刻
            entry = self.change_log.get(self.seq, {"yc": [], "yx": []})
            yc = {k: self.yc[k] for k in entry.get("yc", []) if k in self.yc}
            yx = {k: self.yx[k] for k in entry.get("yx", []) if k in self.yx}
            yc["sim_time"] = self.yc["sim_time"]
            resp = {"seq": self.seq, "yc": yc, "yx": yx}
        self._send(conn, protocol.TYPE_YC_YX_RESPONSE, resp, dst)

    def _handle_command(self, conn: socket.socket, data: Dict, dst: str) -> None:
        failed = []
        for cmd in data.get("commands", []):
            target = cmd.get("target")
            value = cmd.get("value")
            if target == "wtg_power":
                self.wtg_power_set = float(value)
            elif target == "diesel_power":
                self.diesel_power_set = float(value)
            else:
                failed.append(cmd)
        self.cmd_seq += 1
        self._send(conn, protocol.TYPE_COMMAND_RESPONSE,
                   {"result": "success" if not failed else "partial",
                    "failed_commands": failed, "executed_seq": self.cmd_seq},
                   dst)

    # ------------------------------------------------------------------
    # 仿真计算（数据源唯一：由"电网模拟器"计算 wtg_power / diesel_power）
    # ------------------------------------------------------------------
    def _available_wind(self, ws: float) -> float:
        return estimate_available_power(
            ws, self.params["cut_in_wind"], self.params["cut_out_wind"],
            self.params["rated_wind"], self.params["rated_power"],
        )

    def _compute_wtg_power(self, ws: float) -> float:
        avail = self._available_wind(ws)
        if avail <= 1e-9 or self.wtg_power_set <= 0:
            return 0.0
        return min(avail, self.wtg_power_set, self.params["rated_power"])

    def _tick(self) -> None:
        self.sim_time += self.tick_period
        ws, lp = self.scenario(self.sim_time)
        wtg = self._compute_wtg_power(ws)
        diesel = clamp(lp - wtg, self.params["diesel_min"], self.params["diesel_max"])

        new_yc = {
            "sim_time": self.sim_time,
            "wind_speed": round(ws, 4),
            "load_power": round(lp, 4),
            "wtg_power": round(wtg, 4),
            "diesel_power": round(diesel, 4),
        }
        new_yx = {
            "wtg_status": 1 if self._available_wind(ws) > 1e-9 and self.wtg_power_set > 0 else 0,
            "diesel_status": 1 if diesel > 1e-9 else 0,
        }

        # 变位检测（sim_time 不计入变位判定）
        changed_yc = [k for k in ("wind_speed", "load_power", "wtg_power", "diesel_power")
                      if self.yc.get(k) != new_yc[k]]
        changed_yx = [k for k in new_yx if self.yx.get(k) != new_yx[k]]

        if changed_yc or changed_yx:
            self.seq += 1
            self.change_log[self.seq] = {"yc": changed_yc, "yx": changed_yx}
        self.yc.update(new_yc)
        self.yx.update(new_yx)

    def _tick_loop(self) -> None:
        while not self._stop.is_set():
            self._tick()
            self._stop.wait(self.tick_period)
