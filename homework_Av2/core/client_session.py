"""
TCP客户端会话管理

特性：
- 带接收缓冲区，解决粘包/半包问题
- 线程安全发送（send_json）
- 一次性取出所有完整报文（recv_all_json）
"""
import json
import socket
import threading
import time

BUFFER_SIZE = 4096


class ClientSession:
    """TCP客户端会话（带缓冲区）"""

    def __init__(self, conn, addr, role=None):
        self.conn = conn
        self.addr = addr
        self.role = role  # 'ems' 或 'wind_ctrl'
        self.connected = True
        self.last_heartbeat = time.time()
        self.last_seq = 0
        self.hello_received = False
        self.lock = threading.Lock()
        self.buffer = b""
        self.recv_timeout = 0.2

    def send_json(self, data):
        """
        发送 JSON 报文（自动添加换行符）

        参数:
            data: 要发送的字典数据

        返回:
            bool: 是否发送成功
        """
        try:
            json_str = json.dumps(data, ensure_ascii=False) + '\n'
            with self.lock:
                self.conn.sendall(json_str.encode('utf-8'))
            return True
        except Exception:
            self.connected = False
            return False

    def recv_all_json(self, timeout=None):
        """
        从缓冲区取出所有完整报文

        原理：
            1. 从 socket 读取数据，追加到缓冲区
            2. 按换行符 \n 分割缓冲区
            3. 将所有完整行解析为 JSON 对象，返回列表
            4. 未完整接收的行保留在缓冲区

        参数:
            timeout: 超时时间（秒），None 使用默认值

        返回:
            list: JSON 对象列表（可能为空）
        """
        if timeout is not None:
            self.conn.settimeout(timeout)
        else:
            self.conn.settimeout(self.recv_timeout)

        while self.connected:
            try:
                chunk = self.conn.recv(BUFFER_SIZE)
                if not chunk:
                    self.connected = False
                    return []
                self.buffer += chunk
            except socket.timeout:
                # 超时后检查缓冲区是否有积压
                if b'\n' in self.buffer:
                    lines = self.buffer.split(b'\n')
                    self.buffer = b""
                    result = []
                    for line in lines:
                        line = line.strip()
                        if line:
                            try:
                                result.append(json.loads(line.decode('utf-8')))
                            except json.JSONDecodeError:
                                continue
                    return result
                return []
            except Exception:
                self.connected = False
                return []

            # 有数据时，取出所有完整行
            if b'\n' in self.buffer:
                lines = self.buffer.split(b'\n')
                self.buffer = b""
                result = []
                for line in lines:
                    line = line.strip()
                    if line:
                        try:
                            result.append(json.loads(line.decode('utf-8')))
                        except json.JSONDecodeError:
                            continue
                return result
        return []