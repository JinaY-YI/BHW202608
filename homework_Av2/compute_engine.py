"""
计算引擎 - 独立线程运行
"""
import time
import threading
import datetime
from db_utils import GridDB

class ComputeEngine:
    def __init__(self):
        self.db = GridDB()
        self.running = False
        self.paused = False
        self.thread = None
        self.lock = threading.Lock()
        self.current_time = 0
        self.wind_online = False
        self._load_params()

    def set_wind_online(self, online):
        self.wind_online = online
        if not online:
            self.db.update_scada_yx(0, 1)
            self.db.write_log("INFO", "compute_engine", "风机断开连接，强制停机")

    def _load_params(self):
        params = self.db.get_device_params()
        self.params = params if params else {
            "cut_in_wind": 3.0, "cut_out_wind": 25.0,
            "rated_wind": 12.0, "rated_power": 50.0,
            "diesel_max": 80.0, "diesel_min": 5.0
        }

    def _calc_wind_power(self, wind_speed, pitch_angle):
        cut_in = self.params["cut_in_wind"]
        cut_out = self.params["cut_out_wind"]
        rated_wind = self.params["rated_wind"]
        rated_power = self.params["rated_power"]
        if wind_speed < cut_in or wind_speed > cut_out:
            return 0.0, 0
        if wind_speed <= rated_wind:
            base = (wind_speed - cut_in) / (rated_wind - cut_in) * rated_power
        else:
            base = rated_power
        pitch_factor = max(0.0, 1.0 - pitch_angle / 90.0)
        actual = base * pitch_factor
        actual = min(actual, rated_power)
        actual = max(0.0, actual)
        return round(actual, 2), 1

    def _compute_loop(self):
        print(f"[{datetime.datetime.now()}] 计算引擎启动")
        self.db.write_log("INFO", "compute_engine", "计算引擎启动")

        while self.running:
            if self.paused:
                time.sleep(0.1)
                continue

            sim = self.db.get_sim_param()
            if not sim:
                break
            if sim["sim_status"] == "STOPPED":
                time.sleep(0.1)
                continue

            if self.current_time > sim["sim_end"]:
                self.current_time = 0
                print(f"[{datetime.datetime.now()}] 仿真循环到起点 t=0")
                self.db.write_log("INFO", "compute_engine", "仿真自动循环到起点")

            curve = self.db.get_env_curve(self.current_time)
            if not curve:
                self.current_time = 0
                continue

            wind_speed = curve["wind_speed"]
            load_power = curve["load_power"]

            yt = self.db.get_scada_yt()
            if yt:
                wtg_power_set = yt.get("wtg_power_set", 0.0)
                diesel_power_set = yt.get("diesel_power_set", 0.0)
                pitch_angle = yt.get("pitch_angle_set", 0.0)
            else:
                wtg_power_set = 0.0
                diesel_power_set = 0.0
                pitch_angle = 0.0

            yx = self.db.get_scada_yx()
            wtg_status = yx["wtg_status"] if yx else 0

            if self.wind_online and wtg_status == 1:
                wtg_power, wtg_status = self._calc_wind_power(wind_speed, pitch_angle)
            else:
                wtg_power = 0.0
                wtg_status = 0

            diesel_max = self.params["diesel_max"]
            diesel_min = self.params["diesel_min"]
            diesel_power = max(diesel_min, min(diesel_max, diesel_power_set))

            diesel_status = 1 if diesel_power > 0 else 0

            self.db.update_scada_yc(
                sim_time=self.current_time,
                wind_speed=wind_speed,
                load_power=load_power,
                wtg_power=wtg_power,
                diesel_power=diesel_power
            )
            self.db.update_scada_yx(wtg_status, diesel_status)
            self.db.save_history(
                sim_time=self.current_time,
                wind_speed=wind_speed,
                load_power=load_power,
                wtg_power=wtg_power,
                diesel_power=diesel_power,
                pitch_angle_set=pitch_angle,
                log_msg=f"t={self.current_time}"
            )

            print(f"[t={self.current_time}] 风速:{wind_speed:.1f} 负荷:{load_power:.1f} "
                  f"风机:{wtg_power:.1f}(set={wtg_power_set:.1f}) 柴发:{diesel_power:.1f}(set={diesel_power_set:.1f})")

            self.current_time += 1
            self.db.update_sim_time(self.current_time)
            time.sleep(1)

        print(f"[{datetime.datetime.now()}] 计算引擎停止")
        self.db.write_log("INFO", "compute_engine", "计算引擎停止")

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.running = True
            self.paused = False
            self.current_time = 0
            self._load_params()
            self.thread = threading.Thread(target=self._compute_loop, daemon=True)
            self.thread.start()
            print(f"[{datetime.datetime.now()}] 计算引擎线程已创建")

    def pause(self):
        self.paused = True
        self.db.write_log("INFO", "compute_engine", "计算引擎暂停")

    def resume(self):
        self.paused = False
        self.db.write_log("INFO", "compute_engine", "计算引擎恢复")

    def stop(self):
        self.running = False
        self.paused = False
        if self.thread:
            self.thread.join(timeout=2)
            self.thread = None
        print(f"[{datetime.datetime.now()}] 计算引擎已停止")

    def reset(self):
        self.stop()
        self.current_time = 0
        self.wind_online = False
        self.db.update_sim_time(0)
        self.db.update_sim_status("STOPPED")
        self.db.update_scada_yx(0, 1)
        self.db.update_scada_yt_wtg_power_set(0)
        self.db.update_scada_yt_diesel_power_set(0)
        self.db.update_scada_yt_pitch_angle(0)

        # ===== 柴发初始化为最低功率 =====
        diesel_min = self.params.get("diesel_min", 5.0)
        # 重置 scada_yc 时，diesel_power 设为 diesel_min
        # 但 scada_yc 的更新由计算循环负责，重置时只清空遥调，实际功率由计算循环重新写入
        # 所以我们只需要确保计算循环从 t=0 开始时会使用柴油最低功率

        print(f"[{datetime.datetime.now()}] 仿真已重置到 t=0（柴发初始功率={diesel_min}kW）")
        self.db.write_log("INFO", "compute_engine", f"仿真重置到起点（柴发初始功率={diesel_min}kW）")