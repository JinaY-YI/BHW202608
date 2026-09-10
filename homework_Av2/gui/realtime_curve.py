"""
实时曲线Tab：2x2网格，显示风速、负荷、风机出力、柴发出力
支持自适应Y轴：平时保持默认范围，数据超出时自动扩展，数据回归后恢复
"""
import tkinter as tk
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


class RealtimeCurveTab:
    """实时曲线Tab（自适应Y轴）"""

    def __init__(self, notebook, db, colors):
        self.db = db
        self.colors = colors
        self.data = {
            'wind_speed': {'times': [], 'values': []},
            'load_power': {'times': [], 'values': []},
            'wtg_power': {'times': [], 'values': []},
            'diesel_power': {'times': [], 'values': []}
        }
        self.max_points = 60

        # 每个子图的默认Y轴范围（数据在范围内时使用）
        configs = [
            {'key': 'wind_speed', 'title': '风速', 'unit': 'm/s', 'color': '#58a6ff', 'default_ylim': (0, 20)},
            {'key': 'load_power', 'title': '负荷', 'unit': 'kW', 'color': '#f0883e', 'default_ylim': (0, 80)},
            {'key': 'wtg_power', 'title': '风机出力', 'unit': 'kW', 'color': '#3fb950', 'default_ylim': (0, 60)},
            {'key': 'diesel_power', 'title': '柴发出力', 'unit': 'kW', 'color': '#f85149', 'default_ylim': (0, 100)}
        ]
        self.configs = configs

        parent = tk.Frame(notebook, bg=colors['bg'])
        notebook.add(parent, text="📈 实时曲线")
        self.parent = parent

        self._build()

    def _build(self):
        fig = Figure(figsize=(10, 7), dpi=100, facecolor=self.colors['bg'])
        self.axes = []
        self.lines = {}

        for i, cfg in enumerate(self.configs):
            ax = fig.add_subplot(2, 2, i + 1)
            ax.set_facecolor(self.colors['card'])
            ax.set_title(cfg['title'], color=self.colors['text'], fontsize=11)
            ax.set_ylabel(cfg['unit'], color=self.colors['text_dim'], fontsize=10)
            ax.set_xlabel('时间 (秒)', color=self.colors['text_dim'], fontsize=9)
            ax.tick_params(colors=self.colors['text_dim'])
            ax.grid(True, alpha=0.2, color=self.colors['border'])
            ax.set_xlim(0, 60)
            ax.set_ylim(cfg['default_ylim'][0], cfg['default_ylim'][1])

            line, = ax.plot([], [], color=cfg['color'], linewidth=2)
            self.lines[cfg['key']] = line
            self.axes.append(ax)

        fig.tight_layout(pad=2.0)
        self.canvas = FigureCanvasTkAgg(fig, self.parent)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def update(self):
        """更新曲线（自适应Y轴）"""
        try:
            yc = self.db.get_scada_yc()
            if not yc:
                return

            current_time = yc.get('sim_time', 0)

            # ===== 1. 更新数据缓存（含回跳检测） =====
            for key in ['wind_speed', 'load_power', 'wtg_power', 'diesel_power']:
                data = self.data[key]
                # 检测回跳（仿真循环回起点），清空缓存
                if data['times'] and current_time < data['times'][-1]:
                    data['times'].clear()
                    data['values'].clear()

                if data['times'] and data['times'][-1] == current_time:
                    continue

                data['times'].append(current_time)
                data['values'].append(yc.get(key, 0))
                if len(data['times']) > self.max_points:
                    data['times'].pop(0)
                    data['values'].pop(0)

            # ===== 2. 更新曲线 & 自适应Y轴 =====
            for i, cfg in enumerate(self.configs):
                key = cfg['key']
                ax = self.axes[i]
                line = self.lines[key]
                data = self.data[key]
                line.set_data(data['times'], data['values'])

                # 更新X轴
                if data['times']:
                    x_min = max(0, data['times'][-1] - self.max_points)
                    x_max = max(self.max_points, data['times'][-1] + 5)
                    ax.set_xlim(x_min, x_max)

                # ===== 自适应Y轴 =====
                if data['values']:
                    data_min = min(data['values'])
                    data_max = max(data['values'])
                    default_min, default_max = cfg['default_ylim']

                    # 判断是否需要扩展
                    need_expand = (data_max > default_max) or (data_min < default_min)

                    if need_expand:
                        # 计算新的Y轴范围（带10%边距，并保持默认最小值0或数据最小值）
                        margin = (data_max - data_min) * 0.1 + 0.5
                        new_min = min(default_min, data_min - margin)
                        new_max = max(default_max, data_max + margin)
                        ax.set_ylim(new_min, new_max)
                    else:
                        # 数据在默认范围内，恢复默认刻度
                        ax.set_ylim(default_min, default_max)

            self.canvas.draw_idle()
        except Exception:
            pass