#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
南极考察站微电网 —— 风电子站上位机（人机界面）V2
====================================================
架构说明：
  本程序为人机界面，本身不直接访问串口。
  串口收发由后台进程 serialport.py 独占完成，数据库 wind.db 作为中转：
      单片机 --串口--> serialport.py --写入--> wind.db --读取--> 本界面
      本界面(参数/模式修改) --写入--> wind.db --检测--> serialport.py --串口--> 单片机

功能（对应大作业任务书 3.3-(2)-3）：
  1. 监视电网模拟器与风机控制器的通信连接情况（link_grid 指示灯 + 串口数据新鲜度）
  2. 监视风机与单片机控制器的运行状态（启停、开环/闭环模式）
  3. 展示当前风机参数与实时运行数据
  4. 修改风机参数（切入/切出/额定风速、额定功率）与控制参数（开环/闭环），
     写入数据库后由 serialport.py 自动通过串口下发到风机控制器（CMD 0x01 / 0x03）
  5. 以曲线/表格方式展示风速与风机相关的历史数据

运行前：
  1. 先运行 db_init.py 初始化数据库
  2. 再后台运行 serialport.py（保持串口数据更新）
  3. 最后运行本程序
"""

import sys
import os
import sqlite3
import time
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QLineEdit, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QGridLayout,
    QMessageBox, QRadioButton, QCheckBox, QButtonGroup
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
import pyqtgraph as pg

# ==================== 配置 ====================
# 与 serialport.py / db_init.py 保持一致（wind.db 目录下的 wind.db 文件）
DB_PATH = os.path.join("wind.db", "wind.db")

# 串口数据新鲜度阈值：超过该秒数未收到 CMD 0x02 数据，判定控制器通信中断
SERIAL_FRESH_SECONDS = 5.0
# 界面刷新周期
FAST_REFRESH_MS = 500      # 实时数据/状态/历史
SLOW_REFRESH_MS = 1000     # 参数回显（避免覆盖正在编辑的内容）
# 历史曲线/表格显示的最大点数
HISTORY_LIMIT = 600
TABLE_ROWS = 200

# 参数合法范围（依据《变量字典与通信协议 V3》第二部分）
PARAM_RANGES = {
    "cut_in_wind":  (0.0, 10.0, "切入风速 (m/s)"),
    "cut_out_wind": (10.0, 30.0, "切出风速 (m/s)"),
    "rated_wind":   (5.0, 20.0, "额定风速 (m/s)"),
    "rated_power":  (0.0, 200.0, "额定功率 (kW)"),
}
DEFAULT_PARAMS = {"cut_in_wind": 3.0, "cut_out_wind": 25.0,
                  "rated_wind": 12.0, "rated_power": 100.0}

COLOR_OK = "#1a9850"      # 在线/运行
COLOR_OFF = "#d73027"     # 离线/停机
COLOR_MODE = "#2166ac"    # 模式


def db_log(level: str, source: str, message: str):
    """向 wind.db 的 sys_log 表写日志（供追溯验收）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO sys_log (level, source, message) VALUES (?, ?, ?)",
            (level, source, message))
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass  # 日志失败不影响界面运行


class DatabaseReader:
    """封装数据库读写操作，适配 wind.db 表结构（短连接，避免多进程锁冲突）"""

    @staticmethod
    def init_ui_cmd_table():
        """确保 ui_cmd 命令中转表存在（serialport.py 会轮询并消费该表）"""
        conn = sqlite3.connect(DB_PATH)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS ui_cmd (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cmd TEXT NOT NULL,          -- 'set_mode' / 'query_params'
                value REAL,
                create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()

    @staticmethod
    def push_ui_cmd(cmd: str, value=0) -> bool:
        """向上位机命令表写入一条命令，由 serialport.py 转发到串口"""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("INSERT INTO ui_cmd (cmd, value) VALUES (?, ?)",
                         (cmd, value))
            conn.commit()
            conn.close()
            return True
        except sqlite3.Error:
            return False

    @staticmethod
    def get_ctrl_runtime() -> dict:
        """获取控制器最新运行状态（ctrl_runtime_info，含 link_grid）"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute('''
                SELECT wtg_status, ctrl_mode_wind, link_grid,
                       pitch_angle_set, wtg_power, wtg_power_set, available_power
                FROM ctrl_runtime_info ORDER BY id DESC LIMIT 1
            ''')
            row = c.fetchone()
        except sqlite3.OperationalError:
            row = None
        conn.close()
        keys = ["wtg_status", "ctrl_mode_wind", "link_grid",
                "pitch_angle_set", "wtg_power", "wtg_power_set", "available_power"]
        if row:
            return dict(zip(keys, row))
        return {k: 0 for k in keys}

    @staticmethod
    def get_latest_history():
        """获取最新一条历史记录（用于实时显示与串口新鲜度判断）"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute('''
                SELECT timestamp, wind_speed, wtg_power, wtg_power_set,
                       pitch_angle_set, available_power, wtg_status,
                       ctrl_mode_wind, link_grid
                FROM history_data ORDER BY id DESC LIMIT 1
            ''')
            row = c.fetchone()
        except sqlite3.OperationalError:
            row = None
        conn.close()
        if not row:
            return None
        keys = ["timestamp", "wind_speed", "wtg_power", "wtg_power_set",
                "pitch_angle_set", "available_power", "wtg_status",
                "ctrl_mode_wind", "link_grid"]
        return dict(zip(keys, row))

    @staticmethod
    def get_history(limit=HISTORY_LIMIT):
        """获取最近 limit 条历史记录（从旧到新，用于曲线和表格）"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute('''
                SELECT timestamp, wind_speed, wtg_power, wtg_power_set,
                       pitch_angle_set, available_power, wtg_status,
                       ctrl_mode_wind, link_grid
                FROM history_data ORDER BY id DESC LIMIT ?
            ''', (limit,))
            rows = c.fetchall()
        except sqlite3.OperationalError:
            rows = []
        conn.close()
        rows.reverse()  # 从旧到新
        return rows

    @staticmethod
    def get_device_params() -> dict:
        """获取当前设备参数（含最后同步时间，用于显示参数与单片机的同步时刻）"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute("SELECT cut_in_wind, cut_out_wind, rated_wind, rated_power, "
                      "update_time FROM device_params LIMIT 1")
            row = c.fetchone()
        except sqlite3.OperationalError:
            row = None
        conn.close()
        if row:
            return {
                "cut_in_wind": row[0], "cut_out_wind": row[1],
                "rated_wind": row[2], "rated_power": row[3],
                "update_time": row[4],
            }
        d = dict(DEFAULT_PARAMS)
        d["update_time"] = None
        return d

    @staticmethod
    def update_device_params(cut_in: float, cut_out: float,
                             rated_wind: float, rated_power: float):
        """更新设备参数表（serialport.py 每 1s 检测变化并经串口下发 CMD 0x01）"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM device_params")
        c.execute('''
            INSERT INTO device_params (cut_in_wind, cut_out_wind, rated_wind, rated_power,
                                       update_time)
            VALUES (?, ?, ?, ?, datetime('now','localtime'))
        ''', (cut_in, cut_out, rated_wind, rated_power))
        conn.commit()
        conn.close()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("风电子站上位机 —— 风机监控与参数维护")
        self.setGeometry(80, 60, 1280, 860)

        # 参数编辑脏标记：用户正在修改未保存时，暂停自动回显，避免覆盖输入
        self._params_dirty = False
        self._last_param_refresh = 0.0

        self._init_ui()

        # 定时器
        self.fast_timer = QTimer(self)
        self.fast_timer.timeout.connect(self.update_display)
        self.fast_timer.start(FAST_REFRESH_MS)

        self.slow_timer = QTimer(self)
        self.slow_timer.timeout.connect(self.refresh_params_from_db)
        self.slow_timer.start(SLOW_REFRESH_MS)

        self.update_display()
        self.refresh_params_from_db()

    # ------------------------------------------------------------------
    # 界面构建
    # ------------------------------------------------------------------
    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(8)

        main_layout.addWidget(self._build_status_group())
        main_layout.addWidget(self._build_realtime_group())

        mid_layout = QHBoxLayout()
        mid_layout.addWidget(self._build_param_group(), 3)
        mid_layout.addWidget(self._build_mode_group(), 2)
        main_layout.addLayout(mid_layout)

        main_layout.addWidget(self._build_history_group(), 1)

    def _build_status_group(self) -> QGroupBox:
        """系统状态栏：四个指示灯"""
        group = QGroupBox("系统状态")
        layout = QHBoxLayout(group)
        label_font = QFont("Microsoft YaHei", 11, QFont.Weight.Bold)

        self.lbl_grid = QLabel("● 电网模拟器：--")
        self.lbl_serial = QLabel("● 控制器串口通信：--")
        self.lbl_run = QLabel("● 风机状态：--")
        self.lbl_mode = QLabel("● 控制模式：--")
        self.lbl_update = QLabel("最后数据时间：--")

        for lbl in (self.lbl_grid, self.lbl_serial, self.lbl_run, self.lbl_mode):
            lbl.setFont(label_font)
            layout.addWidget(lbl)
        layout.addStretch()
        layout.addWidget(self.lbl_update)
        return group

    def _build_realtime_group(self) -> QGroupBox:
        """实时运行数据面板"""
        group = QGroupBox("实时运行数据")
        layout = QGridLayout(group)
        label_font = QFont("Microsoft YaHei", 11, QFont.Weight.Bold)
        value_font = QFont("Consolas", 16, QFont.Weight.Bold)

        items = [
            ("风速 (m/s)", "wind_speed", 0, 0),
            ("实际功率 (kW)", "wtg_power", 0, 1),
            ("设定功率 (kW)", "wtg_power_set", 0, 2),
            ("目标桨距角 (°)", "pitch_angle_set", 1, 0),
            ("可用功率 (kW)", "available_power", 1, 1),
        ]
        self.display_labels = {}
        self.display_captions = {}      # 标题控件，开环/闭环时切换文案
        for text, key, row, col in items:
            lbl = QLabel(text)
            lbl.setFont(label_font)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val = QLabel("--")
            val.setFont(value_font)
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val.setStyleSheet(
                "border: 1px solid #bbb; border-radius: 6px; padding: 6px;"
                "background: #fafafa; color: #222;")
            layout.addWidget(lbl, row * 2, col)
            layout.addWidget(val, row * 2 + 1, col)
            self.display_labels[key] = val
            self.display_captions[key] = lbl

        # 开环/闭环下 wtg_power 的语义提示（开环为单片机本地预估值）
        self._power_label_name = None
        self.lbl_power_hint = QLabel("")
        self.lbl_power_hint.setWordWrap(True)
        self.lbl_power_hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.lbl_power_hint, 4, 0, 1, 3)
        return group

    def _build_param_group(self) -> QGroupBox:
        """风机参数修改面板（写入数据库 → serialport.py 串口下发 CMD 0x01）"""
        group = QGroupBox("风机参数修改（保存后经串口下发至风机控制器）")
        layout = QGridLayout(group)

        self.param_edits = {}
        for i, key in enumerate(["cut_in_wind", "cut_out_wind",
                                 "rated_wind", "rated_power"]):
            lo, hi, text = PARAM_RANGES[key]
            lbl = QLabel(f"{text}\n范围 {lo}~{hi}")
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            edit = QLineEdit()
            edit.textChanged.connect(lambda _=None: self._mark_params_dirty())
            layout.addWidget(lbl, i, 0)
            layout.addWidget(edit, i, 1)
            self.param_edits[key] = edit

        self.btn_save = QPushButton("保存并下发参数")
        self.btn_save.clicked.connect(self.save_params)
        self.btn_read = QPushButton("从单片机读取参数")
        self.btn_read.clicked.connect(self.read_params_from_mcu)
        self.btn_default = QPushButton("恢复默认值")
        self.btn_default.clicked.connect(self.fill_default_params)
        layout.addWidget(self.btn_save, 4, 0)
        layout.addWidget(self.btn_read, 4, 1)
        layout.addWidget(self.btn_default, 5, 0)

        self.lbl_param_sync = QLabel("参数同步时刻：--")
        self.lbl_param_sync.setStyleSheet("color: #1a9850; font-size: 11px;")
        layout.addWidget(self.lbl_param_sync, 5, 1)

        hint = QLabel("")
        # 说明：参数写入本地数据库后，串口进程自动检测变化并通过\n
        # "
        # "CMD 0x01 下发到单片机；同时每 5 秒自动回读单片机中的参数\n"
        # "（CMD 0x05/0x06），界面将自动刷新为单片机中的真实值。
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint, 6, 0, 1, 2)
        return group

    def _build_mode_group(self) -> QGroupBox:
        """控制模式（开环/闭环）修改面板（串口下发 CMD 0x03）"""
        group = QGroupBox("控制参数修改（开环 / 闭环）")
        layout = QVBoxLayout(group)

        self.rb_open = QRadioButton("开环模式 (0)")
        self.rb_close = QRadioButton("闭环模式 (1)")
        self.rb_close.setChecked(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.rb_open, 0)
        self._mode_group.addButton(self.rb_close, 1)

        btn = QPushButton("下发控制模式")
        btn.clicked.connect(self.send_mode)

        hint = QLabel("")
        #说明：模式命令经数据库中转，由串口进程\n"
        #             "通过 CMD 0x03 下发到单片机。
        hint.setStyleSheet("color: #666; font-size: 11px;")

        layout.addWidget(self.rb_open)
        layout.addWidget(self.rb_close)
        layout.addWidget(btn)
        layout.addWidget(hint)
        layout.addStretch()
        return group

    def _build_history_group(self) -> QGroupBox:
        """历史数据：曲线 + 表格"""
        group = QGroupBox(f"历史数据（最近 {HISTORY_LIMIT} 条）")
        layout = QVBoxLayout(group)

        tabs = QTabWidget()

        # ---- 曲线页 ----
        plot_page = QWidget()
        plot_layout = QVBoxLayout(plot_page)
        # 曲线选择
        check_layout = QHBoxLayout()
        self.curve_checks = {}
        series = [
            ("wind_speed", "风速", "#1f77b4"),
            ("wtg_power", "实际功率", "#d62728"),
            ("wtg_power_set", "设定功率", "#ff7f0e"),
            ("pitch_angle_set", "桨距角", "#2ca02c"),
            ("available_power", "可用功率", "#9467bd"),
        ]
        self.curves = {}
        for key, name, color in series:
            cb = QCheckBox(name)
            cb.setChecked(key in ("wind_speed", "wtg_power"))
            cb.setStyleSheet(f"color: {color}; font-weight: bold;")
            cb.stateChanged.connect(self._on_curve_check)
            check_layout.addWidget(cb)
            self.curve_checks[key] = (cb, name, color)
        check_layout.addStretch()
        plot_layout.addLayout(check_layout)

        axis = pg.DateAxisItem(orientation='bottom')
        self.plot = pg.PlotWidget(axisItems={'bottom': axis})
        self.plot.setBackground('w')


        self.plot.setTitle("历史曲线（时间轴）", color='#333', size='11pt')
        self.plot.setLabel('left', '数值')
        self.plot.addLegend(offset=(10, 10))
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        for key, (cb, name, color) in self.curve_checks.items():
            self.curves[key] = self.plot.plot(
                pen=pg.mkPen(color, width=2), name=name)
        plot_layout.addWidget(self.plot)
        tabs.addTab(plot_page, "曲线")

        # ---- 表格页 ----
        self.table = QTableWidget()
        headers = ["时间", "风速(m/s)", "实际功率(kW)", "设定功率(kW)",
                   "桨距角(°)", "可用功率(kW)", "风机状态", "模式", "电网连接"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        tabs.addTab(self.table, "表格")

        layout.addWidget(tabs)
        return group

    # ------------------------------------------------------------------
    # 数据刷新
    # ------------------------------------------------------------------
    def _set_indicator(self, lbl: QLabel, ok: bool, text_ok: str, text_off: str):
        lbl.setText(f"● {text_ok if ok else text_off}")
        lbl.setStyleSheet(f"color: {COLOR_OK if ok else COLOR_OFF}; font-weight: bold;")

    def _set_power_labels(self, mode_closed: bool):
        """同步 wtg_power 各处文案。

        闭环：该字段是电网模拟器 wind_result 回传的实测并网功率 -> "实际功率"
        开环：单片机不下发指令、模拟器也不回传，该字段是控制器按同一出力模型
              本地推算的预估值 -> "预估出力"（避免与实测值混淆）
        """
        name = "实际功率" if mode_closed else "预估出力"
        if self._power_label_name == name:
            return
        self._power_label_name = name

        # 1) 实时数据面板标题
        cap = self.display_captions.get("wtg_power")
        if cap is not None:
            cap.setText(f"{name} (kW)")

        # 2) 曲线选择框 + 图例
        entry = self.curve_checks.get("wtg_power")
        if entry is not None:
            cb, _old, color = entry
            cb.setText(name)
            self.curve_checks["wtg_power"] = (cb, name, color)
            try:
                legend = self.plot.plotItem.legend
                if legend is not None:
                    label = legend.getLabel(self.curves["wtg_power"])
                    if label is not None:
                        label.setText(name)
            except Exception:
                pass

        # 3) 表格表头
        item = self.table.horizontalHeaderItem(2)
        if item is not None:
            item.setText(f"{name}(kW)")

        # 4) 语义提示
        if mode_closed:
            self.lbl_power_hint.setText(
                "闭环：功率取自电网模拟器 wind_result 回传的实测值。")
        else:
            self.lbl_power_hint.setText(
                "开环：单片机按与闭环相同的逻辑计算策略，但不下发控制指令，"
                "故此处为本地预估出力 = 可用功率 × (1 − 桨距角/90°)。")

    def update_display(self):
        """定时刷新：状态指示灯、实时数据、历史曲线与表格"""
        latest = DatabaseReader.get_latest_history()
        ctrl = DatabaseReader.get_ctrl_runtime()

        # ---- 1. 状态指示灯 ----
        # 电网模拟器 ↔ 风机控制器 TCP 连接（link_grid 由单片机上报）
        self._set_indicator(self.lbl_grid, bool(ctrl.get("link_grid", 0)),
                            "电网模拟器：在线", "电网模拟器：离线")
        # 控制器串口通信：以最新历史数据时间戳判断新鲜度
        if latest and latest.get("timestamp"):
            age = time.time() - latest["timestamp"]
            serial_ok = 0 <= age <= SERIAL_FRESH_SECONDS
        else:
            age = None
            serial_ok = False
        self._set_indicator(self.lbl_serial, serial_ok,
                            "控制器串口通信：正常", "控制器串口通信：中断")
        # 风机启停状态
        self._set_indicator(self.lbl_run, bool(ctrl.get("wtg_status", 0)),
                            "风机状态：运行", "风机状态：停机")
        # 控制模式
        mode_closed = bool(ctrl.get("ctrl_mode_wind", 0))
        self.lbl_mode.setText(f"● 控制模式：{'闭环' if mode_closed else '开环'}")
        self.lbl_mode.setStyleSheet(f"color: {COLOR_MODE}; font-weight: bold;")
        # 开环下"实际功率"是单片机本地预估值，切换各处文案
        self._set_power_labels(mode_closed)

        if latest and latest.get("timestamp"):
            self.lbl_update.setText(
                "最后数据时间：" + datetime.fromtimestamp(latest["timestamp"]).strftime("%H:%M:%S"))
        else:
            self.lbl_update.setText("最后数据时间：--")

        # ---- 2. 实时数值（优先取最新历史记录，缺失时用 ctrl_runtime_info 兜底）----
        def pick(key):
            if latest and latest.get(key) is not None:
                return latest[key]
            return ctrl.get(key, 0.0)

        values = {
            "wind_speed": pick("wind_speed"),
            "wtg_power": pick("wtg_power"),
            "wtg_power_set": pick("wtg_power_set"),
            "pitch_angle_set": pick("pitch_angle_set"),
            "available_power": pick("available_power"),
        }
        for key, val in values.items():
            try:
                self.display_labels[key].setText(f"{float(val):.2f}")
            except (TypeError, ValueError):
                self.display_labels[key].setText("--")

        # ---- 3. 历史曲线与表格 ----
        history = DatabaseReader.get_history()
        if history:
            ts = [row[0] for row in history]
            for key, col in (("wind_speed", 1), ("wtg_power", 2),
                             ("wtg_power_set", 3), ("pitch_angle_set", 4),
                             ("available_power", 5)):
                cb, name, _color = self.curve_checks[key]
                if cb.isChecked():
                    data = [row[col] if row[col] is not None else 0.0 for row in history]
                    self.curves[key].setData(ts, data)
                else:
                    self.curves[key].setData([], [])

            # 表格（最新在上，限制行数保证流畅）
            rows = list(reversed(history[-TABLE_ROWS:]))
            self.table.setRowCount(len(rows))
            for i, row in enumerate(rows):
                t_str = datetime.fromtimestamp(row[0]).strftime("%m-%d %H:%M:%S") \
                    if row[0] else "--"
                cells = [
                    t_str,
                    f"{row[1]:.2f}" if row[1] is not None else "--",
                    f"{row[2]:.2f}" if row[2] is not None else "--",
                    f"{row[3]:.2f}" if row[3] is not None else "--",
                    f"{row[4]:.2f}" if row[4] is not None else "--",
                    f"{row[5]:.2f}" if row[5] is not None else "--",
                    "运行" if row[6] else "停机",
                    "闭环" if row[7] else "开环",
                    "在线" if row[8] else "离线",
                ]
                for j, text in enumerate(cells):
                    self.table.setItem(i, j, QTableWidgetItem(text))

    def _on_curve_check(self):
        """切换曲线显隐（数据将在下一次刷新时填充/清空）"""
        pass

    def refresh_params_from_db(self):
        """
        定期从数据库回显参数：数据库中的参数由串口进程周期回读单片机（CMD 0x05/0x06）
        更新，因此界面会自动显示单片机中的真实参数。
        用户正在编辑未保存时（dirty）不覆盖输入框，但仍刷新同步时刻提示。
        """
        params = DatabaseReader.get_device_params()
        if not params:
            return

        # 同步时刻提示始终刷新
        ts = params.get("update_time")
        if ts:
            self.lbl_param_sync.setText(f"参数同步时刻：{ts}（单片机回读）")
        else:
            self.lbl_param_sync.setText("参数同步时刻：--")

        if self._params_dirty:
            return

        for key, edit in self.param_edits.items():
            val = params.get(key)
            if val is not None and edit.text() != f"{val:g}":
                edit.blockSignals(True)
                edit.setText(f"{val:g}")
                edit.blockSignals(False)
        self._last_param_refresh = time.time()

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def _mark_params_dirty(self):
        self._params_dirty = True

    def _parse_params(self):
        """解析并校验 4 个风机参数，非法时弹窗提示并返回 None"""
        vals = {}
        for key, (lo, hi, name) in PARAM_RANGES.items():
            text = self.param_edits[key].text().strip()
            try:
                v = float(text)
            except ValueError:
                QMessageBox.warning(self, "输入错误", f"{name}：请输入有效数值")
                return None
            if not (lo <= v <= hi):
                QMessageBox.warning(self, "超出范围",
                                    f"{name}：允许范围 {lo}~{hi}，当前输入 {v:g}")
                return None
            vals[key] = v
        if vals["cut_in_wind"] >= vals["cut_out_wind"]:
            QMessageBox.warning(self, "逻辑错误", "切入风速必须小于切出风速")
            return None
        if vals["rated_wind"] <= vals["cut_in_wind"] or vals["rated_wind"] >= vals["cut_out_wind"]:
            QMessageBox.warning(self, "逻辑错误",
                                "额定风速应介于切入风速与切出风速之间")
            return None
        return vals

    def save_params(self):
        """保存风机参数：写数据库 → serialport.py 检测变化 → 串口下发 CMD 0x01"""
        vals = self._parse_params()
        if vals is None:
            return
        try:
            DatabaseReader.update_device_params(
                vals["cut_in_wind"], vals["cut_out_wind"],
                vals["rated_wind"], vals["rated_power"])
        except sqlite3.Error as e:
            QMessageBox.critical(self, "保存失败", f"数据库写入失败：{e}")
            return
        self._params_dirty = False
        db_log("INFO", "wind_ui",
               f"上位机修改风机参数并下发：切入={vals['cut_in_wind']:g}, "
               f"切出={vals['cut_out_wind']:g}, 额定风速={vals['rated_wind']:g}, "
               f"额定功率={vals['rated_power']:g}")
        QMessageBox.information(
            self, "成功",
            "参数已写入数据库，串口进程将在 1 秒内通过 CMD 0x01 下发至风机控制器。\n"
            "界面随后会自动回读单片机中的参数，显示值与单片机保持一致。")

    def send_mode(self):
        """下发控制模式：写 ui_cmd 表 → serialport.py → 串口 CMD 0x03"""
        mode = 1 if self.rb_close.isChecked() else 0
        if not DatabaseReader.push_ui_cmd("set_mode", mode):
            QMessageBox.critical(self, "失败",
                                 "命令写入数据库失败，请确认数据库可用。")
            return
        db_log("INFO", "wind_ui",
               f"上位机下发控制模式：{'闭环' if mode else '开环'}")
        QMessageBox.information(
            self, "已提交",
            f"控制模式（{'闭环' if mode else '开环'}）命令已提交，"
            "将由串口进程通过 CMD 0x03 下发至风机控制器。")

    def read_params_from_mcu(self):
        """
        主动向单片机查询当前参数（CMD 0x05），
        串口进程收到应答（CMD 0x06）后写入数据库，界面随即自动刷新。
        """
        if not DatabaseReader.push_ui_cmd("query_params", 0):
            QMessageBox.critical(self, "失败", "命令写入数据库失败，请确认数据库可用。")
            return
        db_log("INFO", "wind_ui", "上位机请求从单片机读取风机参数（CMD 0x05）")
        # 立即取消脏标记，使回读结果可以立刻回显
        self._params_dirty = False
        QMessageBox.information(
            self, "已提交",
            "查询命令已提交，串口进程将通过 CMD 0x05 向单片机读取参数，\n"
            "收到应答后界面会自动刷新（也可等待每 5 秒的周期回读）。")

    def fill_default_params(self):
        for key, val in DEFAULT_PARAMS.items():
            self.param_edits[key].blockSignals(True)
            self.param_edits[key].setText(f"{val:g}")
            self.param_edits[key].blockSignals(False)
        self._params_dirty = False


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print(f"警告：数据库文件 {DB_PATH} 不存在，请先运行 db_init.py 和 serialport.py。")
    try:
        DatabaseReader.init_ui_cmd_table()
    except sqlite3.Error as e:
        print(f"初始化 ui_cmd 表失败：{e}")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
