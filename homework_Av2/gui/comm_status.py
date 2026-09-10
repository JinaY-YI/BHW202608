"""
通信状态显示：EMS + 风机控制器在线/离线状态
"""
import tkinter as tk


class CommStatus:
    """通信状态显示"""

    def __init__(self, parent, db, log_callback=None):
        self.parent = parent
        self.db = db
        self.log = log_callback or print

        self.colors = {
            'bg': '#0d1117', 'card': '#161b22', 'accent': '#1f6feb',
            'success': '#3fb950', 'danger': '#f85149',
            'text': '#f0f6fc', 'text_dim': '#8b949e'
        }

        self._build()

    def _build(self):
        card = self._create_card(self.parent, "📡 通信状态")
        card.pack(fill=tk.X, pady=(0, 8))

        row = tk.Frame(card, bg=self.colors['card'])
        row.pack(fill=tk.X, pady=5, padx=5)

        self.ems_label = tk.Label(row, text="EMS: ● 离线", font=('微软雅黑', 10),
                                  fg=self.colors['danger'], bg=self.colors['card'])
        self.ems_label.pack(side=tk.LEFT, padx=10)

        self.wind_label = tk.Label(row, text="风机控制器: ● 离线", font=('微软雅黑', 10),
                                   fg=self.colors['danger'], bg=self.colors['card'])
        self.wind_label.pack(side=tk.LEFT, padx=10)

    def _create_card(self, parent, title):
        frame = tk.Frame(parent, bg=self.colors['card'], relief=tk.RIDGE, bd=1)
        header = tk.Frame(frame, bg=self.colors['accent'], height=28)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text=title, font=('微软雅黑', 9, 'bold'), fg='white', bg=self.colors['accent']).pack(
            anchor='w', padx=10, pady=4)
        return frame

    def update(self):
        """刷新通信状态（由主循环调用）"""
        # 实际状态从外部获取（由 MainWindow 传入）
        # 这里只做占位，实际由外部更新
        pass

    def set_status(self, ems_online, wind_online):
        """设置通信状态"""
        self.ems_label.config(
            text=f"EMS: ● {'在线' if ems_online else '离线'}",
            fg=self.colors['success'] if ems_online else self.colors['danger']
        )
        self.wind_label.config(
            text=f"风机控制器: ● {'在线' if wind_online else '离线'}",
            fg=self.colors['success'] if wind_online else self.colors['danger']
        )