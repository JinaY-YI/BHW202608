"""EMS 主站人机界面（tkinter，无第三方依赖，浅色简洁风格）。

页面：连接控制 + 四个页签（实时监控 / 历史曲线 / 调度配置 / 运行日志）。
曲线：普通折线 + 自动量程，可通过"显示范围"下拉切换回看长度。
"""
from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, List, Optional

from . import evaluation, strategy
from .database import Database
from .mock_grid import MockGrid
from .operator_core import OperatorCore
from .operator_io import OperatorIO

_REFRESH_MS = 1000
_FONT = "微软雅黑"
_MONO = "Consolas"

# 曲线序列颜色（浅色背景上清晰可见）
SERIES_COLORS = {
    "风速": "#1f77b4",
    "负荷": "#ff7f0e",
    "风机出力": "#2ca02c",
    "柴发出力": "#d62728",
}
SERIES_ORDER = ["风速", "负荷", "风机出力", "柴发出力"]


def _fmt(value, digits: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


class CurveCanvas(tk.Canvas):
    """简单可靠的折线图：自动量程，显示最近 N 个点。"""

    def __init__(self, master, height: int = 360, **kw):
        super().__init__(master, bg="white", highlightthickness=1,
                         highlightbackground="#d0d0d0", height=height, **kw)
        self.series: Dict[str, List[Optional[float]]] = {}
        self.max_points: int = 120
        self.bind("<Configure>", lambda e: self.redraw())

    def set_series(self, data: Dict[str, List[Optional[float]]]) -> None:
        self.series = {k: list(v) for k, v in data.items() if k in SERIES_ORDER}
        self.redraw()

    def set_max_points(self, n: int) -> None:
        self.max_points = int(n)
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        w = max(self.winfo_width(), 60)
        h = max(self.winfo_height(), 60)
        pl, pr, pt, pb = 48, 12, 14, 26
        pw, ph = w - pl - pr, h - pt - pb

        # 取每个序列最近 max_points 个点
        trimmed: Dict[str, List[Optional[float]]] = {}
        for k, vals in self.series.items():
            if vals:
                trimmed[k] = vals[-self.max_points:]
        if not trimmed:
            self.create_text(w / 2, h / 2, text="暂无数据", fill="#888888",
                             font=(_FONT, 12))
            return

        all_vals = [v for vals in trimmed.values() for v in vals if v is not None]
        if not all_vals:
            self.create_text(w / 2, h / 2, text="暂无数据", fill="#888888",
                             font=(_FONT, 12))
            return
        vmin, vmax = min(all_vals), max(all_vals)
        if vmax - vmin < 1e-9:
            vmax = vmin + 1.0
        # 上下留 8% 边距，避免曲线顶到边框
        margin = (vmax - vmin) * 0.08
        vmin, vmax = vmin - margin, vmax + margin
        span = vmax - vmin

        n = max(len(vals) for vals in trimmed.values())

        def px(i: int) -> float:
            return pl + i * pw / max(n - 1, 1)

        def py(v: float) -> float:
            return pt + (vmax - v) * ph / span

        # 网格 + 纵轴刻度
        for g in range(5):
            v = vmin + span * g / 4
            y = py(v)
            self.create_line(pl, y, w - pr, y, fill="#eeeeee")
            self.create_text(pl - 8, y, anchor="e", text=f"{v:.0f}",
                             fill="#888888", font=(_MONO, 8))

        # 图例
        lx = pl
        for name in SERIES_ORDER:
            if name not in trimmed:
                continue
            color = SERIES_COLORS[name]
            self.create_rectangle(lx, 3, lx + 10, 9, fill=color, outline="")
            self.create_text(lx + 14, 6, anchor="w", text=name, fill="#333333",
                             font=(_FONT, 8))
            lx += 16 + 7 * len(name) + 18

        # 折线
        for name in SERIES_ORDER:
            if name not in trimmed:
                continue
            vals = trimmed[name]
            pts = [(px(i), py(v)) for i, v in enumerate(vals) if v is not None]
            if len(pts) >= 2:
                flat = [c for p in pts for c in p]
                self.create_line(*flat, fill=SERIES_COLORS[name], width=2)
            elif pts:
                x, y = pts[0]
                self.create_oval(x - 3, y - 3, x + 3, y + 3,
                                 fill=SERIES_COLORS[name], outline="")


class EMSApp(tk.Tk):
    """EMS 主站主窗口。"""

    def __init__(self, db: Database, auto_start: bool = True):
        super().__init__()
        self.db = db
        self.io: Optional[OperatorIO] = None
        self.core: Optional[OperatorCore] = None
        self.grid: Optional[MockGrid] = None

        self.title("EMS 主站 - 南极考察站微电网智能调控系统")
        self.geometry("1060x760")
        self.minsize(900, 620)
        self.configure(bg="#f0f0f0")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        if auto_start:
            self.after(300, self._on_start)
        self.after(_REFRESH_MS, self._refresh)
        self.after(1000, self._refresh_evaluation)   # 1s 后首次打分，之后每 20s 一次

    # ------------------------------------------------------------------
    def _build(self) -> None:
        self._build_connection_bar()

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._build_monitor(nb)
        self._build_history(nb)
        self._build_config(nb)
        self._build_evaluation(nb)
        self._build_log(nb)

    def _build_connection_bar(self) -> None:
        bar = ttk.LabelFrame(self, text="连接控制")
        bar.pack(fill="x", padx=8, pady=(8, 4))

        self.var_conn_mode = tk.StringVar(value="mock")
        ttk.Radiobutton(bar, text="内置仿真桩", variable=self.var_conn_mode,
                        value="mock").grid(row=0, column=0, padx=4, pady=4)
        ttk.Radiobutton(bar, text="电网模拟器", variable=self.var_conn_mode,
                        value="grid").grid(row=0, column=1, padx=4, pady=4)

        ttk.Label(bar, text="地址：").grid(row=0, column=2, padx=(12, 2))
        self.e_host = ttk.Entry(bar, width=14)
        self.e_host.insert(0, "127.0.0.1")
        self.e_host.grid(row=0, column=3, padx=2)
        ttk.Label(bar, text="端口：").grid(row=0, column=4, padx=(6, 2))
        self.e_port = ttk.Entry(bar, width=7)
        self.e_port.insert(0, "8888")
        self.e_port.grid(row=0, column=5, padx=2)

        ttk.Button(bar, text="▶ 启动", command=self._on_start).grid(
            row=0, column=6, padx=8, pady=4)
        ttk.Button(bar, text="■ 停止", command=self._on_stop).grid(
            row=0, column=7, padx=4, pady=4)

        self.var_target = tk.StringVar(value="未启动")
        self.var_running = tk.StringVar(value="已停止")
        ttk.Label(bar, textvariable=self.var_target, foreground="#1f77b4").grid(
            row=0, column=8, padx=(12, 2))
        ttk.Label(bar, textvariable=self.var_running, foreground="#2ca02c").grid(
            row=0, column=9, padx=2)

    def _build_monitor(self, nb) -> None:
        f = ttk.Frame(nb)
        nb.add(f, text="实时监控")

        left = ttk.Frame(f)
        left.pack(side="left", fill="y", padx=(6, 4), pady=6)

        status = ttk.LabelFrame(left, text="通信状态")
        status.pack(fill="x", pady=(0, 6))
        self.var_conn = tk.StringVar(value="-")
        self.var_last_rx = tk.StringVar(value="-")
        self.var_last_cmd = tk.StringVar(value="-")
        for r, (label, var) in enumerate([
            ("与电网模拟器连接：", self.var_conn),
            ("最近数据更新：", self.var_last_rx),
            ("最近命令回执：", self.var_last_cmd),
        ]):
            ttk.Label(status, text=label).grid(row=r, column=0, sticky="w", padx=6, pady=2)
            ttk.Label(status, textvariable=var).grid(row=r, column=1, sticky="w", padx=6)

        scada = ttk.LabelFrame(left, text="SCADA 遥测/遥信")
        scada.pack(fill="x", pady=(0, 6))
        self.scada_vars = {}
        for r, (key, label) in enumerate([
            ("wind_speed", "风速 (m/s)"), ("load_power", "负荷功率 (kW)"),
            ("wtg_power", "风机出力 (kW)"), ("diesel_power", "柴发出力 (kW)"),
            ("wtg_status", "风机启停"), ("diesel_status", "柴发启停"),
        ]):
            self.scada_vars[key] = tk.StringVar(value="-")
            ttk.Label(scada, text=label + "：").grid(row=r, column=0, sticky="w", padx=6, pady=1)
            ttk.Label(scada, textvariable=self.scada_vars[key]).grid(row=r, column=1, sticky="w", padx=6)

        eval_frame = ttk.LabelFrame(left, text="调度结果评价")
        eval_frame.pack(fill="x")
        self.eval_vars = {}
        for r, (key, label) in enumerate([
            ("balance_error", "功率平衡误差 (kW)"),
            ("renewable_share", "新能源占比"),
            ("wind_utilization", "风机利用率"),
            ("diesel_reserve_ratio", "柴发备用率"),
        ]):
            self.eval_vars[key] = tk.StringVar(value="-")
            ttk.Label(eval_frame, text=label + "：").grid(row=r, column=0, sticky="w", padx=6, pady=1)
            ttk.Label(eval_frame, textvariable=self.eval_vars[key]).grid(row=r, column=1, sticky="w", padx=6)

        right = ttk.Frame(f)
        right.pack(side="left", fill="both", expand=True, padx=(4, 6), pady=6)

        sets = ttk.LabelFrame(right, text="EMS 调度输出（遥调设定）")
        sets.pack(fill="x", pady=(0, 6))
        self.set_vars = {}
        for col, (key, label) in enumerate([
            ("wtg_power_set", "风机目标功率 (kW)"),
            ("diesel_power_set", "柴发目标功率 (kW)"),
            ("available_wind", "可用风功率 (kW)"),
            ("diesel_reserve", "柴发备用 (kW)"),
        ]):
            self.set_vars[key] = tk.StringVar(value="-")
            ttk.Label(sets, text=label + "：").grid(row=0, column=col * 2, sticky="w", padx=(2, 2))
            ttk.Label(sets, textvariable=self.set_vars[key]).grid(row=0, column=col * 2 + 1, sticky="w", padx=2)

        curve_frame = ttk.LabelFrame(right, text="实时曲线")
        curve_frame.pack(fill="both", expand=True)
        self.curve = CurveCanvas(curve_frame, height=380)
        self.curve.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_history(self, nb) -> None:
        f = ttk.Frame(nb)
        nb.add(f, text="历史曲线")

        top = ttk.Frame(f)
        top.pack(fill="x", padx=6, pady=6)
        ttk.Label(top, text="显示范围：").pack(side="left")
        self.var_hist_range = tk.StringVar(value="120")
        ttk.Combobox(top, textvariable=self.var_hist_range, state="readonly", width=8,
                     values=["60", "120", "300", "600", "1200", "全部"]).pack(side="left", padx=4)
        ttk.Button(top, text="刷新", command=self._refresh_history).pack(side="left", padx=6)

        self.hist_curve = CurveCanvas(f, height=360)
        self.hist_curve.pack(fill="both", expand=True, padx=6, pady=6)

        dcols = ("sim_time", "wtg_power_set", "diesel_power_set", "available_wind",
                 "diesel_reserve", "renewable_share", "shortfall")
        ttk.Label(f, text="调度历史（最近 60 条）").pack(anchor="w", padx=6)
        self.disp_tree = ttk.Treeview(f, columns=dcols, show="headings", height=8)
        for c in dcols:
            self.disp_tree.heading(c, text=c)
            self.disp_tree.column(c, width=110, anchor="center")
        self.disp_tree.pack(fill="both", expand=True, padx=6, pady=6)

    def _build_config(self, nb) -> None:
        f = ttk.Frame(nb)
        nb.add(f, text="调度配置")

        sched = ttk.LabelFrame(f, text="调度参数")
        sched.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        ttk.Label(sched, text="数据采集周期 (s)：").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.e_collect = ttk.Entry(sched, width=10)
        self.e_collect.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(sched, text="调度决策周期 (s)：").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.e_decision = ttk.Entry(sched, width=10)
        self.e_decision.grid(row=1, column=1, sticky="w", padx=6)
        self.var_mode = tk.IntVar(value=1)
        ttk.Label(sched, text="控制模式：").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        ttk.Radiobutton(sched, text="闭环", variable=self.var_mode, value=1).grid(row=2, column=1, sticky="w")
        ttk.Radiobutton(sched, text="开环", variable=self.var_mode, value=0).grid(row=2, column=2, sticky="w")
        ttk.Button(sched, text="应用", command=self._apply_sched).grid(row=3, column=0, columnspan=3, pady=8)

        dev = ttk.LabelFrame(f, text="设备参数（由电网模拟器 param_notify 同步，可手动覆盖）")
        dev.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)
        self.dev_vars = {}
        for r, (key, label) in enumerate([
            ("cut_in_wind", "切入风速 (m/s)"), ("cut_out_wind", "切出风速 (m/s)"),
            ("rated_wind", "额定风速 (m/s)"), ("rated_power", "额定功率 (kW)"),
            ("diesel_max", "柴发最大出力 (kW)"), ("diesel_min", "柴发最小出力 (kW)"),
        ]):
            self.dev_vars[key] = tk.StringVar(value="-")
            ttk.Label(dev, text=label + "：").grid(row=r, column=0, sticky="w", padx=6, pady=2)
            ttk.Entry(dev, textvariable=self.dev_vars[key], width=12).grid(row=r, column=1, sticky="w", padx=6)
        ttk.Button(dev, text="保存设备参数", command=self._apply_device_params).grid(
            row=6, column=0, columnspan=2, pady=8)

        f.columnconfigure(0, weight=1)

    def _build_evaluation(self, nb) -> None:
        """调度效果评价页：每 20s 刷新一次三个角色的分数。"""
        f = ttk.Frame(nb)
        nb.add(f, text="调度评价")

        # 顶部：综合分 + 时间
        top = ttk.Frame(f)
        top.pack(fill="x", padx=8, pady=8)
        ttk.Label(top, text="综合调度效果评分：").pack(side="left")
        self.var_overall = tk.StringVar(value="-")
        ttk.Label(top, textvariable=self.var_overall, font=(_FONT, 18, "bold"),
                  foreground="#1f77b4").pack(side="left", padx=6)
        self.var_eval_time = tk.StringVar(value="尚未打分")
        ttk.Label(top, textvariable=self.var_eval_time, foreground="#888888").pack(
            side="right")

        # 三个角色的分数卡片
        cards = ttk.Frame(f)
        cards.pack(fill="x", padx=8)
        self.role_vars = {}
        self.role_item_texts = {}
        roles = [("ems", "EMS 主站"), ("grid", "电网模拟器"), ("wind", "风电子站")]
        for i, (key, label) in enumerate(roles):
            frame = ttk.LabelFrame(cards, text=label)
            frame.grid(row=0, column=i, sticky="nsew", padx=4, pady=4)
            cards.grid_columnconfigure(i, weight=1)
            var = tk.StringVar(value="-")
            self.role_vars[key] = var
            ttk.Label(frame, textvariable=var, font=(_FONT, 28, "bold"),
                      foreground="#1f77b4").pack(pady=6)
            txt = tk.Text(frame, height=5, width=26, font=(_MONO, 8), relief="flat",
                          background="#f5f5f5")
            txt.pack(fill="both", expand=True, padx=6, pady=4)
            txt.configure(state="disabled")
            self.role_item_texts[key] = txt

        # 底部说明
        tip = ttk.Label(f, text="评分规则：满分 100，综合分为三者平均。每 20 秒刷新一次。",
                        foreground="#888888")
        tip.pack(anchor="w", padx=8, pady=6)

    def _build_log(self, nb) -> None:
        f = ttk.Frame(nb)
        nb.add(f, text="运行日志")
        self.log_text = tk.Text(f, wrap="none", height=20, font=(_MONO, 9))
        sb = ttk.Scrollbar(f, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True, padx=6, pady=6)

    # ------------------------------------------------------------------
    def _on_start(self) -> None:
        self._stop_components()
        mode = self.var_conn_mode.get()
        host = self.e_host.get().strip() or "127.0.0.1"
        try:
            port = int(self.e_port.get())
        except ValueError:
            messagebox.showerror("EMS", "端口必须是数字")
            return

        if mode == "mock":
            self.grid = MockGrid(host="127.0.0.1", port=0).start()
            host, port = "127.0.0.1", self.grid.port
            self.var_target.set(f"内置仿真桩 {host}:{port}")
            self.db.add_log("INFO", "hmi", f"内置仿真桩启动于 {host}:{port}")
        else:
            self.var_target.set(f"电网模拟器 {host}:{port}")
            self.db.add_log("INFO", "hmi", f"连接电网模拟器 {host}:{port}")

        self.io = OperatorIO(self.db, host=host, port=port)
        self.core = OperatorCore(self.db)
        self.io.start()
        self.core.start()
        self.var_running.set("运行中")

    def _stop_components(self) -> None:
        if self.io is not None:
            self.io.stop()
            self.io.join(timeout=2)
            self.io = None
        if self.core is not None:
            self.core.stop()
            self.core.join(timeout=2)
            self.core = None
        if self.grid is not None:
            self.grid.stop()
            self.grid = None
        self.var_running.set("已停止")

    def _on_stop(self) -> None:
        self._stop_components()
        self.var_target.set("未启动")

    def _on_close(self) -> None:
        self._stop_components()
        self.destroy()

    # ------------------------------------------------------------------
    def _apply_sched(self) -> None:
        try:
            cp = float(self.e_collect.get())
            dp = float(self.e_decision.get())
            mode = int(self.var_mode.get())
            self.db.set_sched_params(collect_period=cp, decision_period=dp, ctrl_mode=mode)
            self.db.add_log("INFO", "hmi", f"调度参数已更新：采集 {cp}s 决策 {dp}s 模式 {mode}")
            messagebox.showinfo("EMS", "调度参数已应用")
        except ValueError:
            messagebox.showerror("EMS", "请输入合法数字")

    def _apply_device_params(self) -> None:
        try:
            fields = {k: float(v.get()) for k, v in self.dev_vars.items()}
            self.db.update_device_params(**fields)
            self.db.add_log("INFO", "hmi", f"设备参数手动更新：{sorted(fields)}")
            messagebox.showinfo("EMS", "设备参数已保存")
        except ValueError:
            messagebox.showerror("EMS", "设备参数必须是数字")

    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        try:
            self._refresh_monitor()
            self._refresh_config()
            self._refresh_history()
            self._refresh_log()
        except Exception:
            pass
        self.after(_REFRESH_MS, self._refresh)

    def _refresh_monitor(self) -> None:
        cs = self.db.get_comm_status()
        self.var_conn.set("已连接" if cs.get("connected") else "未连接")
        self.var_last_rx.set(cs.get("last_data_update") or "-")
        self.var_last_cmd.set(cs.get("last_command_result") or "-")

        yc = self.db.get_scada_yc()
        yx = self.db.get_scada_yx()
        for k in ("wind_speed", "load_power", "wtg_power", "diesel_power"):
            self.scada_vars[k].set(_fmt(yc.get(k)))
        self.scada_vars["wtg_status"].set("运行" if yx.get("wtg_status") else "停机")
        self.scada_vars["diesel_status"].set("运行" if yx.get("diesel_status") else "停机")

        dev = self.db.get_device_realtime()
        self.set_vars["wtg_power_set"].set(_fmt(dev.get("wtg_power_set")))
        self.set_vars["diesel_power_set"].set(_fmt(dev.get("diesel_power_set")))
        self.set_vars["available_wind"].set(_fmt(dev.get("available_wind")))
        params = self.db.get_device_params()
        reserve = (params.get("diesel_max") or 0) - (dev.get("diesel_power_set") or 0)
        self.set_vars["diesel_reserve"].set(_fmt(reserve))

        metrics = strategy.evaluate_dispatch(
            load_power=yc.get("load_power") or 0,
            wtg_power=yc.get("wtg_power") or 0,
            diesel_power=yc.get("diesel_power") or 0,
            available_wind=dev.get("available_wind") or 0,
            diesel_max=params.get("diesel_max") or 0,
        )
        self.eval_vars["balance_error"].set(_fmt(metrics["balance_error"]))
        self.eval_vars["renewable_share"].set(f"{metrics['renewable_share']*100:.1f}%")
        self.eval_vars["wind_utilization"].set(f"{metrics['wind_utilization']*100:.1f}%")
        self.eval_vars["diesel_reserve_ratio"].set(f"{metrics['diesel_reserve_ratio']*100:.1f}%")

        hist = self.db.get_history(limit=2000)
        self.curve.set_series({
            "风速": [h["wind_speed"] for h in hist],
            "负荷": [h["load_power"] for h in hist],
            "风机出力": [h["wtg_power"] for h in hist],
            "柴发出力": [h["diesel_power"] for h in hist],
        })

    def _refresh_config(self) -> None:
        p = self.db.get_sched_params()
        if not self.e_collect.get():
            self.e_collect.insert(0, _fmt(p.get("data_collect_period"), 0))
            self.e_decision.insert(0, _fmt(p.get("decision_period"), 0))
            self.var_mode.set(int(p.get("ctrl_mode_ems", 1)))
        dev = self.db.get_device_params()
        for k in self.dev_vars:
            self.dev_vars[k].set(_fmt(dev.get(k)))

    def _refresh_history(self) -> None:
        rng = self.var_hist_range.get()
        limit = 2000 if rng == "全部" else int(rng)
        hist = self.db.get_history(limit=limit)
        # 关键：让曲线按所选范围显示对应点数（否则一直只画默认 120 个点）
        self.hist_curve.set_max_points(limit)
        self.hist_curve.set_series({
            "风速": [h["wind_speed"] for h in hist],
            "负荷": [h["load_power"] for h in hist],
            "风机出力": [h["wtg_power"] for h in hist],
            "柴发出力": [h["diesel_power"] for h in hist],
        })

        self.disp_tree.delete(*self.disp_tree.get_children())
        for d in self.db.get_dispatch_history(limit=60):
            self.disp_tree.insert("", "end", values=(
                _fmt(d["sim_time"], 0), _fmt(d["wtg_power_set"]), _fmt(d["diesel_power_set"]),
                _fmt(d["available_wind"]), _fmt(d["diesel_reserve"]),
                f"{d['renewable_share']*100:.1f}%" if d.get("renewable_share") is not None else "-",
                _fmt(d["shortfall"]),
            ))

    def _refresh_log(self) -> None:
        self.log_text.delete("1.0", "end")
        for l in self.db.get_logs(limit=200):
            self.log_text.insert(
                "end", f"[{l['time']}] [{l['level']}] ({l['source']}) {l['message']}\n")
        self.log_text.see("end")

    def _refresh_evaluation(self) -> None:
        """每 20s 打一次三个角色的调度效果分数。"""
        try:
            scores = evaluation.compute_scores(self.db)
            self.var_overall.set(f"{scores['overall']:.1f}")
            self.var_eval_time.set("最近打分：" + time.strftime("%H:%M:%S", time.localtime()))
            for key, label in [("ems", "EMS 主站"), ("grid", "电网模拟器"), ("wind", "风电子站")]:
                info = scores[key]
                self.role_vars[key].set(f"{info['score']:.1f}")
                txt = self.role_item_texts[key]
                txt.configure(state="normal")
                txt.delete("1.0", "end")
                for name, s, full in info["items"]:
                    txt.insert("end", f"{name:<8} {s:5.1f} / {full:.0f}\n")
                txt.configure(state="disabled")
        except Exception:
            pass
        self.after(20000, self._refresh_evaluation)
