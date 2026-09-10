import sqlite3
import threading
import datetime

DB_NAME = "grid.db"


class GridDB:
    """电网模拟器数据库工具类（线程安全）"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_connection()
        return cls._instance

    def _init_connection(self):
        self.conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._write_lock = threading.Lock()

    def _execute_query(self, sql, params=None, fetch_one=False, fetch_all=False):
        with self._write_lock:
            cursor = self.conn.cursor()
            try:
                if params:
                    cursor.execute(sql, params)
                else:
                    cursor.execute(sql)
                if fetch_one:
                    return cursor.fetchone()
                elif fetch_all:
                    return cursor.fetchall()
                else:
                    self.conn.commit()
                    return cursor.rowcount
            finally:
                cursor.close()

    # ==================== 1. 仿真参数 ====================
    def get_sim_param(self):
        sql = "SELECT sim_start, sim_end, sim_step, sim_status FROM sim_param WHERE id=1"
        row = self._execute_query(sql, fetch_one=True)
        if row:
            return {"sim_start": row[0], "sim_end": row[1], "sim_step": row[2], "sim_status": row[3]}
        return None

    def update_sim_status(self, status):
        sql = "UPDATE sim_param SET sim_status = ? WHERE id=1"
        self._execute_query(sql, (status,))

    def update_sim_time(self, current_time):
        sql = "UPDATE sim_param SET sim_start = ? WHERE id=1"
        self._execute_query(sql, (current_time,))

    # ==================== 2. 环境曲线 ====================
    def get_env_curve(self, sim_time):
        sql = "SELECT wind_speed, load_power FROM env_curve WHERE sim_time = ?"
        row = self._execute_query(sql, (sim_time,), fetch_one=True)
        if row:
            return {"wind_speed": row[0], "load_power": row[1]}
        return None

    def get_curve_count(self):
        sql = "SELECT COUNT(*) FROM env_curve"
        row = self._execute_query(sql, fetch_one=True)
        return row[0] if row else 0

    # ==================== 3. 设备参数 ====================
    def get_device_params(self):
        sql = "SELECT cut_in_wind, cut_out_wind, rated_wind, rated_power, diesel_max, diesel_min FROM device_params WHERE id=1"
        row = self._execute_query(sql, fetch_one=True)
        if row:
            return {
                "cut_in_wind": row[0], "cut_out_wind": row[1],
                "rated_wind": row[2], "rated_power": row[3],
                "diesel_max": row[4], "diesel_min": row[5]
            }
        return None

    def update_wind_params(self, cut_in, cut_out, rated_wind, rated_power):
        sql = "UPDATE device_params SET cut_in_wind=?, cut_out_wind=?, rated_wind=?, rated_power=? WHERE id=1"
        self._execute_query(sql, (cut_in, cut_out, rated_wind, rated_power))

    def update_diesel_params(self, diesel_max, diesel_min):
        sql = "UPDATE device_params SET diesel_max=?, diesel_min=? WHERE id=1"
        self._execute_query(sql, (diesel_max, diesel_min))

    # ==================== 4. SCADA 遥测 ====================
    def get_scada_yc(self):
        sql = "SELECT sim_time, wind_speed, load_power, wtg_power, diesel_power FROM scada_yc WHERE id=1"
        row = self._execute_query(sql, fetch_one=True)
        if row:
            return {
                "sim_time": row[0], "wind_speed": row[1],
                "load_power": row[2], "wtg_power": row[3], "diesel_power": row[4]
            }
        return None

    def update_scada_yc(self, sim_time, wind_speed, load_power, wtg_power, diesel_power):
        sql = "UPDATE scada_yc SET update_time=?, sim_time=?, wind_speed=?, load_power=?, wtg_power=?, diesel_power=? WHERE id=1"
        self._execute_query(sql, (datetime.datetime.now(), sim_time, wind_speed, load_power, wtg_power, diesel_power))

    # ==================== 5. SCADA 遥信 ====================
    def get_scada_yx(self):
        sql = "SELECT wtg_status, diesel_status FROM scada_yx WHERE id=1"
        row = self._execute_query(sql, fetch_one=True)
        if row:
            return {"wtg_status": row[0], "diesel_status": row[1]}
        return None

    def update_scada_yx(self, wtg_status, diesel_status):
        sql = "UPDATE scada_yx SET update_time=?, wtg_status=?, diesel_status=? WHERE id=1"
        self._execute_query(sql, (datetime.datetime.now(), wtg_status, diesel_status))

    # ==================== 6. SCADA 遥调 ====================
    def get_scada_yt(self):
        sql = "SELECT wtg_power_set, diesel_power_set, pitch_angle_set FROM scada_yt WHERE id=1"
        row = self._execute_query(sql, fetch_one=True)
        if row:
            return {
                "wtg_power_set": row[0],
                "diesel_power_set": row[1],
                "pitch_angle_set": row[2]
            }
        return None

    def update_scada_yt_wtg_power_set(self, wtg_power_set):
        sql = "UPDATE scada_yt SET update_time=?, wtg_power_set=? WHERE id=1"
        self._execute_query(sql, (datetime.datetime.now(), wtg_power_set))

    def update_scada_yt_diesel_power_set(self, diesel_power_set):
        sql = "UPDATE scada_yt SET update_time=?, diesel_power_set=? WHERE id=1"
        self._execute_query(sql, (datetime.datetime.now(), diesel_power_set))

    def update_scada_yt_pitch_angle(self, pitch_angle_set):
        sql = "UPDATE scada_yt SET update_time=?, pitch_angle_set=? WHERE id=1"
        self._execute_query(sql, (datetime.datetime.now(), pitch_angle_set))

    # ==================== 7. 历史断面 ====================
    def save_history(self, sim_time, wind_speed, load_power, wtg_power, diesel_power, pitch_angle_set, log_msg=""):
        sql = "INSERT INTO history (sim_time, wind_speed, load_power, wtg_power, diesel_power, pitch_angle_set, log_msg) VALUES (?, ?, ?, ?, ?, ?, ?)"
        self._execute_query(sql, (sim_time, wind_speed, load_power, wtg_power, diesel_power, pitch_angle_set, log_msg))

    # ==================== 8. 系统日志 ====================
    def write_log(self, level, source, message):
        sql = "INSERT INTO sys_log (log_time, level, source, message) VALUES (?, ?, ?, ?)"
        self._execute_query(sql, (datetime.datetime.now(), level, source, message))

    # ==================== 9. 遥控表 ====================
    def add_remote_command(self, cmd_id, target, value, source, status="received"):
        """添加遥控指令记录"""
        sql = "INSERT INTO scada_yk (cmd_id, target, value, source, status, received_at) VALUES (?, ?, ?, ?, ?, ?)"
        self._execute_query(sql, (cmd_id, target, value, source, status, datetime.datetime.now()))

    def get_remote_commands(self, limit=50):
        """获取最近的遥控指令记录"""
        sql = "SELECT * FROM scada_yk ORDER BY id DESC LIMIT ?"
        rows = self._execute_query(sql, (limit,), fetch_all=True)
        return [dict(row) for row in rows] if rows else []

    def update_remote_status(self, cmd_id, status, executed_at=None):
        """更新遥控指令状态"""
        if executed_at is None:
            executed_at = datetime.datetime.now()
        sql = "UPDATE scada_yk SET status=?, executed_at=? WHERE cmd_id=?"
        self._execute_query(sql, (status, executed_at, cmd_id))

    def close(self):
        self.conn.close()