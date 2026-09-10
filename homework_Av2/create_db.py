import sqlite3
import datetime
import math

DB_NAME = "grid.db"

def create_database():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    print(f"[{datetime.datetime.now()}] 正在初始化数据库: {DB_NAME} ...")

    # ===== 仿真参数表 =====
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sim_param (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            sim_start INTEGER DEFAULT 0,
            sim_end INTEGER DEFAULT 999999,
            sim_step INTEGER DEFAULT 1,
            sim_status TEXT DEFAULT 'STOPPED'
        )
    ''')

    # ===== 环境曲线表 =====
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS env_curve (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sim_time INTEGER NOT NULL UNIQUE,
            wind_speed REAL NOT NULL,
            load_power REAL NOT NULL
        )
    ''')

    # ===== 设备参数表 =====
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS device_params (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            cut_in_wind REAL DEFAULT 3.0,
            cut_out_wind REAL DEFAULT 25.0,
            rated_wind REAL DEFAULT 12.0,
            rated_power REAL DEFAULT 50.0,
            diesel_max REAL DEFAULT 80.0,
            diesel_min REAL DEFAULT 5.0
        )
    ''')

    # ===== SCADA 遥测表 =====
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scada_yc (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            update_time TEXT,
            sim_time INTEGER DEFAULT 0,
            wind_speed REAL DEFAULT 0.0,
            load_power REAL DEFAULT 0.0,
            wtg_power REAL DEFAULT 0.0,
            diesel_power REAL DEFAULT 5.0
        )
    ''')

    # ===== SCADA 遥信表 =====
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scada_yx (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            update_time TEXT,
            wtg_status INTEGER DEFAULT 0,
            diesel_status INTEGER DEFAULT 1
        )
    ''')

    # ===== SCADA 遥调表 =====
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scada_yt (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            update_time TEXT,
            wtg_power_set REAL DEFAULT 0.0,
            diesel_power_set REAL DEFAULT 5.0,
            pitch_angle_set REAL DEFAULT 0.0
        )
    ''')

    # ===== 新增：遥控表 =====
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scada_yk (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cmd_id TEXT,
            target TEXT NOT NULL,
            value INTEGER NOT NULL,
            source TEXT NOT NULL,
            status TEXT DEFAULT 'received',
            received_at TEXT,
            executed_at TEXT
        )
    ''')

    # ===== 历史断面表 =====
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sim_time INTEGER NOT NULL,
            wind_speed REAL,
            load_power REAL,
            wtg_power REAL,
            diesel_power REAL,
            pitch_angle_set REAL,
            log_msg TEXT
        )
    ''')

    # ===== 系统日志表 =====
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sys_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_time TEXT,
            level TEXT,
            source TEXT,
            message TEXT
        )
    ''')

    print("✅ 所有表结构创建成功（含 scada_yk 遥控表）")

    # ===== 插入默认数据 =====
    cursor.execute("INSERT OR IGNORE INTO sim_param (id, sim_start, sim_end, sim_step, sim_status) VALUES (1, 0, 999999, 1, 'STOPPED')")
    cursor.execute("INSERT OR IGNORE INTO device_params (id, cut_in_wind, cut_out_wind, rated_wind, rated_power, diesel_max, diesel_min) VALUES (1, 3.0, 25.0, 12.0, 50.0, 80.0, 5.0)")
    cursor.execute("INSERT OR IGNORE INTO scada_yc (id, update_time, sim_time, wind_speed, load_power, wtg_power, diesel_power) VALUES (1, datetime('now'), 0, 0.0, 0.0, 0.0, 5.0)")
    cursor.execute("INSERT OR IGNORE INTO scada_yx (id, update_time, wtg_status, diesel_status) VALUES (1, datetime('now'), 0, 1)")
    cursor.execute("INSERT OR IGNORE INTO scada_yt (id, update_time, wtg_power_set, diesel_power_set, pitch_angle_set) VALUES (1, datetime('now'), 0.0, 5.0, 0.0)")

    # ===== 生成初始曲线 =====
    cursor.execute("SELECT COUNT(*) FROM env_curve")
    if cursor.fetchone()[0] == 0:
        curves = []
        for t in range(10000):
            wind = round(8.0 + 7.0 * math.sin(2 * math.pi * t / 50), 2)
            load = round(40.0 + 20.0 * math.sin(2 * math.pi * t / 30), 2)
            wind = max(0.0, wind)
            load = max(0.0, load)
            curves.append((t, wind, load))
        cursor.executemany("INSERT INTO env_curve (sim_time, wind_speed, load_power) VALUES (?, ?, ?)", curves)
        print(f"✅ 插入 {len(curves)} 条初始曲线")

    conn.commit()
    cursor.close()
    conn.close()
    print(f"[{datetime.datetime.now()}] 🎉 数据库 '{DB_NAME}' 初始化完成！")
    print("✅ 已增加 scada_yk 遥控表")

if __name__ == "__main__":
    create_database()