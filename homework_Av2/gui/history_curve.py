"""
历史曲线Tab：查询历史数据，2x2网格显示
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


class HistoryCurveTab:
    """历史曲线Tab"""

    def __init__(self, notebook, db, colors):
        self.db = db
        self.colors = colors

        parent = tk.Frame(notebook, bg=colors['bg'])
        notebook.add(parent, text="📉 历史曲线")
        self.parent = parent

        self.configs = [
            {'key': 'wind_speed', 'title': '风速', 'color': '#58a6ff'},
            {'key': 'load_power', 'title': '负荷', 'color': '#f0883e'},
            {'key': 'wtg_power', 'title': '风机出力', 'color': '#3fb950'},
            {'key': 'diesel_power', 'title': '柴发出力', 'color': '#f85149'}
        ]

        self._build()

    def _build(self):
        # 控制栏
        control = tk.Frame(self.parent, bg=self.colors['bg'])
        control.pack(fill=tk.X, pady=5, padx=5)

        tk.Label(control, text="起始:", font=('微软雅黑', 9), fg=self.colors['text_dim'], bg=self.colors['bg']).pack(
            side=tk.LEFT)
        self.start_entry = tk.Entry(control, width=6, font=('Consolas', 9), bg=self.colors['card'],
                                    fg=self.colors['text'])
        self.start_entry.insert(0, "0")
        self.start_entry.pack(side=tk.LEFT, padx=2)

        tk.Label(control, text="结束:", font=('微软雅黑', 9), fg=self.colors['text_dim'], bg=self.colors['bg']).pack(
            side=tk.LEFT, padx=(10, 0))
        self.end_entry = tk.Entry(control, width=6, font=('Consolas', 9), bg=self.colors['card'],
                                  fg=self.colors['text'])
        self.end_entry.insert(0, "100")
        self.end_entry.pack(side=tk.LEFT, padx=2)

        self.check_vars = {}
        for cfg in self.configs:
            var = tk.BooleanVar(value=True)
            self.check_vars[cfg['key']] = var
            tk.Checkbutton(control, text=cfg['title'], variable=var,
                           bg=self.colors['bg'], fg=self.colors['text'],
                           selectcolor=self.colors['card'], font=('微软雅黑', 9)).pack(side=tk.LEFT, padx=5)

        tk.Button(control, text="🔍 查询", command=self._load,
                  bg='#1f6feb', fg='white', font=('微软雅黑', 9), padx=10).pack(side=tk.RIGHT, padx=5)
        tk.Button(control, text="📥 导出", command=self._export,
                  bg=self.colors['border'], fg=self.colors['text'], font=('微软雅黑', 9), padx=10).pack(side=tk.RIGHT,
                                                                                                        padx=5)

        # 曲线图
        fig = Figure(figsize=(10, 7), dpi=100, facecolor=self.colors['bg'])
        self.axes = []
        self.lines = {}

        for i, cfg in enumerate(self.configs):
            ax = fig.add_subplot(2, 2, i + 1)
            ax.set_facecolor(self.colors['card'])
            ax.set_title(cfg['title'], color=self.colors['text'], fontsize=11)
            ax.set_ylabel('值', color=self.colors['text_dim'], fontsize=10)
            ax.set_xlabel('仿真时刻 (s)', color=self.colors['text_dim'], fontsize=9)
            ax.tick_params(colors=self.colors['text_dim'])
            ax.grid(True, alpha=0.2, color=self.colors['border'])
            line, = ax.plot([], [], color=cfg['color'], linewidth=2)
            self.lines[cfg['key']] = line
            self.axes.append(ax)

        fig.tight_layout(pad=2.0)
        self.canvas = FigureCanvasTkAgg(fig, self.parent)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self._clear()

    def _clear(self):
        for ax in self.axes:
            ax.clear()
            ax.set_facecolor(self.colors['card'])
            ax.tick_params(colors=self.colors['text_dim'])
            ax.grid(True, alpha=0.2, color=self.colors['border'])
        for i, cfg in enumerate(self.configs):
            self.axes[i].set_title(cfg['title'], color=self.colors['text'], fontsize=11)
            self.axes[i].set_ylabel('值', color=self.colors['text_dim'], fontsize=10)
            self.axes[i].set_xlabel('仿真时刻 (s)', color=self.colors['text_dim'], fontsize=9)
        self.canvas.draw()

    def _load(self):
        try:
            start = int(self.start_entry.get() or 0)
            end = int(self.end_entry.get() or 100)
        except ValueError:
            return

        conn = sqlite3.connect("grid.db")
        cursor = conn.cursor()
        cursor.execute('''SELECT sim_time, wind_speed, load_power, wtg_power, diesel_power
                          FROM history
                          WHERE sim_time BETWEEN ? AND ?
                          ORDER BY sim_time''', (start, end))
        rows = cursor.fetchall()
        conn.close()

        self._clear()
        if not rows:
            return

        times = [r[0] for r in rows]
        data = {
            'wind_speed': [r[1] for r in rows],
            'load_power': [r[2] for r in rows],
            'wtg_power': [r[3] for r in rows],
            'diesel_power': [r[4] for r in rows]
        }

        for i, cfg in enumerate(self.configs):
            key = cfg['key']
            ax = self.axes[i]
            if self.check_vars.get(key, tk.BooleanVar(value=True)).get():
                ax.plot(times, data[key], color=cfg['color'], linewidth=2)
            ax.set_xlim(start, end)

        self.canvas.draw()

    def _export(self):
        try:
            start = int(self.start_entry.get() or 0)
            end = int(self.end_entry.get() or 100)
        except ValueError:
            return

        filepath = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV文件", "*.csv")])
        if not filepath:
            return

        conn = sqlite3.connect("grid.db")
        cursor = conn.cursor()
        cursor.execute('''SELECT sim_time, wind_speed, load_power, wtg_power, diesel_power, pitch_angle_set, log_msg
                          FROM history
                          WHERE sim_time BETWEEN ? AND ?
                          ORDER BY sim_time''', (start, end))
        rows = cursor.fetchall()
        conn.close()

        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(
                ['sim_time', 'wind_speed', 'load_power', 'wtg_power', 'diesel_power', 'pitch_angle', 'log_msg'])
            writer.writerows(rows)

        messagebox.showinfo("导出成功", f"已导出 {len(rows)} 条数据")