#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
南极考察站微电网 —— 风机控制器上位机串口监控程序
适配修正后的数据库表结构（history_data 包含 link_grid）
功能：
1. 通过串口接收单片机上报的实时运行数据（CMD 0x02）
2. 解析数据并更新数据库 ctrl_runtime_info 表（最新值）
3. 将每条记录插入 history_data 表（历史数据，含 link_grid）
4. 支持发送修改参数（CMD 0x01）、修改模式（CMD 0x03）、查询参数（CMD 0x05）命令
5. 自动检测 device_params 表变化并下发到单片机
6. 【新增】周期性向单片机查询风机参数（CMD 0x05），收到应答（CMD 0x06）后
   回写 device_params 表，使上位机界面自动显示单片机中的真实参数
7. 日志记录与错误处理

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
PARAMS_CHECK_INTERVAL = 1.0             # 参数检查周期（秒）
PARAM_QUERY_INTERVAL = 5.0              # 周期性向单片机查询参数（CMD 0x05）的间隔（秒）
PARAM_PENDING_TIMEOUT = 3.0             # 下发参数后等待单片机生效的确认窗口（秒）
PARAM_EPS = 1e-3                        # 参数比较容差（单片机 float 为单精度，回读值可能有微小偏差）

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
            # 先清空表，再插入新记录（保证单条）
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
        向 history_data 表插入一条历史记录（适配修正后的表结构）
        data 应包含字段：timestamp, wind_speed, wtg_power_set, wtg_power,
                         pitch_angle_set, available_power, wtg_status,
                         ctrl_mode_wind, link_grid
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO history_data 
                (timestamp, wind_speed, wtg_power_set, wtg_power,
                 pitch_angle_set, available_power, wtg_status,
                 ctrl_mode_wind, link_grid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                data.get('timestamp', int(time.time())),
                data.get('wind_speed', 0.0),
                data.get('wtg_power_set', 0.0),
                data.get('wtg_power', 0.0),
                data.get('pitch_angle_set', 0.0),
                data.get('available_power', 0.0),
                data.get('wtg_status', 0),
                data.get('ctrl_mode_wind', 0),
                data.get('link_grid', 0)
            ))
            conn.commit()
            cursor.close()
            conn.close()
            logger.debug("插入 history_data 记录成功")

    def update_device_params(self, params: Dict[str, float]) -> None:
        """
        更新 device_params 表（参数修改时调用）
        params 应包含字段：cut_in_wind, cut_out_wind, rated_wind, rated_power
        update_time 显式写入本地时间，便于界面显示"最后同步时刻"
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            # 清空并插入新参数（保持单条记录）
            cursor.execute("DELETE FROM device_params")
            cursor.execute('''
                INSERT INTO device_params 
                (cut_in_wind, cut_out_wind, rated_wind, rated_power, update_time)
                VALUES (?, ?, ?, ?, datetime('now','localtime'))
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

    def write_log(self, level: str, source: str, message: str) -> None:
        """写入 sys_log 表（参数下发/回读等事件追溯）"""
        with self._lock:
            try:
                conn = self._get_conn()
                conn.execute(
                    "INSERT INTO sys_log (level, source, message) VALUES (?, ?, ?)",
                    (level, source, message))
                conn.commit()
                conn.close()
            except sqlite3.Error as e:
                logger.warning(f"写日志失败: {e}")

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
                # 如果表为空，返回默认值
                return {"cut_in_wind": 3.0, "cut_out_wind": 25.0,
                        "rated_wind": 12.0, "rated_power": 100.0}

    def pop_ui_cmds(self) -> list:
        """
        取出并清空 ui_cmd 表中的待处理命令（由风电上位机 wind_ui.py 写入）
        返回 [{'cmd': 'set_mode', 'value': 1}, ...] 等命令列表
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT id, cmd, value FROM ui_cmd ORDER BY id")
                rows = cursor.fetchall()
                if rows:
                    cursor.execute("DELETE FROM ui_cmd")
                    conn.commit()
            except sqlite3.OperationalError:
                rows = []  # 表不存在（旧版数据库未创建）
            cursor.close()
            conn.close()
        return [dict(r) for r in rows]


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
        # 参数自动下发相关
        self._last_params = None
        self._last_check_time = 0
        self._params_check_interval = PARAMS_CHECK_INTERVAL
        # 参数周期回读相关（CMD 0x05 / 0x06）
        self._last_param_query = 0.0        # 上次查询时间
        self._pending_params = None         # 已下发、等待单片机生效确认的参数
        self._pending_deadline = 0.0        # 确认窗口截止时间

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
        # 初始化参数缓存
        self._last_params = self.db.get_device_params()
        self._last_check_time = time.time()
        self._last_param_query = 0.0        # 置 0 以便启动后立即回读一次参数
        logger.info("开始监控串口数据...")
        while self.running:
            try:
                # 读取可用数据
                if self.serial.in_waiting > 0:
                    data = self.serial.read(self.serial.in_waiting)
                    if data:
                        with self._lock:
                            self._buffer.extend(data)
                        self._process_buffer()
                # 定期检查参数变化及上位机命令
                now = time.time()
                if now - self._last_check_time >= self._params_check_interval:
                    self._check_and_send_params()
                    self._process_ui_cmds()
                    self._query_params_periodically()
                    self._last_check_time = now
                # 适当休眠
                time.sleep(0.01)
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
        # 组织数据字典
        realtime_dict = {
            'wtg_status': wtg_status,
            'pitch_angle_set': pitch_angle_set,
            'wtg_power': wtg_power,
            'wtg_power_set': wtg_power_set,
            'ctrl_mode_wind': ctrl_mode_wind,
            'available_power': available_power,
            'link_grid': link_grid,
        }
        # 更新 ctrl_runtime_info
        self.db.update_realtime(realtime_dict)

        # 插入历史数据（包含所有字段）
        history_dict = {
            'timestamp': int(time.time()),          # 当前时间戳
            'wind_speed': wind_speed,
            'wtg_power_set': wtg_power_set,
            'wtg_power': wtg_power,
            'pitch_angle_set': pitch_angle_set,
            'available_power': available_power,
            'wtg_status': wtg_status,
            'ctrl_mode_wind': ctrl_mode_wind,
            'link_grid': link_grid,
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
        """
        处理参数查询应答（CMD 0x06）：单片机回读的风机参数写入 device_params，
        上位机界面据此周期刷新，实现"参数从单片机返回并显示"。
        """
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

        # 若本地下发刚发出且仍在确认窗口内：等待单片机生效，暂不采信回读值，
        # 否则会把刚下发的参数又覆盖回旧值（并触发无意义的重发）。
        if self._pending_params is not None:
            if time.time() < self._pending_deadline:
                if self._params_equal(params, self._pending_params):
                    logger.info("单片机已确认新参数（回读值一致）")
                    self.db.write_log("INFO", "serial",
                                      "单片机回读确认新参数已生效")
                    self._pending_params = None
                else:
                    logger.info("回读值与刚下发的参数不一致，暂忽略（等待单片机生效）")
                    return
            else:
                logger.warning("下发参数超过确认窗口未被单片机采纳，以回读值为准")
                self.db.write_log("WARNING", "serial",
                                  "下发参数超时未被单片机采纳，已按回读值更新")
                self._pending_params = None

        self.db.update_device_params(params)
        # 更新本地缓存，以免重复发送
        self._last_params = params
        logger.info(f"参数回读更新: 切入={cut_in:.1f}, 切出={cut_out:.1f}, "
                    f"额定风速={rated_wind:.1f}, 额定功率={rated_power:.1f}")
        self.db.write_log("INFO", "serial",
                          f"从单片机回读参数: 切入={cut_in:g}, 切出={cut_out:g}, "
                          f"额定风速={rated_wind:g}, 额定功率={rated_power:g}")

    @staticmethod
    def _params_equal(a: Dict[str, float], b: Dict[str, float]) -> bool:
        """带容差比较两组风机参数（单片机为单精度浮点，回读值可能有微小偏差）"""
        for k in ('cut_in_wind', 'cut_out_wind', 'rated_wind', 'rated_power'):
            try:
                if abs(float(a.get(k, 0.0)) - float(b.get(k, 0.0))) > PARAM_EPS:
                    return False
            except (TypeError, ValueError):
                return False
        return True

    # ==================== 上位机命令转发 ====================
    def _process_ui_cmds(self):
        """处理上位机 wind_ui.py 写入 ui_cmd 表的命令并经串口转发"""
        for row in self.db.pop_ui_cmds():
            cmd = row.get('cmd')
            value = row.get('value', 0)
            if cmd == 'set_mode':
                ok = self.send_set_mode(int(value))
                logger.info(f"下发控制模式命令({'闭环' if value else '开环'}): "
                            f"{'成功' if ok else '失败'}")
            elif cmd == 'query_params':
                ok = self.send_query_param()
                logger.info(f"下发参数查询命令(CMD 0x05): {'成功' if ok else '失败'}")
            else:
                logger.warning(f"未知的上位机命令: {cmd}")

    # ==================== 参数自动下发 ====================
    def _check_and_send_params(self):
        """检查 device_params 表是否有变化，若有则发送到单片机（CMD 0x01）"""
        current = self.db.get_device_params()
        if current is None:
            return
        # 与缓存比较（带容差，避免 float 精度差异导致反复重发）
        if self._last_params is None:
            self._last_params = current
            return
        if not self._params_equal(current, self._last_params):
            # 发送新参数
            ok = self.send_set_param(
                current['cut_in_wind'],
                current['cut_out_wind'],
                current['rated_wind'],
                current['rated_power']
            )
            if ok:
                self._last_params = current
                # 进入确认窗口：期间忽略回读值，等单片机采纳后再采信
                self._pending_params = dict(current)
                self._pending_deadline = time.time() + PARAM_PENDING_TIMEOUT
                logger.info("自动下发新参数到单片机成功")
                self.db.write_log("INFO", "serial",
                                  f"下发风机参数: 切入={current['cut_in_wind']:g}, "
                                  f"切出={current['cut_out_wind']:g}, "
                                  f"额定风速={current['rated_wind']:g}, "
                                  f"额定功率={current['rated_power']:g}")
            else:
                logger.warning("自动下发参数失败，保留原缓存")

    # ==================== 参数周期回读 ====================
    def _query_params_periodically(self):
        """
        周期性向单片机发送参数查询（CMD 0x05），单片机以 CMD 0x06 回读，
        经 _handle_param_response 写入 device_params，供上位机界面显示。
        """
        # 正在等待下发确认时不查询，避免与下发流程互相干扰
        if self._pending_params is not None:
            return
        now = time.time()
        if now - self._last_param_query >= PARAM_QUERY_INTERVAL:
            if self.send_query_param():
                self._last_param_query = now
                logger.debug("已发送参数查询命令（CMD 0x05）")

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
    # 确保数据库目录存在
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)

    # 初始化数据库（如果表不存在则创建，但这里假定已由 db_init.py 完成）
    db = DatabaseManager(DB_PATH)
    # 确保 device_params 有默认值（如果表为空）
    params = db.get_device_params()
    if not params:
        db.update_device_params({"cut_in_wind": 3.0, "cut_out_wind": 25.0,
                                 "rated_wind": 12.0, "rated_power": 100.0})

    # 初始化串口监控
    monitor = SerialMonitor(SERIAL_PORT, BAUDRATE, db)
    if not monitor.open():
        logger.error("无法打开串口，程序退出")
        return

    # 启动监控（阻塞）
    try:
        monitor.start()
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