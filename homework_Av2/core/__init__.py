"""
core 包：核心模块

包含：
- waveform: 波形生成函数
- client_session: TCP会话管理
- message_handler: 报文分发与处理
- tcp_server: TCP服务器
"""

# 端口与身份常量
PORT_EMS = 8888
PORT_WIND = 8088
DST_GRID = "grid_simulator"
SRC = "grid_simulator"

# 导出
from .waveform import *
from .client_session import ClientSession
from .message_handler import MessageHandler
from .tcp_server import TCPServer

__all__ = [
    "PORT_EMS", "PORT_WIND", "DST_GRID", "SRC",
    "generate_sine_wave", "generate_constant",
    "generate_square_wave", "generate_noise_wave",
    "ClientSession", "MessageHandler", "TCPServer"
]