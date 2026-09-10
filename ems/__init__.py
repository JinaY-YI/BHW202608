"""EMS 主站 (学生 B) 软件包。

南极考察站微电网智能调控系统 —— 能量管理系统 (EMS) 主站。

模块划分：
- config        : 全局配置常量
- database      : ems.db 数据库 schema 与 CRUD
- protocol      : V3 TCP 通信协议 JSON 报文构建/解析
- strategy      : 调度决策算法（风电优先、柴发补偿、保留备用）
- operator_io   : 通信进程（与电网模拟器 TCP 交互）
- operator_core : 调度策略进程（本地计算）
- mock_grid     : 电网模拟器仿真桩（用于独立测试）
- hmi           : 人机界面（tkinter）
- app           : 编排器（组装各进程/线程并启动）
"""

__version__ = "1.0.0"
