"""
实时数据显示：风速、负荷、风机出力、柴发出力、风机状态、桨距角
"""
import tkinter as tk


class RealtimeData:
    """实时数据显示"""

    def __init__(self, parent, db, log_callback=None):
        self.parent = parent
        self.db = db
        self.log = log_callback or print

        self.colors = {
            'bg': '#0d1117', 'card': '#161b22',
            'text': '#f0f6fc', 'text_dim': '#8b949e'
        }

        self._build()

    def _build(self):
        card = self._create_card(self.parent, "📊 实时数据")
        card.pack(fill=tk.BOTH, expand=True)

        grid = tk.Frame(card, bg=self.colors['card'])
        grid.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        items = [
            ("🌬 风速", "wind_speed", "m/s"),
            ("⚡ 负荷", "load_power", "kW"),
            ("💨 风机出力", "wtg_power", "kW"),
            ("🛢️ 柴发出力", "diesel_power", "kW"),
            ("🔄 风机状态", "wtg_status", ""),
            ("🔧 桨距角", "pitch_angle", "°"),
        ]

        self.vars = {}
        for i, (label, key, unit) in enumerate(items):
            row, col = i // 2, i % 2
            f = tk.Frame(grid, bg=self.colors['card'], relief=tk.RIDGE, bd=1)
            f.grid(row=row, column=col, sticky="nsew", padx=2, pady=2)

            tk.Label(f, text=label, font=('微软雅黑', 9), fg=self.colors['text_dim'], bg=self.colors['card']).pack(
                anchor='w', padx=5)
            var = tk.StringVar(value="--")
            self.vars[key] = var
            tk.Label(f, textvariable=var, font=('Consolas', 14, 'bold'), fg=self.colors['text'],
                     bg=self.colors['card']).pack(anchor='w', padx=5)
            if unit:
                tk.Label(f, text=unit, font=('微软雅黑', 9), fg=self.colors['text_dim'], bg=self.colors['card']).place(
                    relx=0.82, rely=0.5)

        for r in range(3):
            grid.grid_rowconfigure(r, weight=1)
        for c in range(2):
            grid.grid_columnconfigure(c, weight=1)

    def _create_card(self, parent, title):
        frame = tk.Frame(parent, bg=self.colors['card'], relief=tk.RIDGE, bd=1)
        header = tk.Frame(frame, bg='#1f6feb', height=28)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text=title, font=('微软雅黑', 9, 'bold'), fg='white', bg='#1f6feb').pack(anchor='w', padx=10,
                                                                                                  pady=4)
        return frame

    def update(self):
        """刷新数据显示（由主循环调用）"""
        yc = self.db.get_scada_yc()
        yx = self.db.get_scada_yx()
        yt = self.db.get_scada_yt()

        if yc:
            self.vars["wind_speed"].set(f"{yc.get('wind_speed', 0):.1f}")
            self.vars["load_power"].set(f"{yc.get('load_power', 0):.1f}")
            self.vars["wtg_power"].set(f"{yc.get('wtg_power', 0):.1f}")
            self.vars["diesel_power"].set(f"{yc.get('diesel_power', 0):.1f}")

        if yx:
            status = "▶ 运行中" if yx.get('wtg_status') else "⏹ 已停机"
            self.vars["wtg_status"].set(status)

        if yt:
            self.vars["pitch_angle"].set(f"{yt.get('pitch_angle_set', 0):.1f}")