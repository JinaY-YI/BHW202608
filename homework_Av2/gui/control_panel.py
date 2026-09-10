"""
仿真控制面板：启动/暂停/停止/重置 + 参数设置
"""
import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3


class ControlPanel:
    """仿真控制面板"""

    def __init__(self, parent, db, engine, log_callback=None):
        self.parent = parent
        self.db = db
        self.engine = engine
        self.log = log_callback or print

        self.colors = {
            'bg': '#0d1117', 'card': '#161b22', 'accent': '#1f6feb',
            'success': '#3fb950', 'warning': '#d29922', 'danger': '#f85149',
            'text': '#f0f6fc', 'text_dim': '#8b949e', 'border': '#30363d'
        }

        self._build()

    def _build(self):
        card = self._create_card(self.parent, "🎮 仿真控制")
        card.pack(fill=tk.X, pady=(0, 8))

        ctrl_row = tk.Frame(card, bg=self.colors['card'])
        ctrl_row.pack(fill=tk.X, pady=5, padx=5)

        self.btn_start = tk.Button(ctrl_row, text="▶ 启动", command=self._start,
                                   bg=self.colors['success'], fg='white', font=('微软雅黑', 10, 'bold'), padx=12)
        self.btn_start.pack(side=tk.LEFT, padx=3)

        self.btn_pause = tk.Button(ctrl_row, text="⏸ 暂停", command=self._pause,
                                   bg=self.colors['warning'], fg='white', font=('微软雅黑', 10, 'bold'), padx=12, state=tk.DISABLED)
        self.btn_pause.pack(side=tk.LEFT, padx=3)

        self.btn_stop = tk.Button(ctrl_row, text="⏹ 停止", command=self._stop,
                                  bg=self.colors['danger'], fg='white', font=('微软雅黑', 10, 'bold'), padx=12, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=3)

        self.btn_reset = tk.Button(ctrl_row, text="🔄 重置", command=self._reset,
                                   bg=self.colors['accent'], fg='white', font=('微软雅黑', 10, 'bold'), padx=12)
        self.btn_reset.pack(side=tk.LEFT, padx=3)

        # ===== 参数行 =====
        param_row = tk.Frame(card, bg=self.colors['card'])
        param_row.pack(fill=tk.X, pady=5, padx=5)

        tk.Label(param_row, text="起始:", font=('微软雅黑', 9), fg=self.colors['text_dim'], bg=self.colors['card']).pack(side=tk.LEFT)
        self.start_entry = tk.Entry(param_row, width=6, font=('Consolas', 9), bg=self.colors['bg'], fg=self.colors['text'])
        self.start_entry.insert(0, "0")
        self.start_entry.pack(side=tk.LEFT, padx=2)

        tk.Label(param_row, text="结束:", font=('微软雅黑', 9), fg=self.colors['text_dim'], bg=self.colors['card']).pack(side=tk.LEFT, padx=(10,0))
        self.end_entry = tk.Entry(param_row, width=6, font=('Consolas', 9), bg=self.colors['bg'], fg=self.colors['text'])
        self.end_entry.insert(0, "999999")
        self.end_entry.pack(side=tk.LEFT, padx=2)

        tk.Label(param_row, text="步长:", font=('微软雅黑', 9), fg=self.colors['text_dim'], bg=self.colors['card']).pack(side=tk.LEFT, padx=(10,0))
        self.step_entry = tk.Entry(param_row, width=4, font=('Consolas', 9), bg=self.colors['bg'], fg=self.colors['text'])
        self.step_entry.insert(0, "1")
        self.step_entry.pack(side=tk.LEFT, padx=2)

        tk.Button(param_row, text="应用", command=self._apply_params,
                  bg=self.colors['border'], fg=self.colors['text'], font=('微软雅黑', 9), padx=8).pack(side=tk.LEFT, padx=10)

        # ===== 状态标签（左侧） =====
        self.status_label = tk.Label(param_row, text="● 系统就绪", font=('微软雅黑', 9),
                                     fg=self.colors['success'], bg=self.colors['card'])
        self.status_label.pack(side=tk.RIGHT, padx=5)

        # ===== 时间标签（右侧） =====
        self.time_label = tk.Label(param_row, text="⏱ t=0s", font=('微软雅黑', 9),
                                   fg='#ffffff', bg=self.colors['card'])
        self.time_label.pack(side=tk.RIGHT, padx=10)

    def _create_card(self, parent, title):
        frame = tk.Frame(parent, bg=self.colors['card'], relief=tk.RIDGE, bd=1)
        header = tk.Frame(frame, bg=self.colors['accent'], height=28)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text=title, font=('微软雅黑', 9, 'bold'), fg='white', bg=self.colors['accent']).pack(anchor='w', padx=10, pady=4)
        return frame

    def _apply_params(self):
        try:
            start = int(self.start_entry.get())
            end = int(self.end_entry.get())
            step = int(self.step_entry.get())
            conn = sqlite3.connect("grid.db")
            cursor = conn.cursor()
            cursor.execute("UPDATE sim_param SET sim_start=?, sim_end=?, sim_step=? WHERE id=1", (start, end, step))
            conn.commit()
            cursor.execute("SELECT sim_start, sim_end, sim_step FROM sim_param WHERE id=1")
            row = cursor.fetchone()
            conn.close()
            if row:
                self.log(f"✅ 仿真参数已更新: 起始={row[0]}, 结束={row[1]}, 步长={row[2]}")
            else:
                self.log("⚠️ 参数更新失败", "WARNING")
        except ValueError:
            messagebox.showerror("错误", "请输入有效的数字")

    def _start(self):
        self.db.update_sim_status("RUNNING")
        self.engine.start()
        self.btn_start.config(state=tk.DISABLED)
        self.btn_pause.config(state=tk.NORMAL, text="⏸ 暂停", command=self._pause)
        self.btn_stop.config(state=tk.NORMAL)
        self.status_label.config(text="● 仿真运行中", fg=self.colors['success'])
        self.log("仿真已启动")

    def _pause(self):
        self.db.update_sim_status("PAUSED")
        self.engine.pause()
        self.btn_pause.config(text="▶ 继续", command=self._resume)
        self.status_label.config(text="● 已暂停", fg=self.colors['warning'])
        self.log("仿真已暂停")

    def _resume(self):
        self.db.update_sim_status("RUNNING")
        self.engine.resume()
        self.btn_pause.config(text="⏸ 暂停", command=self._pause)
        self.status_label.config(text="● 仿真运行中", fg=self.colors['success'])
        self.log("仿真已恢复")

    def _stop(self):
        self.db.update_sim_status("STOPPED")
        self.engine.stop()
        self.btn_start.config(state=tk.NORMAL)
        self.btn_pause.config(state=tk.DISABLED, text="⏸ 暂停", command=self._pause)
        self.btn_stop.config(state=tk.DISABLED)
        self.status_label.config(text="● 已停止", fg=self.colors['danger'])
        self.log("仿真已停止")

    def _reset(self):
        self.engine.reset()
        self.engine.set_wind_online(False)

        sim = self.db.get_sim_param()
        if sim:
            current_end = sim.get("sim_end", 999999)
        else:
            current_end = 999999
        self.end_entry.delete(0, tk.END)
        self.end_entry.insert(0, str(current_end))

        try:
            conn = sqlite3.connect("grid.db")
            cursor = conn.cursor()
            cursor.execute("DELETE FROM history")
            conn.commit()
            conn.close()
            self.log("历史表已清空")
        except Exception as e:
            self.log(f"清空历史表失败: {e}", "ERROR")

        params = self.db.get_device_params()
        diesel_min = params.get("diesel_min", 5.0) if params else 5.0

        try:
            conn = sqlite3.connect("grid.db")
            cursor = conn.cursor()
            cursor.execute("SELECT wind_speed, load_power FROM env_curve WHERE sim_time=0")
            row = cursor.fetchone()
            conn.close()
            if row:
                wind_speed_0, load_power_0 = row[0], row[1]
            else:
                wind_speed_0, load_power_0 = 8.0, 40.0
        except:
            wind_speed_0, load_power_0 = 8.0, 40.0

        self.db.update_scada_yc(0, wind_speed_0, load_power_0, 0.0, diesel_min)
        self.db.update_scada_yx(0, 1)
        self.db.update_scada_yt_wtg_power_set(0)
        self.db.update_scada_yt_diesel_power_set(diesel_min)
        self.db.update_scada_yt_pitch_angle(0)

        self.btn_start.config(state=tk.NORMAL)
        self.btn_pause.config(state=tk.DISABLED, text="⏸ 暂停", command=self._pause)
        self.btn_stop.config(state=tk.DISABLED)
        self.status_label.config(text="● 已重置", fg=self.colors['warning'])
        self.time_label.config(text="⏱ t=0s")
        self.log(f"仿真已重置（柴发初始功率={diesel_min}kW）")