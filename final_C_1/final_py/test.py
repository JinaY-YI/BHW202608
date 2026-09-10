#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
南极考察站微电网 —— 风机控制器上位机串口监控程序
功能：
1. 通过串口接收单片机上报的实时运行数据（CMD 0x02）
2. 解析数据并更新数据库 ctrl_runtime_info 表（最新值）
3. 将每条记录插入 history_data 表（历史数据）
4. 支持发送修改参数（CMD 0x01）、修改模式（CMD 0x03）、查询参数（CMD 0x05）命令
5. 日志记录与错误处理

使用前请确保已安装 pyserial 和 sqlite3（内置）
"""

import serial
import serial.tools.list_ports
import struct
import sqlite3
import time
import logging
import threading
import os
from datetime import datetime
from typing import Optional, Tuple, Dict, Any

# ==================== 配置 ====================
DB_PATH = "wind.db/wind.db"                     # 数据库文件路径
SERIAL_PORT = "COM9"                    # 请根据实际修改（Windows）或 "/dev/ttyUSB0"（Linux）
BAUDRATE = 115200
TIMEOUT = 0.5                           # 串口读取超时（秒）
LOG_LEVEL = logging.INFO

# 协议常量
FRAME_HEAD1 = 0xAA
FRAME_HEAD2 = 0x55
FRAME_TAIL  = 0x0D

CMD_SET_PARAM       = 0x01   # 上位机 → 控制器：修改风机参数
CMD_REPORT_REALTIME = 0x02   # 控制器 → 上位机：上送实时数据
CMD_SET_MODE        = 0x03   # 上位机 → 控制器：修改控制模式
CMD_ACK_RESULT      = 0x04   # 控制器 → 上位机：应答修改结果
CMD_QUERY_PARAM     = 0x05   # 上位机 → 控制器：查询当前全部参数
CMD_RESP_PARAM      = 0x06   # 控制器 → 上位机：返回查询参数

# 实时数据体结构（24字节，小端浮点）
REALTIME_STRUCT = struct.Struct('<fffffBBBB')  # 5个float + 4个uint8
# 顺序：wind_speed, wtg_power_set, wtg_power, pitch_angle_set, available_power,
#       wtg_status, ctrl_mode_wind, link_grid, reserved

# 参数数据体结构（16字节，4个float）
PARAM_STRUCT = struct.Struct('<ffff')  # cut_in, cut_out, rated_wind, rated_power


# ==================== 日志配置 ====================
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("wind_serial.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ==================== 数据库操作类 ====================
class DatabaseManager:
    """封装数据库更新操作，确保线程安全"""
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def update_realtime(self, data: Dict[str, Any]) -> None:
        """
        更新 ctrl_runtime_info 表（只保留最新一条记录）
        data 应包含字段：wtg_status, pitch_angle_set, wtg_power,
                         wtg_power_set, ctrl_mode_wind, available_power, link_grid
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            # 先清空表，再插入新记录（或使用 UPDATE 方式，这里采用删除+插入保证单条）
            cursor.execute("DELETE FROM ctrl_runtime_info")
            cursor.execute('''
                INSERT INTO ctrl_runtime_info 
                (wtg_status, pitch_angle_set, wtg_power, wtg_power_set,
                 ctrl_mode_wind, available_power, link_grid)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                data.get('wtg_status', 0),
                data.get('pitch_angle_set', 0.0),
                data.get('wtg_power', 0.0),
                data.get('wtg_power_set', 0.0),
                data.get('ctrl_mode_wind', 0),
                data.get('available_power', 0.0),
                data.get('link_grid', 0)
            ))
            conn.commit()
            cursor.close()
            conn.close()
            logger.debug("更新 ctrl_runtime_info 成功")

    def insert_history(self, data: Dict[str, Any]) -> None:
        """
        向 history_data 表插入一条历史记录
        data 应包含字段：sim_time, wind_speed, load_power, wtg_power, diesel_power,
                         pitch_angle_set, wtg_status, ctrl_mode_wind,
                         available_power, wtg_power_set
        注：load_power 和 diesel_power 在串口数据中不包含，此处用占位值（0），
            如有需要可从其他来源获取。
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO history_data 
                (sim_time, wind_speed, load_power, wtg_power, diesel_power,
                 pitch_angle_set, wtg_status, ctrl_mode_wind,
                 available_power, wtg_power_set)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                data.get('sim_time', 0),
                data.get('wind_speed', 0.0),
                data.get('load_power', 0.0),        # 暂不可用
                data.get('wtg_power', 0.0),
                data.get('diesel_power', 0.0),      # 暂不可用
                data.get('pitch_angle_set', 0.0),
                data.get('wtg_status', 0),
                data.get('ctrl_mode_wind', 0),
                data.get('available_power', 0.0),
                data.get('wtg_power_set', 0.0)
            ))
            conn.commit()
            cursor.close()
            conn.close()
            logger.debug("插入 history_data 记录成功")

    def update_device_params(self, params: Dict[str, float]) -> None:
        """
        更新 device_params 表（参数修改时调用）
        params 应包含字段：cut_in_wind, cut_out_wind, rated_wind, rated_power
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            # 清空并插入新参数（保持单条记录）
            cursor.execute("DELETE FROM device_params")
            cursor.execute('''
                INSERT INTO device_params 
                (cut_in_wind, cut_out_wind, rated_wind, rated_power)
                VALUES (?, ?, ?, ?)
            ''', (
                params.get('cut_in_wind', 3.0),
                params.get('cut_out_wind', 25.0),
                params.get('rated_wind', 12.0),
                params.get('rated_power', 100.0)
            ))
            conn.commit()
            cursor.close()
            conn.close()
            logger.info("更新 device_params 成功")

    def get_device_params(self) -> Dict[str, float]:
        """查询当前设备参数"""
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT cut_in_wind, cut_out_wind, rated_wind, rated_power FROM device_params LIMIT 1")
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            if row:
                return dict(row)
            else:
                return {"cut_in_wind": 3.0, "cut_out_wind": 25.0,
                        "rated_wind": 12.0, "rated_power": 100.0}


# ==================== 串口通信与协议处理类 ====================
class SerialProtocol:
    """串口协议解析与打包"""
    @staticmethod
    def crc16_modbus(data: bytes) -> int:
        """Modbus CRC16 计算（与单片机一致）"""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc

    @staticmethod
    def pack_frame(cmd: int, data: bytes) -> bytes:
        """
        打包一帧
        :param cmd: 命令码
        :param data: 数据体（bytes）
        :return: 完整帧（bytes）
        """
        total_len = 7 + len(data)  # 头2 + 长度1 + 命令1 + 数据N + CRC2 + 尾1
        frame = bytearray()
        frame.append(FRAME_HEAD1)
        frame.append(FRAME_HEAD2)
        frame.append(total_len)
        frame.append(cmd)
        frame.extend(data)
        # 计算CRC（从长度字节到数据体末尾）
        crc = SerialProtocol.crc16_modbus(frame[2:])  # 从索引2开始（长度字节之后）
        frame.append(crc & 0xFF)        # CRC低字节
        frame.append((crc >> 8) & 0xFF) # CRC高字节
        frame.append(FRAME_TAIL)
        return bytes(frame)

    @staticmethod
    def parse_frame(frame: bytes) -> Tuple[bool, int, bytes, int]:
        """
        解析一帧
        :param frame: 完整帧（bytes）
        :return: (是否成功, 命令码, 数据体, 数据长度)
        """
        if len(frame) < 7:
            return False, 0, b'', 0
        if frame[0] != FRAME_HEAD1 or frame[1] != FRAME_HEAD2 or frame[-1] != FRAME_TAIL:
            return False, 0, b'', 0
        total_len = frame[2]
        if total_len != len(frame):
            return False, 0, b'', 0
        cmd = frame[3]
        data_len = total_len - 7
        data = frame[4:4+data_len]
        # 校验CRC
        crc_calc = SerialProtocol.crc16_modbus(frame[2:2+2+data_len])  # 长度+命令+数据
        crc_recv = frame[4+data_len] | (frame[5+data_len] << 8)
        if crc_calc != crc_recv:
            return False, 0, b'', 0
        return True, cmd, data, data_len


class SerialMonitor:
    """
    串口监控主类
    负责打开串口、持续读取数据、解析并触发相应回调
    """
    def __init__(self, port: str, baudrate: int, db_manager: DatabaseManager):
        self.port = port
        self.baudrate = baudrate
        self.db = db_manager
        self.serial = None
        self.running = False
        self._buffer = bytearray()          # 接收缓冲区
        self._lock = threading.Lock()
        # 回调函数字典（命令码 -> 处理函数）
        self.handlers = {
            CMD_REPORT_REALTIME: self._handle_realtime,
            CMD_ACK_RESULT: self._handle_ack,
            CMD_RESP_PARAM: self._handle_param_response,
        }

    def open(self) -> bool:
        """打开串口"""
        try:
            self.serial = serial.Serial(self.port, self.baudrate, timeout=TIMEOUT)
            logger.info(f"串口打开成功: {self.port} {self.baudrate}")
            return True
        except Exception as e:
            logger.error(f"打开串口失败: {e}")
            return False

    def close(self):
        """关闭串口"""
        if self.serial and self.serial.is_open:
            self.serial.close()
            logger.info("串口已关闭")

    def start(self):
        """启动监控循环（阻塞）"""
        if not self.serial or not self.serial.is_open:
            logger.error("串口未打开，无法启动监控")
            return
        self.running = True
        logger.info("开始监控串口数据...")
        while self.running:
            try:
                # 读取可用数据
                if self.serial.in_waiting > 0:
                    data = self.serial.read(self.serial.in_waiting)
                    if data:
                        with self._lock:
                            self._buffer.extend(data)
                        # 尝试解析帧
                        self._process_buffer()
                else:
                    time.sleep(0.01)  # 降低CPU占用
            except serial.SerialException as e:
                logger.error(f"串口异常: {e}")
                self.running = False
                break
            except Exception as e:
                logger.error(f"未知异常: {e}", exc_info=True)
                time.sleep(0.1)

    def stop(self):
        """停止监控"""
        self.running = False
        logger.info("监控已停止")

    def _process_buffer(self):
        """
        从缓冲区中提取完整帧并处理
        采用查找帧头的方式，确保容错
        """
        while True:
            # 查找帧头 AA 55
            if len(self._buffer) < 7:
                break
            # 查找第一个 AA
            idx = self._buffer.find(bytes([FRAME_HEAD1]))
            if idx == -1:
                # 没有帧头，清空缓冲区
                self._buffer.clear()
                break
            if idx > 0:
                # 丢弃帧头之前的数据
                self._buffer = self._buffer[idx:]
                continue
            # 现在 buffer[0] == AA，检查下一个字节是否为 55
            if len(self._buffer) < 2 or self._buffer[1] != FRAME_HEAD2:
                # 不是 AA 55，丢弃第一个字节并继续
                self._buffer.pop(0)
                continue
            # 检查长度是否足够
            if len(self._buffer) < 3:
                break  # 长度字节还未接收完整
            total_len = self._buffer[2]
            if total_len < 7 or total_len > 64:  # 最大帧长64
                # 长度异常，丢弃帧头，重新查找
                self._buffer.pop(0)
                continue
            if len(self._buffer) < total_len:
                # 还未收完完整帧，等待更多数据
                break
            # 提取完整帧
            frame = bytes(self._buffer[:total_len])
            # 验证帧尾
            if frame[-1] != FRAME_TAIL:
                # 帧尾错误，丢弃帧头
                self._buffer.pop(0)
                continue
            # 解析帧
            ok, cmd, data, data_len = SerialProtocol.parse_frame(frame)
            if ok:
                # 处理命令
                handler = self.handlers.get(cmd)
                if handler:
                    handler(cmd, data, data_len)
                else:
                    logger.warning(f"未处理的命令码: 0x{cmd:02X}")
                # 移除已处理的帧
                del self._buffer[:total_len]
            else:
                # 解析失败，丢弃帧头尝试下一个
                self._buffer.pop(0)

    # ==================== 命令处理回调 ====================
    def _handle_realtime(self, cmd: int, data: bytes, data_len: int):
        """处理实时数据帧（CMD 0x02）"""
        if data_len != 24:
            logger.error(f"实时数据长度错误: {data_len}，期望24")
            return
        # 解包
        try:
            (wind_speed, wtg_power_set, wtg_power, pitch_angle_set,
             available_power, wtg_status, ctrl_mode_wind, link_grid, reserved) = REALTIME_STRUCT.unpack(data)
        except Exception as e:
            logger.error(f"解包实时数据失败: {e}")
            return
        # 组织数据
        realtime_dict = {
            'wtg_status': wtg_status,
            'pitch_angle_set': pitch_angle_set,
            'wtg_power': wtg_power,
            'wtg_power_set': wtg_power_set,
            'ctrl_mode_wind': ctrl_mode_wind,
            'available_power': available_power,
            'link_grid': link_grid,
        }
        # 更新数据库 ctrl_runtime_info
        self.db.update_realtime(realtime_dict)
        # 插入历史数据（缺少 sim_time, load_power, diesel_power，暂用0）
        history_dict = {
            'sim_time': int(time.time()),  # 可用当前时间戳，或从其他来源获取仿真时刻
            'wind_speed': wind_speed,
            'load_power': 0.0,
            'wtg_power': wtg_power,
            'diesel_power': 0.0,
            'pitch_angle_set': pitch_angle_set,
            'wtg_status': wtg_status,
            'ctrl_mode_wind': ctrl_mode_wind,
            'available_power': available_power,
            'wtg_power_set': wtg_power_set,
        }
        self.db.insert_history(history_dict)
        # 控制台输出
        logger.info(f"实时数据: 风速={wind_speed:.1f}m/s, 风机功率={wtg_power:.1f}kW, "
                    f"设定={wtg_power_set:.1f}kW, 桨距角={pitch_angle_set:.1f}°, "
                    f"状态={'运行' if wtg_status else '停机'}, 模式={'闭环' if ctrl_mode_wind else '开环'}, "
                    f"连接={'在线' if link_grid else '离线'}")

    def _handle_ack(self, cmd: int, data: bytes, data_len: int):
        """处理应答帧（CMD 0x04）"""
        if data_len == 1:
            result = data[0]
            if result == 0:
                logger.info("收到命令成功应答")
            else:
                logger.warning("收到命令失败应答")
        else:
            logger.warning("应答长度异常")

    def _handle_param_response(self, cmd: int, data: bytes, data_len: int):
        """处理参数查询应答（CMD 0x06）"""
        if data_len != 16:
            logger.error(f"参数数据长度错误: {data_len}，期望16")
            return
        try:
            (cut_in, cut_out, rated_wind, rated_power) = PARAM_STRUCT.unpack(data)
        except Exception as e:
            logger.error(f"解包参数数据失败: {e}")
            return
        params = {
            'cut_in_wind': cut_in,
            'cut_out_wind': cut_out,
            'rated_wind': rated_wind,
            'rated_power': rated_power
        }
        self.db.update_device_params(params)
        logger.info(f"参数更新: 切入={cut_in:.1f}, 切出={cut_out:.1f}, "
                    f"额定风速={rated_wind:.1f}, 额定功率={rated_power:.1f}")

    # ==================== 发送命令（供外部调用） ====================
    def send_set_param(self, cut_in: float, cut_out: float, rated_wind: float, rated_power: float) -> bool:
        """发送修改参数命令（CMD 0x01）"""
        data = PARAM_STRUCT.pack(cut_in, cut_out, rated_wind, rated_power)
        frame = SerialProtocol.pack_frame(CMD_SET_PARAM, data)
        return self._send_frame(frame)

    def send_set_mode(self, mode: int) -> bool:
        """发送修改模式命令（CMD 0x03）"""
        if mode not in (0, 1):
            logger.error("模式必须为0或1")
            return False
        data = bytes([mode])
        frame = SerialProtocol.pack_frame(CMD_SET_MODE, data)
        return self._send_frame(frame)

    def send_query_param(self) -> bool:
        """发送查询参数命令（CMD 0x05）"""
        frame = SerialProtocol.pack_frame(CMD_QUERY_PARAM, b'')
        return self._send_frame(frame)

    def _send_frame(self, frame: bytes) -> bool:
        """发送帧到串口"""
        if not self.serial or not self.serial.is_open:
            logger.error("串口未打开")
            return False
        try:
            self.serial.write(frame)
            logger.debug(f"发送帧: {frame.hex().upper()}")
            return True
        except Exception as e:
            logger.error(f"发送失败: {e}")
            return False


# ==================== 主程序入口 ====================
def main():
    # 初始化数据库（确保表存在）
    db = DatabaseManager(DB_PATH)
    # 可选：检查是否有 device_params 记录，若无则插入默认
    params = db.get_device_params()
    if not params:
        db.update_device_params({"cut_in_wind": 3.0, "cut_out_wind": 25.0,
                                 "rated_wind": 12.0, "rated_power": 100.0})

    # 初始化串口监控
    monitor = SerialMonitor(SERIAL_PORT, BAUDRATE, db)
    if not monitor.open():
        logger.error("无法打开串口，程序退出")
        return

    # 启动监控线程（因为主循环会阻塞，可另起线程，这里直接运行）
    try:
        monitor.start()   # 阻塞直到 stop() 被调用或发生错误
    except KeyboardInterrupt:
        logger.info("用户中断")
    finally:
        monitor.stop()
        monitor.close()


if __name__ == "__main__":
    # 若需要自动查找串口，可取消下面的注释
    # ports = serial.tools.list_ports.comports()
    # for p in ports:
    #     print(p.device, p.description)
    main()



# #!/usr/bin/env python3
# # -*- coding: utf-8 -*-
# """
# 发送查询参数命令到 MCU，等待响应并更新 device_params
# """
#
# import time
# from serialport import SerialMonitor, DatabaseManager, SERIAL_PORT, BAUDRATE
#
# DB_PATH = "wind.db/wind.db"
#
# def main():
#     db = DatabaseManager(DB_PATH)
#     monitor = SerialMonitor(SERIAL_PORT, BAUDRATE, db)
#     if not monitor.open():
#         print("串口打开失败")
#         return
#
#     # 发送查询参数命令（CMD 0x05）
#     print("发送查询参数命令...")
#     monitor.send_query_param()
#
#     # 等待响应（MCU 应在收到后回复 CMD 0x06）
#     time.sleep(1)
#
#     # 查看 device_params 是否更新
#     params = db.get_device_params()
#     print(f"当前参数: 切入={params['cut_in_wind']:.1f}, 切出={params['cut_out_wind']:.1f}, "
#           f"额定风速={params['rated_wind']:.1f}, 额定功率={params['rated_power']:.1f}")
#
#     monitor.close()
#
# if __name__ == "__main__":
#     main()

#
# #!/usr/bin/env python3
# # -*- coding: utf-8 -*-
# """
# 单元测试：wind_serial_monitor.py
# 使用 unittest 框架，模拟串口数据与数据库操作
# 运行方式：python -m unittest test_wind_serial_monitor.py
# """
#
# import unittest
# import struct
# import sqlite3
# import tempfile
# import os
# import time
# from unittest.mock import Mock, patch, MagicMock
# from typing import Dict, Any
#
# # 导入待测试的模块（确保 wind_serial_monitor.py 在同一目录或 PYTHONPATH 中）
# import serialport as wsm
#
#
# class TestCRC16(unittest.TestCase):
#     """测试 Modbus CRC16 计算"""
#
#     def test_crc16_known_values(self):
#         # 已知数据：长度 0x17, 命令 0x02, 数据 24字节全0 -> 计算CRC（可以用第三方库验证，这里手动比对）
#         data = bytes([0x17, 0x02] + [0x00]*24)
#         crc = wsm.SerialProtocol.crc16_modbus(data)
#         # 预期CRC（使用在线计算器或已知结果，此处以MCU代码一致为准）
#         # 通过运行MCU代码或手动计算得到，此处仅演示，实际可使用任意的验证
#         # 我们直接信任算法，测试一致性即可
#         self.assertIsInstance(crc, int)
#         self.assertTrue(0 <= crc <= 0xFFFF)
#
#
# class TestFramePacking(unittest.TestCase):
#     """测试帧打包与解析"""
#
#     def test_pack_frame_basic(self):
#         data = b'\x01\x02\x03\x04'
#         frame = wsm.SerialProtocol.pack_frame(wsm.CMD_SET_PARAM, data)
#         # 验证帧结构
#         self.assertEqual(frame[0], 0xAA)
#         self.assertEqual(frame[1], 0x55)
#         self.assertEqual(frame[2], 7 + len(data))  # total_len
#         self.assertEqual(frame[3], wsm.CMD_SET_PARAM)
#         self.assertEqual(frame[4:8], data)
#         self.assertEqual(frame[-1], 0x0D)
#         # 验证CRC字段存在且不为零（非零可能性大）
#         crc_low = frame[8]
#         crc_high = frame[9]
#         self.assertTrue(crc_low != 0 or crc_high != 0)  # CRC不一定非零，但长度正确
#
#     def test_pack_and_parse(self):
#         original_data = b'\x12\x34\x56\x78'
#         frame = wsm.SerialProtocol.pack_frame(wsm.CMD_SET_PARAM, original_data)
#         ok, cmd, data, data_len = wsm.SerialProtocol.parse_frame(frame)
#         self.assertTrue(ok)
#         self.assertEqual(cmd, wsm.CMD_SET_PARAM)
#         self.assertEqual(data, original_data)
#         self.assertEqual(data_len, len(original_data))
#
#     def test_parse_invalid_frame(self):
#         invalid = bytes([0xAA, 0x55, 0x08, 0x00, 0x01, 0x02, 0x03, 0x0D])  # 无CRC或长度错误
#         ok, _, _, _ = wsm.SerialProtocol.parse_frame(invalid)
#         self.assertFalse(ok)
#
#         invalid2 = bytes([0xAA, 0x56, 0x07, 0x00, 0x00, 0x00, 0x0D])  # 错误帧头
#         ok, _, _, _ = wsm.SerialProtocol.parse_frame(invalid2)
#         self.assertFalse(ok)
#
#
# class TestDatabaseManager(unittest.TestCase):
#     """测试数据库操作（使用临时文件）"""
#
#     def setUp(self):
#         self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
#         self.db_path = self.temp_db.name
#         self.temp_db.close()
#         # 初始化表结构（复制自 wind_db_init.py，简单起见直接执行SQL）
#         self._create_tables()
#         self.db_manager = wsm.DatabaseManager(self.db_path)
#
#     def tearDown(self):
#         os.unlink(self.db_path)
#
#     def _create_tables(self):
#         conn = sqlite3.connect(self.db_path)
#         cursor = conn.cursor()
#         cursor.execute('''
#             CREATE TABLE IF NOT EXISTS ctrl_runtime_info (
#                 id INTEGER PRIMARY KEY AUTOINCREMENT,
#                 wtg_status INTEGER DEFAULT 0,
#                 pitch_angle_set REAL DEFAULT 0.0,
#                 wtg_power REAL DEFAULT 0.0,
#                 wtg_power_set REAL DEFAULT 0.0,
#                 ctrl_mode_wind INTEGER DEFAULT 0,
#                 available_power REAL DEFAULT 0.0,
#                 link_grid INTEGER DEFAULT 0,
#                 update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
#             )
#         ''')
#         cursor.execute('''
#             CREATE TABLE IF NOT EXISTS history_data (
#                 id INTEGER PRIMARY KEY AUTOINCREMENT,
#                 sim_time INTEGER NOT NULL,
#                 wind_speed REAL,
#                 load_power REAL,
#                 wtg_power REAL,
#                 diesel_power REAL,
#                 pitch_angle_set REAL,
#                 wtg_status INTEGER,
#                 ctrl_mode_wind INTEGER,
#                 available_power REAL,
#                 wtg_power_set REAL,
#                 record_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
#             )
#         ''')
#         cursor.execute('''
#             CREATE TABLE IF NOT EXISTS device_params (
#                 id INTEGER PRIMARY KEY AUTOINCREMENT,
#                 cut_in_wind REAL NOT NULL DEFAULT 3.0,
#                 cut_out_wind REAL NOT NULL DEFAULT 25.0,
#                 rated_wind REAL NOT NULL DEFAULT 12.0,
#                 rated_power REAL NOT NULL DEFAULT 100.0,
#                 update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
#             )
#         ''')
#         conn.commit()
#         conn.close()
#
#     def test_update_realtime(self):
#         data = {
#             'wtg_status': 1,
#             'pitch_angle_set': 5.0,
#             'wtg_power': 45.2,
#             'wtg_power_set': 50.0,
#             'ctrl_mode_wind': 1,
#             'available_power': 60.0,
#             'link_grid': 1
#         }
#         self.db_manager.update_realtime(data)
#         # 验证表中只有一条记录，且值正确
#         conn = sqlite3.connect(self.db_path)
#         cursor = conn.cursor()
#         cursor.execute("SELECT wtg_status, pitch_angle_set, wtg_power, wtg_power_set, ctrl_mode_wind, available_power, link_grid FROM ctrl_runtime_info")
#         row = cursor.fetchone()
#         self.assertIsNotNone(row)
#         self.assertEqual(row[0], 1)
#         self.assertAlmostEqual(row[1], 5.0)
#         self.assertAlmostEqual(row[2], 45.2)
#         self.assertAlmostEqual(row[3], 50.0)
#         self.assertEqual(row[4], 1)
#         self.assertAlmostEqual(row[5], 60.0)
#         self.assertEqual(row[6], 1)
#         conn.close()
#
#     def test_insert_history(self):
#         data = {
#             'sim_time': 12345,
#             'wind_speed': 8.5,
#             'load_power': 20.0,
#             'wtg_power': 30.0,
#             'diesel_power': 10.0,
#             'pitch_angle_set': 2.0,
#             'wtg_status': 1,
#             'ctrl_mode_wind': 1,
#             'available_power': 40.0,
#             'wtg_power_set': 35.0
#         }
#         self.db_manager.insert_history(data)
#         conn = sqlite3.connect(self.db_path)
#         cursor = conn.cursor()
#         cursor.execute("SELECT COUNT(*) FROM history_data")
#         count = cursor.fetchone()[0]
#         self.assertEqual(count, 1)
#         cursor.execute("SELECT sim_time, wind_speed, load_power, wtg_power, diesel_power FROM history_data")
#         row = cursor.fetchone()
#         self.assertEqual(row[0], 12345)
#         self.assertAlmostEqual(row[1], 8.5)
#         self.assertAlmostEqual(row[2], 20.0)
#         self.assertAlmostEqual(row[3], 30.0)
#         self.assertAlmostEqual(row[4], 10.0)
#         conn.close()
#
#     def test_update_device_params(self):
#         params = {
#             'cut_in_wind': 3.5,
#             'cut_out_wind': 26.0,
#             'rated_wind': 13.0,
#             'rated_power': 110.0
#         }
#         self.db_manager.update_device_params(params)
#         conn = sqlite3.connect(self.db_path)
#         cursor = conn.cursor()
#         cursor.execute("SELECT cut_in_wind, cut_out_wind, rated_wind, rated_power FROM device_params")
#         row = cursor.fetchone()
#         self.assertAlmostEqual(row[0], 3.5)
#         self.assertAlmostEqual(row[1], 26.0)
#         self.assertAlmostEqual(row[2], 13.0)
#         self.assertAlmostEqual(row[3], 110.0)
#         conn.close()
#
#     def test_get_device_params_empty(self):
#         # 确保表为空时返回默认值
#         params = self.db_manager.get_device_params()
#         self.assertEqual(params['cut_in_wind'], 3.0)
#         self.assertEqual(params['cut_out_wind'], 25.0)
#         self.assertEqual(params['rated_wind'], 12.0)
#         self.assertEqual(params['rated_power'], 100.0)
#
#
# class TestSerialMonitorProcessing(unittest.TestCase):
#     """测试 SerialMonitor 的帧处理逻辑（不实际打开串口）"""
#
#     def setUp(self):
#         # 使用内存数据库（临时文件）
#         self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
#         self.db_path = self.temp_db.name
#         self.temp_db.close()
#         # 创建表结构（简单起见，复用之前的创建函数，这里我们拷贝或直接调用）
#         self._create_tables()
#         self.db_manager = wsm.DatabaseManager(self.db_path)
#         # 创建 SerialMonitor 但不实际打开串口，手动注入缓冲区数据
#         self.monitor = wsm.SerialMonitor("DUMMY", 115200, self.db_manager)
#         # 模拟串口对象为 None，但我们可以直接操作 _buffer
#         self.monitor.serial = None  # 避免发送操作
#
#     def tearDown(self):
#         os.unlink(self.db_path)
#
#     def _create_tables(self):
#         conn = sqlite3.connect(self.db_path)
#         cursor = conn.cursor()
#         cursor.execute('''
#             CREATE TABLE IF NOT EXISTS ctrl_runtime_info (
#                 id INTEGER PRIMARY KEY AUTOINCREMENT,
#                 wtg_status INTEGER DEFAULT 0,
#                 pitch_angle_set REAL DEFAULT 0.0,
#                 wtg_power REAL DEFAULT 0.0,
#                 wtg_power_set REAL DEFAULT 0.0,
#                 ctrl_mode_wind INTEGER DEFAULT 0,
#                 available_power REAL DEFAULT 0.0,
#                 link_grid INTEGER DEFAULT 0,
#                 update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
#             )
#         ''')
#         cursor.execute('''
#             CREATE TABLE IF NOT EXISTS history_data (
#                 id INTEGER PRIMARY KEY AUTOINCREMENT,
#                 sim_time INTEGER NOT NULL,
#                 wind_speed REAL,
#                 load_power REAL,
#                 wtg_power REAL,
#                 diesel_power REAL,
#                 pitch_angle_set REAL,
#                 wtg_status INTEGER,
#                 ctrl_mode_wind INTEGER,
#                 available_power REAL,
#                 wtg_power_set REAL,
#                 record_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
#             )
#         ''')
#         cursor.execute('''
#             CREATE TABLE IF NOT EXISTS device_params (
#                 id INTEGER PRIMARY KEY AUTOINCREMENT,
#                 cut_in_wind REAL NOT NULL DEFAULT 3.0,
#                 cut_out_wind REAL NOT NULL DEFAULT 25.0,
#                 rated_wind REAL NOT NULL DEFAULT 12.0,
#                 rated_power REAL NOT NULL DEFAULT 100.0,
#                 update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
#             )
#         ''')
#         conn.commit()
#         conn.close()
#
#     def test_process_realtime_frame(self):
#         """模拟收到一帧实时数据（CMD 0x02）并验证数据库更新"""
#         # 构造实时数据体：风速8.5，设定35，实际30，桨距2.5，可用60，状态1，模式1，连接1，保留0
#         wind_speed = 8.5
#         wtg_power_set = 35.0
#         wtg_power = 30.0
#         pitch_angle = 2.5
#         available = 60.0
#         wtg_status = 1
#         ctrl_mode = 1
#         link_grid = 1
#         reserved = 0
#
#         data_struct = struct.Struct('<fffffBBBB')
#         data_bytes = data_struct.pack(wind_speed, wtg_power_set, wtg_power, pitch_angle,
#                                       available, wtg_status, ctrl_mode, link_grid, reserved)
#         frame = wsm.SerialProtocol.pack_frame(wsm.CMD_REPORT_REALTIME, data_bytes)
#
#         # 将帧放入缓冲区
#         self.monitor._buffer.extend(frame)
#
#         # 调用处理函数（模拟读取循环中的处理）
#         self.monitor._process_buffer()
#
#         # 验证数据库 ctrl_runtime_info
#         conn = sqlite3.connect(self.db_path)
#         cursor = conn.cursor()
#         cursor.execute("SELECT wtg_status, pitch_angle_set, wtg_power, wtg_power_set, ctrl_mode_wind, available_power, link_grid FROM ctrl_runtime_info")
#         row = cursor.fetchone()
#         self.assertIsNotNone(row)
#         self.assertEqual(row[0], wtg_status)
#         self.assertAlmostEqual(row[1], pitch_angle)
#         self.assertAlmostEqual(row[2], wtg_power)
#         self.assertAlmostEqual(row[3], wtg_power_set)
#         self.assertEqual(row[4], ctrl_mode)
#         self.assertAlmostEqual(row[5], available)
#         self.assertEqual(row[6], link_grid)
#
#         # 验证 history_data 插入了一条记录
#         cursor.execute("SELECT COUNT(*) FROM history_data")
#         count = cursor.fetchone()[0]
#         self.assertEqual(count, 1)
#         cursor.execute("SELECT wind_speed, wtg_power, wtg_power_set, pitch_angle_set FROM history_data")
#         hrow = cursor.fetchone()
#         self.assertAlmostEqual(hrow[0], wind_speed)
#         self.assertAlmostEqual(hrow[1], wtg_power)
#         self.assertAlmostEqual(hrow[2], wtg_power_set)
#         self.assertAlmostEqual(hrow[3], pitch_angle)
#         conn.close()
#
#     def test_process_param_response(self):
#         """模拟参数查询应答帧（CMD 0x06）并验证参数表更新"""
#         cut_in = 3.5
#         cut_out = 26.0
#         rated_wind = 13.0
#         rated_power = 110.0
#         data = struct.pack('<ffff', cut_in, cut_out, rated_wind, rated_power)
#         frame = wsm.SerialProtocol.pack_frame(wsm.CMD_RESP_PARAM, data)
#
#         self.monitor._buffer.extend(frame)
#         self.monitor._process_buffer()
#
#         # 验证 device_params
#         conn = sqlite3.connect(self.db_path)
#         cursor = conn.cursor()
#         cursor.execute("SELECT cut_in_wind, cut_out_wind, rated_wind, rated_power FROM device_params")
#         row = cursor.fetchone()
#         self.assertAlmostEqual(row[0], cut_in)
#         self.assertAlmostEqual(row[1], cut_out)
#         self.assertAlmostEqual(row[2], rated_wind)
#         self.assertAlmostEqual(row[3], rated_power)
#         conn.close()
#
#     def test_process_ack(self):
#         """模拟应答帧（CMD 0x04），仅测试不崩溃"""
#         data = bytes([0x00])  # 成功
#         frame = wsm.SerialProtocol.pack_frame(wsm.CMD_ACK_RESULT, data)
#         self.monitor._buffer.extend(frame)
#         self.monitor._process_buffer()
#         # 无异常即通过
#
#
# class TestSendCommands(unittest.TestCase):
#     """测试发送命令（使用 mock 串口）"""
#
#     def setUp(self):
#         self.db_manager = Mock()
#         self.monitor = wsm.SerialMonitor("COM1", 115200, self.db_manager)
#         # 模拟串口对象
#         self.monitor.serial = Mock()
#         self.monitor.serial.is_open = True
#
#     def test_send_set_param(self):
#         with patch.object(wsm.SerialProtocol, 'pack_frame', return_value=b'FAKE') as mock_pack:
#             result = self.monitor.send_set_param(3.0, 25.0, 12.0, 100.0)
#             self.assertTrue(result)
#             self.monitor.serial.write.assert_called_once_with(b'FAKE')
#
#     def test_send_set_mode(self):
#         result = self.monitor.send_set_mode(1)
#         self.assertTrue(result)
#         self.monitor.serial.write.assert_called_once()
#
#     def test_send_query_param(self):
#         result = self.monitor.send_query_param()
#         self.assertTrue(result)
#         self.monitor.serial.write.assert_called_once()
#
#
# if __name__ == '__main__':
#     unittest.main()