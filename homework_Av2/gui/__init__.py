"""
gui 包：图形界面模块

包含：
- main_window: 主窗口
- control_panel: 仿真控制面板
- comm_status: 通信状态显示
- realtime_data: 实时数据显示
- realtime_curve: 实时曲线Tab
- history_curve: 历史曲线Tab
- curve_config: 曲线配置Tab
- scada_table: SCADA数据Tab
- log_view: 运行日志Tab
"""

from .main_window import MainWindow

__all__ = ["MainWindow"]