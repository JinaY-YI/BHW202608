"""
曲线配置Tab：风速 + 负荷曲线配置（正弦/恒定/方波/噪声/正弦噪声）
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import sqlite3
import csv
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
# ===== 中文字体配置 =====
try:
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS']
except:
    pass
plt.rcParams['axes.unicode_minus'] = False
# =========================
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from core.waveform import (
    generate_sine_wave, generate_constant, generate_square_wave,
    generate_noise_wave, generate_sine_with_noise_wave
)


class CurveConfigTab:
    """曲线配置Tab"""

    def __init__(self, notebook, db, colors, log_callback=None):
        self.db = db
        self.colors = colors
        self.log = log_callback or print

        parent = tk.Frame(notebook, bg=colors['bg'])
        notebook.add(parent, text="🌤️ 曲线配置")
        self.parent = parent

        # 风速参数
        self.wind_type = tk.StringVar(value="正弦波")
        self.wind_params = {
            "peak": tk.StringVar(value="15.0"),
            "valley": tk.StringVar(value="1.0"),
            "period": tk.StringVar(value="50"),
            "high_val": tk.StringVar(value="15.0"),
            "low_val": tk.StringVar(value="3.0"),
            "duty_cycle": tk.StringVar(value="50"),
            "mean_val": tk.StringVar(value="8.0"),
            "amplitude": tk.StringVar(value="5.0"),
            "step_size": tk.StringVar(value="0.5"),
            "constant_val": tk.StringVar(value="12.0"),
            "noise_amplitude": tk.StringVar(value="1.5"),  # 新增：正弦噪声的噪声振幅
        }

        # 负荷参数
        self.load_type = tk.StringVar(value="正弦波")
        self.load_params = {
            "peak": tk.StringVar(value="60.0"),
            "valley": tk.StringVar(value="20.0"),
            "period": tk.StringVar(value="30"),
            "high_val": tk.StringVar(value="70.0"),
            "low_val": tk.StringVar(value="20.0"),
            "duty_cycle": tk.StringVar(value="50"),
            "mean_val": tk.StringVar(value="40.0"),
            "amplitude": tk.StringVar(value="15.0"),
            "step_size": tk.StringVar(value="2.0"),
            "constant_val": tk.StringVar(value="40.0"),
            "noise_amplitude": tk.StringVar(value="3.0"),  # 新增
        }

        self._build()

    def _build(self):
        main = tk.Frame(self.parent, bg=self.colors['bg'])
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 左栏：配置
        left = tk.Frame(main, bg=self.colors['bg'])
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        # 风速卡片
        wind_card = self._create_card(left, "🌬️ 风速曲线")
        wind_card.pack(fill=tk.X, pady=(0, 10))
        self.wind_param_frame = self._build_params(wind_card, self.wind_type, self.wind_params, "wind")

        # 负荷卡片
        load_card = self._create_card(left, "⚡ 负荷曲线")
        load_card.pack(fill=tk.X)
        self.load_param_frame = self._build_params(load_card, self.load_type, self.load_params, "load")

        # 按钮
        btn_frame = tk.Frame(left, bg=self.colors['bg'])
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        tk.Button(btn_frame, text="📊 预览", command=self._preview,
                  bg='#1f6feb', fg='white', font=('微软雅黑', 10), padx=15).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="✅ 应用全部", command=self._apply,
                  bg='#3fb950', fg='white', font=('微软雅黑', 10, 'bold'), padx=15).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="📂 导出", command=self._export,
                  bg=self.colors['border'], fg=self.colors['text'], font=('微软雅黑', 10), padx=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="📥 导入", command=self._import_curve,
                  bg=self.colors['border'], fg=self.colors['text'], font=('微软雅黑', 10), padx=10).pack(side=tk.LEFT, padx=5)
        self.status_label = tk.Label(btn_frame, text="", font=('微软雅黑', 9),
                                     fg=self.colors['text_dim'], bg=self.colors['bg'])
        self.status_label.pack(side=tk.RIGHT, padx=10)

        # 右栏：预览
        right = tk.Frame(main, bg=self.colors['bg'])
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        fig = Figure(figsize=(6, 5), dpi=100, facecolor=self.colors['bg'])
        self.wind_ax = fig.add_subplot(211)
        self.wind_ax.set_facecolor(self.colors['card'])
        self.wind_ax.set_title('风速预览', color=self.colors['text'], fontsize=10)
        self.wind_ax.set_ylabel('风速 (m/s)', color=self.colors['text_dim'], fontsize=9)
        self.wind_ax.tick_params(colors=self.colors['text_dim'])
        self.wind_ax.grid(True, alpha=0.2, color=self.colors['border'])
        self.wind_line, = self.wind_ax.plot([], [], color='#58a6ff', linewidth=2)

        self.load_ax = fig.add_subplot(212)
        self.load_ax.set_facecolor(self.colors['card'])
        self.load_ax.set_title('负荷预览', color=self.colors['text'], fontsize=10)
        self.load_ax.set_xlabel('时间 (秒)', color=self.colors['text_dim'], fontsize=9)
        self.load_ax.set_ylabel('负荷 (kW)', color=self.colors['text_dim'], fontsize=9)
        self.load_ax.tick_params(colors=self.colors['text_dim'])
        self.load_ax.grid(True, alpha=0.2, color=self.colors['border'])
        self.load_line, = self.load_ax.plot([], [], color='#f0883e', linewidth=2)

        fig.tight_layout()
        self.canvas = FigureCanvasTkAgg(fig, right)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self._preview()

    def _create_card(self, parent, title):
        frame = tk.Frame(parent, bg=self.colors['card'], relief=tk.RIDGE, bd=1)
        header = tk.Frame(frame, bg='#1f6feb', height=28)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text=title, font=('微软雅黑', 9, 'bold'), fg='white', bg='#1f6feb').pack(anchor='w', padx=10, pady=4)
        return frame

    def _build_params(self, parent, type_var, params, prefix):
        """构建参数面板"""
        row = tk.Frame(parent, bg=self.colors['card'])
        row.pack(fill=tk.X, pady=4, padx=5)
        tk.Label(row, text="波形类型：", font=('微软雅黑', 9), fg=self.colors['text'], bg=self.colors['card']).pack(side=tk.LEFT)
        combo = ttk.Combobox(row, textvariable=type_var,
                             values=["正弦波", "恒定值", "方波", "噪声波", "正弦噪声"],
                             state="readonly", width=12)
        combo.pack(side=tk.LEFT, padx=5)

        param_frame = tk.Frame(parent, bg=self.colors['card'])
        param_frame.pack(fill=tk.X, padx=5, pady=(0, 4))
        combo.bind("<<ComboboxSelected>>", lambda e: self._rebuild_params(param_frame, params, prefix))
        self._fill_params(param_frame, params, prefix)
        return param_frame

    def _fill_params(self, frame, params, prefix):
        """填充参数控件"""
        for widget in frame.winfo_children():
            widget.destroy()

        wave_type = self.wind_type.get() if prefix == "wind" else self.load_type.get()
        param_list = []

        if wave_type == "正弦波":
            param_list = [
                ("峰值", params["peak"], 15.0 if prefix == "wind" else 60.0),
                ("谷值", params["valley"], 1.0 if prefix == "wind" else 20.0),
                ("周期 (秒)", params["period"], 50 if prefix == "wind" else 30),
            ]
        elif wave_type == "恒定值":
            param_list = [
                ("值", params["constant_val"], 12.0 if prefix == "wind" else 40.0),
            ]
        elif wave_type == "方波":
            param_list = [
                ("高电平", params["high_val"], 15.0 if prefix == "wind" else 70.0),
                ("低电平", params["low_val"], 3.0 if prefix == "wind" else 20.0),
                ("周期 (秒)", params["period"], 30),
                ("占空比 (%)", params["duty_cycle"], 50),
            ]
        elif wave_type == "噪声波":
            param_list = [
                ("均值", params["mean_val"], 8.0 if prefix == "wind" else 40.0),
                ("振幅", params["amplitude"], 5.0 if prefix == "wind" else 15.0),
                ("步长", params["step_size"], 0.5 if prefix == "wind" else 2.0),
            ]
        elif wave_type == "正弦噪声":
            # 新增：正弦波 + 噪声
            param_list = [
                ("正弦峰值", params["peak"], 15.0 if prefix == "wind" else 60.0),
                ("正弦谷值", params["valley"], 1.0 if prefix == "wind" else 20.0),
                ("正弦周期 (秒)", params["period"], 50 if prefix == "wind" else 30),
                ("噪声振幅 (±)", params["noise_amplitude"], 1.5 if prefix == "wind" else 3.0),
            ]

        for label, var, default in param_list:
            row = tk.Frame(frame, bg=self.colors['card'])
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=label + "：", font=('微软雅黑', 9), fg=self.colors['text'], bg=self.colors['card'],
                     width=12, anchor='w').pack(side=tk.LEFT)
            var.set(str(default))
            entry = tk.Entry(row, textvariable=var, width=10, font=('Consolas', 9),
                             bg=self.colors['bg'], fg=self.colors['text'], insertbackground='white')
            entry.pack(side=tk.LEFT, padx=5)

    def _rebuild_params(self, frame, params, prefix):
        self._fill_params(frame, params, prefix)
        self._preview()

    def _get_params(self, prefix):
        """获取参数"""
        is_wind = prefix == "wind"
        wave_type = self.wind_type.get() if is_wind else self.load_type.get()
        params = self.wind_params if is_wind else self.load_params

        try:
            if wave_type == "正弦波":
                return {"type": "sine", "peak": float(params["peak"].get()),
                        "valley": float(params["valley"].get()), "period": float(params["period"].get())}
            elif wave_type == "恒定值":
                return {"type": "constant", "value": float(params["constant_val"].get())}
            elif wave_type == "方波":
                return {"type": "square", "high_val": float(params["high_val"].get()),
                        "low_val": float(params["low_val"].get()), "period": float(params["period"].get()),
                        "duty_cycle": float(params["duty_cycle"].get())}
            elif wave_type == "噪声波":
                return {"type": "noise", "mean_val": float(params["mean_val"].get()),
                        "amplitude": float(params["amplitude"].get()),
                        "step_size": float(params["step_size"].get())}
            elif wave_type == "正弦噪声":
                return {"type": "sine_noise",
                        "peak": float(params["peak"].get()),
                        "valley": float(params["valley"].get()),
                        "period": float(params["period"].get()),
                        "noise_amplitude": float(params["noise_amplitude"].get())}
        except ValueError:
            return None
        return None

    def _generate(self, params, duration=200, seed=None):
        if params is None:
            return []
        t = params.get("type")
        if t == "sine":
            return generate_sine_wave(params["peak"], params["valley"], params["period"], duration)
        elif t == "constant":
            return generate_constant(params["value"], duration)
        elif t == "square":
            return generate_square_wave(params["high_val"], params["low_val"], params["period"], params["duty_cycle"], duration)
        elif t == "noise":
            return generate_noise_wave(params["mean_val"], params["amplitude"], params.get("step_size", 0.5),
                                       seed or 42, duration)
        elif t == "sine_noise":
            return generate_sine_with_noise_wave(
                params["peak"], params["valley"], params["period"],
                params.get("noise_amplitude", 0),
                seed or 42, duration
            )
        return []

    def _preview(self):
        wind_params = self._get_params("wind")
        load_params = self._get_params("load")

        wind_data = self._generate(wind_params, duration=200, seed=42) if wind_params else []
        load_data = self._generate(load_params, duration=200, seed=123) if load_params else []
        times = list(range(200))

        if wind_data:
            self.wind_line.set_data(times, wind_data)
            self.wind_ax.set_xlim(0, 200)
            self.wind_ax.set_ylim(max(0, min(wind_data) - 1), max(wind_data) + 1)
        else:
            self.wind_line.set_data([], [])

        if load_data:
            self.load_line.set_data(times, load_data)
            self.load_ax.set_xlim(0, 200)
            self.load_ax.set_ylim(max(0, min(load_data) - 2), max(load_data) + 2)
        else:
            self.load_line.set_data([], [])

        self.canvas.draw_idle()
        self.status_label.config(text="✅ 预览成功", fg=self.colors['success'])

    def _apply(self):
        wind_params = self._get_params("wind")
        load_params = self._get_params("load")

        if wind_params is None:
            self.status_label.config(text="❌ 风速参数无效", fg=self.colors['danger'])
            return
        if load_params is None:
            self.status_label.config(text="❌ 负荷参数无效", fg=self.colors['danger'])
            return

        duration = 10000
        wind_data = self._generate(wind_params, duration=duration, seed=42)
        load_data = self._generate(load_params, duration=duration, seed=123)

        if len(wind_data) < 100 or len(load_data) < 100:
            self.status_label.config(text="❌ 生成失败", fg=self.colors['danger'])
            return

        try:
            conn = sqlite3.connect("grid.db")
            cursor = conn.cursor()
            cursor.execute("DELETE FROM env_curve")
            curves = [(i, wind_data[i], load_data[i]) for i in range(len(wind_data))]
            cursor.executemany("INSERT INTO env_curve (sim_time, wind_speed, load_power) VALUES (?, ?, ?)", curves)
            conn.commit()
            conn.close()
            self.status_label.config(text=f"✅ 已应用 {len(wind_data)} 个点", fg=self.colors['success'])
            self.log(f"曲线已更新: 风速={self.wind_type.get()}, 负荷={self.load_type.get()}")
        except Exception as e:
            self.status_label.config(text=f"❌ 应用失败: {e}", fg=self.colors['danger'])
            self.log(f"曲线应用失败: {e}", "ERROR")

    def _export(self):
        try:
            conn = sqlite3.connect("grid.db")
            cursor = conn.cursor()
            cursor.execute("SELECT sim_time, wind_speed, load_power FROM env_curve ORDER BY sim_time")
            rows = cursor.fetchall()
            conn.close()
            if not rows:
                self.status_label.config(text="❌ 没有数据", fg=self.colors['danger'])
                return
            filepath = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV文件", "*.csv")])
            if filepath:
                with open(filepath, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(["sim_time", "wind_speed", "load_power"])
                    writer.writerows(rows)
                self.status_label.config(text=f"✅ 导出 {len(rows)} 条", fg=self.colors['success'])
        except Exception as e:
            self.status_label.config(text=f"❌ 导出失败: {e}", fg=self.colors['danger'])

    def _import_curve(self):
        filepath = filedialog.askopenfilename(filetypes=[("CSV文件", "*.csv")])
        if not filepath:
            return
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                header = next(reader)
                rows = [(int(float(row[0])), float(row[1]), float(row[2]) if len(row) > 2 else 40.0)
                        for row in reader if len(row) >= 2]
            if len(rows) < 10:
                self.status_label.config(text="❌ 数据点太少", fg=self.colors['danger'])
                return
            conn = sqlite3.connect("grid.db")
            cursor = conn.cursor()
            cursor.execute("DELETE FROM env_curve")
            cursor.executemany("INSERT INTO env_curve (sim_time, wind_speed, load_power) VALUES (?, ?, ?)", rows)
            conn.commit()
            conn.close()
            self.status_label.config(text=f"✅ 导入 {len(rows)} 条", fg=self.colors['success'])
            self.log(f"曲线已从CSV导入: {filepath}")
        except Exception as e:
            self.status_label.config(text=f"❌ 导入失败: {e}", fg=self.colors['danger'])