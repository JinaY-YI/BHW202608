"""EMS 主站数据库模块。

管理 ems.db（SQLite）。表结构对应协议第六部分字段映射 + PDF 3.2 节要求：
- sched_params     : 调度参数（采集周期/决策周期/开闭环模式）
- device_params    : 风机与柴发设备参数（以 param_notify 同步为准）
- env_realtime     : 环境实时数据（风速/负荷/仿真时刻）
- device_realtime  : 设备实时状态（含 EMS 计算出的出力设定值）
- scada_yc         : SCADA 遥测（含增量 seq）
- scada_yx         : SCADA 遥信
- scada_yt / scada_yk : 遥调/遥控表（EMS 仅写遥调）
- history          : 历史数据表
- dispatch_history : 调度历史表（含评价指标）
- sys_log          : 系统运行日志
- comm_status      : 通信状态

说明：每个线程/进程各自 new 一个 Database 实例即可安全并发（独立 sqlite3 连接，
WAL 模式 + busy_timeout）。
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sched_params (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data_collect_period REAL NOT NULL DEFAULT 1,
    decision_period REAL NOT NULL DEFAULT 5,
    ctrl_mode_ems INTEGER NOT NULL DEFAULT 1
);
INSERT OR IGNORE INTO sched_params (id) VALUES (1);

CREATE TABLE IF NOT EXISTS device_params (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cut_in_wind REAL NOT NULL DEFAULT 3.0,
    cut_out_wind REAL NOT NULL DEFAULT 25.0,
    rated_wind REAL NOT NULL DEFAULT 12.0,
    rated_power REAL NOT NULL DEFAULT 100.0,
    diesel_max REAL NOT NULL DEFAULT 150.0,
    diesel_min REAL NOT NULL DEFAULT 10.0
);
INSERT OR IGNORE INTO device_params (id) VALUES (1);

CREATE TABLE IF NOT EXISTS env_realtime (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    sim_time REAL,
    wind_speed REAL,
    load_power REAL,
    updated_at TEXT
);
INSERT OR IGNORE INTO env_realtime (id) VALUES (1);

CREATE TABLE IF NOT EXISTS device_realtime (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    sim_time REAL,
    wtg_status INTEGER DEFAULT 0,
    diesel_status INTEGER DEFAULT 0,
    wtg_power REAL,
    diesel_power REAL,
    wtg_power_set REAL,
    diesel_power_set REAL,
    available_wind REAL,
    pitch_angle_set REAL,
    updated_at TEXT
);
INSERT OR IGNORE INTO device_realtime (id) VALUES (1);

CREATE TABLE IF NOT EXISTS scada_yc (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    seq INTEGER NOT NULL DEFAULT 0,
    sim_time REAL,
    wind_speed REAL,
    load_power REAL,
    wtg_power REAL,
    diesel_power REAL,
    updated_at TEXT
);
INSERT OR IGNORE INTO scada_yc (id) VALUES (1);

CREATE TABLE IF NOT EXISTS scada_yx (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    wtg_status INTEGER DEFAULT 0,
    diesel_status INTEGER DEFAULT 0,
    updated_at TEXT
);
INSERT OR IGNORE INTO scada_yx (id) VALUES (1);

CREATE TABLE IF NOT EXISTS scada_yt (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    value REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    executed_seq INTEGER,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS scada_yk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    value REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    executed_seq INTEGER,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_time REAL,
    wind_speed REAL,
    load_power REAL,
    wtg_power REAL,
    diesel_power REAL,
    pitch_angle_set REAL,
    recorded_at TEXT
);

CREATE TABLE IF NOT EXISTS dispatch_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_time REAL,
    wind_speed REAL,
    load_power REAL,
    wtg_power REAL,
    diesel_power REAL,
    available_wind REAL,
    wtg_power_set REAL,
    diesel_power_set REAL,
    diesel_reserve REAL,
    renewable_share REAL,
    balance_error REAL,
    shortfall REAL,
    recorded_at TEXT
);

CREATE TABLE IF NOT EXISTS sys_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT,
    level TEXT,
    source TEXT,
    message TEXT
);

CREATE TABLE IF NOT EXISTS comm_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    connected INTEGER DEFAULT 0,
    remote_addr TEXT,
    last_rx_time TEXT,
    last_tx_time TEXT,
    last_data_update TEXT,
    last_command_result TEXT
);
INSERT OR IGNORE INTO comm_status (id) VALUES (1);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


class Database:
    """ems.db 访问封装。每个线程实例化一个（独立连接）。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or config.DEFAULT_DB_PATH
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._lock = threading.Lock()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- 通用 ----------------------------------------------------------------
    def _execute(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query_one(self, sql: str, params=()) -> Optional[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            row = cur.fetchone()
            cur.close()
            return row

    def _query_all(self, sql: str, params=()) -> List[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
            cur.close()
            return list(rows)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- 调度参数 ------------------------------------------------------------
    def get_sched_params(self) -> Dict[str, Any]:
        row = self._query_one("SELECT * FROM sched_params WHERE id=1")
        return dict(row) if row else {}

    def set_sched_params(self, collect_period=None, decision_period=None, ctrl_mode=None) -> None:
        row = self.get_sched_params()
        cp = row.get("data_collect_period") if collect_period is None else float(collect_period)
        dp = row.get("decision_period") if decision_period is None else float(decision_period)
        cm = row.get("ctrl_mode_ems") if ctrl_mode is None else int(ctrl_mode)
        self._execute(
            "UPDATE sched_params SET data_collect_period=?, decision_period=?, ctrl_mode_ems=? WHERE id=1",
            (cp, dp, cm),
        )

    # -- 设备参数 ------------------------------------------------------------
    def get_device_params(self) -> Dict[str, Any]:
        row = self._query_one("SELECT * FROM device_params WHERE id=1")
        return dict(row) if row else {}

    def update_device_params(self, **fields) -> None:
        allowed = {
            "cut_in_wind", "cut_out_wind", "rated_wind",
            "rated_power", "diesel_max", "diesel_min",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        cols = ", ".join(f"{k}=?" for k in updates)
        self._execute(
            f"UPDATE device_params SET {cols} WHERE id=1",
            tuple(updates.values()),
        )

    # -- 环境实时 ------------------------------------------------------------
    def get_env_realtime(self) -> Dict[str, Any]:
        row = self._query_one("SELECT * FROM env_realtime WHERE id=1")
        return dict(row) if row else {}

    def set_env_realtime(self, sim_time=None, wind_speed=None, load_power=None) -> None:
        cur = self.get_env_realtime()
        st = cur.get("sim_time") if sim_time is None else sim_time
        ws = cur.get("wind_speed") if wind_speed is None else wind_speed
        lp = cur.get("load_power") if load_power is None else load_power
        self._execute(
            "UPDATE env_realtime SET sim_time=?, wind_speed=?, load_power=?, updated_at=? WHERE id=1",
            (st, ws, lp, _now()),
        )

    # -- 设备实时 ------------------------------------------------------------
    def get_device_realtime(self) -> Dict[str, Any]:
        row = self._query_one("SELECT * FROM device_realtime WHERE id=1")
        return dict(row) if row else {}

    def set_device_realtime(self, **fields) -> None:
        allowed = {
            "sim_time", "wtg_status", "diesel_status", "wtg_power", "diesel_power",
            "wtg_power_set", "diesel_power_set", "available_wind", "pitch_angle_set",
        }
        cur = self.get_device_realtime()
        merged = dict(cur)
        merged.update({k: v for k, v in fields.items() if k in allowed})
        self._execute(
            """UPDATE device_realtime SET sim_time=?, wtg_status=?, diesel_status=?,
               wtg_power=?, diesel_power=?, wtg_power_set=?, diesel_power_set=?,
               available_wind=?, pitch_angle_set=?, updated_at=? WHERE id=1""",
            (
                merged.get("sim_time"), merged.get("wtg_status"), merged.get("diesel_status"),
                merged.get("wtg_power"), merged.get("diesel_power"),
                merged.get("wtg_power_set"), merged.get("diesel_power_set"),
                merged.get("available_wind"), merged.get("pitch_angle_set"), _now(),
            ),
        )

    # -- SCADA 遥测 ----------------------------------------------------------
    def get_last_seq(self) -> int:
        row = self._query_one("SELECT seq FROM scada_yc WHERE id=1")
        return int(row["seq"]) if row else 0

    def set_last_seq(self, seq: int) -> None:
        self._execute("UPDATE scada_yc SET seq=? WHERE id=1", (int(seq),))

    def get_scada_yc(self) -> Dict[str, Any]:
        row = self._query_one("SELECT * FROM scada_yc WHERE id=1")
        return dict(row) if row else {}

    def update_scada_yc(self, **fields) -> None:
        """增量更新遥测（只更新提供的字段）。"""
        allowed = {"seq", "sim_time", "wind_speed", "load_power", "wtg_power", "diesel_power"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return
        cols = ", ".join(f"{k}=?" for k in updates)
        self._execute(
            f"UPDATE scada_yc SET {cols}, updated_at=? WHERE id=1",
            tuple(updates.values()) + (_now(),),
        )

    # -- SCADA 遥信 ----------------------------------------------------------
    def get_scada_yx(self) -> Dict[str, Any]:
        row = self._query_one("SELECT * FROM scada_yx WHERE id=1")
        return dict(row) if row else {}

    def update_scada_yx(self, **fields) -> None:
        allowed = {"wtg_status", "diesel_status"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return
        cols = ", ".join(f"{k}=?" for k in updates)
        self._execute(
            f"UPDATE scada_yx SET {cols}, updated_at=? WHERE id=1",
            tuple(updates.values()) + (_now(),),
        )

    # -- 遥调/遥控命令表 -----------------------------------------------------
    def add_command(self, table: str, target: str, value: float) -> int:
        assert table in ("scada_yt", "scada_yk"), "unknown command table"
        cur = self._execute(
            f"INSERT INTO {table} (target, value, status, created_at) VALUES (?,?,?,?)",
            (target, float(value), "pending", _now()),
        )
        return cur.lastrowid

    def get_pending_commands(self, table: str = "scada_yt") -> List[Dict[str, Any]]:
        rows = self._query_all(
            f"SELECT * FROM {table} WHERE status='pending' ORDER BY id"
        )
        return [dict(r) for r in rows]

    def mark_command_status(self, table: str, cmd_id: int, status: str,
                            executed_seq: Optional[int] = None) -> None:
        self._execute(
            f"UPDATE {table} SET status=?, executed_seq=? WHERE id=?",
            (status, executed_seq, cmd_id),
        )

    def get_commands(self, table: str = "scada_yt", limit: int = 100) -> List[Dict[str, Any]]:
        rows = self._query_all(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # -- 历史数据 ------------------------------------------------------------
    def add_history(self, sim_time=None, wind_speed=None, load_power=None,
                    wtg_power=None, diesel_power=None, pitch_angle_set=None) -> None:
        self._execute(
            """INSERT INTO history (sim_time, wind_speed, load_power, wtg_power,
               diesel_power, pitch_angle_set, recorded_at) VALUES (?,?,?,?,?,?,?)""",
            (sim_time, wind_speed, load_power, wtg_power, diesel_power,
             pitch_angle_set, _now()),
        )

    def get_history(self, limit: int = 200) -> List[Dict[str, Any]]:
        rows = self._query_all(
            "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows][::-1]

    # -- 调度历史 ------------------------------------------------------------
    def add_dispatch_history(self, **fields) -> None:
        allowed = {
            "sim_time", "wind_speed", "load_power", "wtg_power", "diesel_power",
            "available_wind", "wtg_power_set", "diesel_power_set", "diesel_reserve",
            "renewable_share", "balance_error", "shortfall",
        }
        vals = {k: fields.get(k) for k in allowed}
        self._execute(
            """INSERT INTO dispatch_history (sim_time, wind_speed, load_power, wtg_power,
               diesel_power, available_wind, wtg_power_set, diesel_power_set, diesel_reserve,
               renewable_share, balance_error, shortfall, recorded_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                vals["sim_time"], vals["wind_speed"], vals["load_power"],
                vals["wtg_power"], vals["diesel_power"], vals["available_wind"],
                vals["wtg_power_set"], vals["diesel_power_set"], vals["diesel_reserve"],
                vals["renewable_share"], vals["balance_error"], vals["shortfall"],
                _now(),
            ),
        )

    def get_dispatch_history(self, limit: int = 200) -> List[Dict[str, Any]]:
        rows = self._query_all(
            "SELECT * FROM dispatch_history ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows][::-1]

    # -- 日志 ----------------------------------------------------------------
    def add_log(self, level: str, source: str, message: str) -> None:
        self._execute(
            "INSERT INTO sys_log (time, level, source, message) VALUES (?,?,?,?)",
            (_now(), level, source, message),
        )

    def get_logs(self, limit: int = 200) -> List[Dict[str, Any]]:
        rows = self._query_all(
            "SELECT * FROM sys_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows][::-1]

    # -- 通信状态 ------------------------------------------------------------
    def get_comm_status(self) -> Dict[str, Any]:
        row = self._query_one("SELECT * FROM comm_status WHERE id=1")
        return dict(row) if row else {}

    def set_comm_status(self, **fields) -> None:
        allowed = {
            "connected", "remote_addr", "last_rx_time", "last_tx_time",
            "last_data_update", "last_command_result",
        }
        cur = self.get_comm_status()
        merged = dict(cur)
        merged.update({k: v for k, v in fields.items() if k in allowed})
        self._execute(
            """UPDATE comm_status SET connected=?, remote_addr=?, last_rx_time=?,
               last_tx_time=?, last_data_update=?, last_command_result=? WHERE id=1""",
            (
                merged.get("connected"), merged.get("remote_addr"),
                merged.get("last_rx_time"), merged.get("last_tx_time"),
                merged.get("last_data_update"), merged.get("last_command_result"),
            ),
        )
