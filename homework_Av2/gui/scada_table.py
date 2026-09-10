"""
SCADA数据Tab：遥测/遥信/遥调/遥控/参数 分组显示，自动刷新
"""
import tkinter as tk
from tkinter import ttk


class ScadaTableTab:
    """SCADA数据Tab"""

    def __init__(self, notebook, db, colors):
        self.db = db
        self.colors = colors

        parent = tk.Frame(notebook, bg=colors['bg'])
        notebook.add(parent, text="📋 SCADA数据")
        self.parent = parent

        self._build()
        self._refresh()

    def _build(self):
        toolbar = tk.Frame(self.parent, bg=self.colors['bg'])
        toolbar.pack(fill=tk.X, pady=5, padx=5)

        tk.Label(toolbar, text="💡 数据自动刷新", font=('微软雅黑', 9),
                 fg=self.colors['text_dim'], bg=self.colors['bg']).pack(side=tk.LEFT)
        tk.Button(toolbar, text="🔄 手动刷新", command=self._refresh,
                  bg='#1f6feb', fg='white', font=('微软雅黑', 9), padx=10).pack(side=tk.RIGHT)

        self.tree = ttk.Treeview(self.parent, columns=('type', 'key', 'value', 'unit'), show='tree headings', height=12)
        self.tree.heading('#0', text='')
        self.tree.heading('type', text='类型')
        self.tree.heading('key', text='字段名')
        self.tree.heading('value', text='当前值')
        self.tree.heading('unit', text='单位')
        self.tree.column('#0', width=30)
        self.tree.column('type', width=80)
        self.tree.column('key', width=140)
        self.tree.column('value', width=120)
        self.tree.column('unit', width=80)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.tree.tag_configure('telemetry', foreground='#58a6ff')
        self.tree.tag_configure('status', foreground='#3fb950')
        self.tree.tag_configure('setpoint', foreground='#f0883e')
        self.tree.tag_configure('remote', foreground='#f85149')   # 遥控红色
        self.tree.tag_configure('param', foreground='#8b949e')

    def refresh(self):
        """刷新数据（外部调用）"""
        self._refresh()

    def _refresh(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        yc = self.db.get_scada_yc()
        yx = self.db.get_scada_yx()
        yt = self.db.get_scada_yt()
        params = self.db.get_device_params()

        # 遥测（蓝色）
        if yc:
            for k, v in yc.items():
                unit = 'kW' if k in ['load_power', 'wtg_power', 'diesel_power'] else ('m/s' if k == 'wind_speed' else 's')
                self.tree.insert('', 'end', text='', values=('遥测', k, f"{v:.2f}", unit), tags=('telemetry',))

        # 遥信（绿色）
        if yx:
            for k, v in yx.items():
                status = '运行' if v else '停机'
                self.tree.insert('', 'end', text='', values=('遥信', k, status, ''), tags=('status',))

        # 遥调（橙色）
        if yt:
            for k, v in yt.items():
                unit = 'kW' if 'power' in k else '°'
                self.tree.insert('', 'end', text='', values=('遥调', k, f"{v:.2f}", unit), tags=('setpoint',))

        # ===== 遥控（红色）- 有数据显示数据，无数据显示占位 =====
        remote_cmds = self.db.get_remote_commands(limit=10)
        if remote_cmds:
            for cmd in remote_cmds:
                value_text = '启动' if cmd['value'] else '停机'
                status_text = cmd['status']
                source = cmd.get('source', 'unknown')
                display_value = f"{value_text} ({status_text})"
                self.tree.insert('', 'end', text='',
                                 values=('遥控', cmd['target'], display_value, source),
                                 tags=('remote',))   # 红色
        else:
            # 没有遥控数据时显示占位提示（也用红色，和参数区分）
            self.tree.insert('', 'end', text='',
                             values=('遥控', '无遥控指令', '—', '—'),
                             tags=('remote',))   # 红色，不是灰色

        # 参数（灰色）
        if params:
            for k, v in params.items():
                unit = 'm/s' if 'wind' in k else 'kW'
                self.tree.insert('', 'end', text='', values=('参数', k, f"{v:.2f}", unit), tags=('param',))