#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
南极考察站微电网 —— 风机控制器侧数据库初始化（修正版）
修正点：
1. 新增显式表名：ctrl_runtime_info（控制器运行状态）和 history_data（历史记录）
2. 插入初始占位数据，确保打开表格时不为空
"""

import sqlite3
import os
import time

DB_PATH = "wind.db/wind.db"  # 数据库文件路径


def get_db_connection(db_path=DB_PATH):
    """获取数据库连接"""
    dirname = os.path.dirname(db_path)
    if dirname and not os.path.exists(dirname):
        os.makedirs(dirname)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    conn = get_db_connection()
    cursor = conn.cursor()

    # ================== 1. 设备参数表 ==================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS device_params (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cut_in_wind REAL NOT NULL DEFAULT 3.0,
            cut_out_wind REAL NOT NULL DEFAULT 25.0,
            rated_wind REAL NOT NULL DEFAULT 12.0,
            rated_power REAL NOT NULL DEFAULT 100.0,
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ================== 2. 控制器运行状态表 (ctrl_runtime_info) ==================
    # 对应任务："控制器运行状态和控制信息（风机启停状态、桨距角、输出功率、输出功率设定、闭环/开环等）"
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ctrl_runtime_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wtg_status INTEGER DEFAULT 0,               -- 风机启停 (0停/1运行)
            pitch_angle_set REAL DEFAULT 0.0,           -- 目标桨距角 (°)
            wtg_power REAL DEFAULT 0.0,                 -- 输出功率 (kW，来自电网模拟器)
            wtg_power_set REAL DEFAULT 0.0,             -- 输出功率设定 (kW，来自EMS)
            ctrl_mode_wind INTEGER DEFAULT 0,           -- 闭环/开环 (0开环/1闭环)
            available_power REAL DEFAULT 0.0,           -- 当前风速下理论最大功率
            link_grid INTEGER DEFAULT 0,                -- 与电网模拟器TCP连接状态
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ================== 3. SCADA 遥测实时表 (scada_yc) ==================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scada_yc (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wind_speed REAL DEFAULT 0.0,
            load_power REAL DEFAULT 0.0,
            wtg_power REAL DEFAULT 0.0,
            diesel_power REAL DEFAULT 0.0,
            pitch_angle_set REAL DEFAULT 0.0,
            available_power REAL DEFAULT 0.0,
            wtg_power_set REAL DEFAULT 0.0,
            sim_time INTEGER DEFAULT 0,
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ================== 4. SCADA 遥信实时表 (scada_yx) ==================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scada_yx (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wtg_status INTEGER DEFAULT 0,
            diesel_status INTEGER DEFAULT 0,
            ctrl_mode_wind INTEGER DEFAULT 0,
            link_grid INTEGER DEFAULT 0,
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ================== 5. 遥调历史表 (scada_yt) ==================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scada_yt (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,
            value REAL NOT NULL,
            time INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            executed_seq INTEGER,
            record_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ================== 6. 遥控历史表 (scada_yk) ==================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scada_yk (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,
            value INTEGER NOT NULL,
            time INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            executed_seq INTEGER,
            record_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ================== 7. 【新增】历史数据表 (history_data) ==================
    # 对应任务："控制策略和运行状态历史记录表" + "SCADA 四遥历史记录表"（合并存储全量快照）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER,                -- 存储接收时刻（或仿真时刻）
    wind_speed REAL,
    wtg_power_set REAL,
    wtg_power REAL,
    pitch_angle_set REAL,
    available_power REAL,
    wtg_status INTEGER,
    ctrl_mode_wind INTEGER,
    link_grid INTEGER
        )
    ''')

    # ================== 8. 系统日志表 (sys_log) ==================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sys_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            level TEXT NOT NULL,
            source TEXT,
            message TEXT NOT NULL
        )
    ''')

    # ---------- 插入占位数据，确保所有表至少有一行，打开不为空 ----------

    # 1. 设备参数（默认值）
    cursor.execute("SELECT COUNT(*) FROM device_params")
    if cursor.fetchone()[0] == 0:
        cursor.execute('''
            INSERT INTO device_params (cut_in_wind, cut_out_wind, rated_wind, rated_power)
            VALUES (3.0, 25.0, 12.0, 100.0)
        ''')

    # 2. 控制器运行状态（初始值：停机、开环、无连接）
    cursor.execute("SELECT COUNT(*) FROM ctrl_runtime_info")
    if cursor.fetchone()[0] == 0:
        cursor.execute('''
            INSERT INTO ctrl_runtime_info 
            (wtg_status, pitch_angle_set, wtg_power, wtg_power_set, ctrl_mode_wind, available_power, link_grid)
            VALUES (0, 0.0, 0.0, 0.0, 0, 0.0, 0)
        ''')
        print("插入 ctrl_runtime_info 初始记录")

    # 3. 遥测表占位
    cursor.execute("SELECT COUNT(*) FROM scada_yc")
    if cursor.fetchone()[0] == 0:
        cursor.execute('''INSERT INTO scada_yc DEFAULT VALUES''')

    # 4. 遥信表占位
    cursor.execute("SELECT COUNT(*) FROM scada_yx")
    if cursor.fetchone()[0] == 0:
        cursor.execute('''INSERT INTO scada_yx DEFAULT VALUES''')

    # 5. 历史数据表（插入一条 sim_time=0 的启动记录，表示系统刚上电）
    cursor.execute("SELECT COUNT(*) FROM history_data")
    if cursor.fetchone()[0] == 0:
        cursor.execute('''
            INSERT INTO history_data 
            (sim_time, wind_speed, load_power, wtg_power, diesel_power, pitch_angle_set, wtg_status, ctrl_mode_wind, available_power, wtg_power_set)
            VALUES (0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0.0, 0.0)
        ''')
        print("插入 history_data 初始记录（仿真时刻 0）")

    # 6. 日志表占位（记录数据库初始化事件）
    cursor.execute("SELECT COUNT(*) FROM sys_log")
    if cursor.fetchone()[0] == 0:
        cursor.execute('''
            INSERT INTO sys_log (level, source, message)
            VALUES ('INFO', 'init_db', '数据库初始化完成，已创建所有表并插入默认记录')
        ''')

    conn.commit()
    cursor.close()
    conn.close()
    print(f"数据库修正完成：{os.path.abspath(DB_PATH)}")

if __name__ == "__main__":
    # 如果想完全重置，可以先删除 wind.db 再运行
    init_database()