"""
主窗口：标题栏（含时间标签）+ 左栏（控制/通信/实时数据）+ 右栏（Notebook）
"""
import tkinter as tk
from tkinter import ttk

from .control_panel import ControlPanel
from .comm_status import CommStatus
from .realtime_data import RealtimeData
from .realtime_curve import RealtimeCurveTab
from .history_curve import HistoryCurveTab
from .curve_config import CurveConfigTab
from .scada_table import ScadaTableTab
from .log_view import LogViewTab


class MainWindow:
    """主窗口"""

    def __init__(self, root, db, engine, tcp_server, log_callback=None):
        self.root = root
        self.db = db
        self.engine = engine
        self.tcp_server = tcp_server
        self.log = log_callback or print

        self.colors = {
            'bg': '#0d1117', 'card': '#161b22', 'accent': '#1f6feb',
            'success': '#3fb950', 'warning': '#d29922', 'danger': '#f85149',
            'text': '#f0f6fc', 'text_dim': '#8b949e', 'border': '#30363d'
        }

        self._build_ui()

    def _build_ui(self):
        self.root.configure(bg=self.colors['bg'])
        main_panel = tk.Frame(self.root, bg=self.colors['bg'])
        main_panel.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # ---------- 标题栏（含时间标签） ----------
        title_frame = tk.Frame(main_panel, bg=self.colors['bg'], height=55)
        title_frame.pack(fill=tk.X, pady=(0, 8))
        title_frame.pack_propagate(False)

        # 标题 - 靠左
        tk.Label(title_frame, text="❄️ 南极考察站微电网智能调控系统",
                 font=('微软雅黑', 18, 'bold'), fg=self.colors['text'], bg=self.colors['bg']).pack(side=tk.LEFT)

        # 状态标签 - 靠右（在时间标签左边）
        self.status_label = tk.Label(title_frame, text="● 系统就绪", font=('微软雅黑', 11),
                                     fg=self.colors['success'], bg=self.colors['bg'])
        self.status_label.pack(side=tk.RIGHT, padx=10)

        # ===== 时间标签 - 靠右，最外层，白色字体 =====
        self.time_label = tk.Label(title_frame, text="⏱ t=0s", font=('微软雅黑', 12, 'bold'),
                                   fg='#ffffff', bg=self.colors['bg'])
        self.time_label.pack(side=tk.RIGHT, padx=15)

        # ---------- 主体 ----------
        body = tk.Frame(main_panel, bg=self.colors['bg'])
        body.pack(fill=tk.BOTH, expand=True)

        # 左栏
        left = tk.Frame(body, bg=self.colors['bg'], width=400)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        left.pack_propagate(False)

        self.control_panel = ControlPanel(left, self.db, self.engine, self.log)
        self.comm_status = CommStatus(left, self.db, self.log)
        self.realtime_data = RealtimeData(left, self.db, self.log)

        # 右栏：Notebook
        right = tk.Frame(body, bg=self.colors['bg'])
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TNotebook', background=self.colors['bg'], borderwidth=0)
        style.configure('TNotebook.Tab', background=self.colors['card'], foreground=self.colors['text'],
                        padding=[10, 5])
        style.map('TNotebook.Tab', background=[('selected', self.colors['accent'])])

        # 各Tab
        self.realtime_curve = RealtimeCurveTab(self.notebook, self.db, self.colors)
        self.history_curve = HistoryCurveTab(self.notebook, self.db, self.colors)
        self.curve_config = CurveConfigTab(self.notebook, self.db, self.colors, self.log)
        self.scada_table = ScadaTableTab(self.notebook, self.db, self.colors)
        self.log_view = LogViewTab(self.notebook, self.log)

        # 注意：self.time_label 已经在标题栏定义了，不要再覆盖
        # self.status_label 也已经在标题栏定义了

    def log(self, msg, level="INFO"):
        """日志转发"""
        self.log_view.log(msg, level)

    def update_display(self):
        """刷新显示（由主循环调用）"""
        self.realtime_data.update()
        self.comm_status.update()
        self.realtime_curve.update()
        self.scada_table.refresh()

        # 继续调度
        if hasattr(self.root, 'running') and self.root.running:
            self.root.after(500, self.update_display)