"""
TCP服务器：双端口监听

- 8888: EMS 主站
- 8088: 风机控制器

特性：
- 双端口非阻塞监听
- 每个客户端独立线程处理
- 心跳超时检测（15秒）
"""
import socket
import threading
import time
from . import PORT_EMS, PORT_WIND
from .client_session import ClientSession
from .message_handler import MessageHandler


class TCPServer:
    """TCP 服务器（双端口）"""

    def __init__(self, db, engine, log_callback=None):
        """
        参数:
            db: GridDB 实例
            engine: ComputeEngine 实例
            log_callback: 日志回调函数
        """
        self.db = db
        self.engine = engine
        self.log = log_callback or print
        self.running = True
        self.server_socket_ems = None
        self.server_socket_wind = None
        self.clients = {}
        self.client_lock = threading.Lock()
        self.handler = MessageHandler(db, engine, self.clients, self.client_lock, log_callback)
        self._heartbeat_thread = None

    def start(self):
        """启动 TCP 服务器"""
        try:
            self._start_server()
        except Exception as e:
            self.log(f"TCP Server 启动失败: {e}", "ERROR")

    def _start_server(self):
        # EMS 端口
        self.server_socket_ems = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket_ems.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket_ems.bind(('0.0.0.0', PORT_EMS))
        self.server_socket_ems.listen(5)
        self.server_socket_ems.settimeout(0.5)

        # 风机端口
        self.server_socket_wind = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket_wind.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket_wind.bind(('0.0.0.0', PORT_WIND))
        self.server_socket_wind.listen(5)
        self.server_socket_wind.settimeout(0.5)

        self.log(f"TCP Server 启动: EMS 监听 {PORT_EMS}, 风机控制器监听 {PORT_WIND}")

        # 启动心跳监控
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_monitor, daemon=True)
        self._heartbeat_thread.start()

        # 主循环
        while self.running:
            self._accept_connections()

    def _accept_connections(self):
        """接受连接（非阻塞轮询）"""
        # EMS 端口
        try:
            conn, addr = self.server_socket_ems.accept()
            threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True).start()
        except socket.timeout:
            pass
        except Exception as e:
            if self.running:
                self.log(f"EMS端口错误: {e}", "ERROR")

        # 风机端口
        try:
            conn, addr = self.server_socket_wind.accept()
            threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True).start()
        except socket.timeout:
            pass
        except Exception as e:
            if self.running:
                self.log(f"风机端口错误: {e}", "ERROR")

    def _handle_client(self, conn, addr):
        """处理单个客户端连接"""
        session = ClientSession(conn, addr)
        role = None

        try:
            # 握手
            role = self.handler.handle_handshake(session, addr)
            if not role:
                conn.close()
                return

            # 注册客户端
            with self.client_lock:
                if role in self.clients:
                    self.clients[role].conn.close()
                self.clients[role] = session

            # 风机上线
            if role == 'wind_ctrl':
                self.engine.set_wind_online(True)
                self.log("风机控制器已上线")

            # EMS 参数推送
            if role == 'ems' and session.hello_received:
                self.handler.send_param_notify(session)

            # 主循环
            last_wind_input = 0
            while session.connected:
                if role == 'wind_ctrl' and time.time() - last_wind_input >= 1.0:
                    self.handler.send_wind_input(session)
                    last_wind_input = time.time()

                msgs = session.recv_all_json(timeout=0.2)
                if msgs:
                    for msg in msgs:
                        self.handler.dispatch(session, msg)

        except Exception as e:
            self.log(f"{role} 异常: {e}", "ERROR")
        finally:
            # 风机下线
            if role == 'wind_ctrl':
                self.engine.set_wind_online(False)
                self.log("风机控制器已下线")
            # 移除客户端
            with self.client_lock:
                if self.clients.get(role) == session:
                    del self.clients[role]
            conn.close()
            self.log(f"{role} 断开连接")

    def _heartbeat_monitor(self):
        """心跳监控：15秒无响应则断开"""
        while self.running:
            time.sleep(5)
            now = time.time()
            with self.client_lock:
                for role, session in list(self.clients.items()):
                    if session.connected and (now - session.last_heartbeat) > 15:
                        self.log(f"⚠️ {role} 心跳超时 (15s)，断开连接", "WARNING")
                        session.connected = False
                        try:
                            session.conn.close()
                        except Exception:
                            pass

    def stop(self):
        """停止 TCP 服务器"""
        self.running = False
        if self.server_socket_ems:
            self.server_socket_ems.close()
        if self.server_socket_wind:
            self.server_socket_wind.close()
        # 关闭所有客户端
        with self.client_lock:
            for session in self.clients.values():
                try:
                    session.conn.close()
                except Exception:
                    pass
            self.clients.clear()
        self.log("TCP Server 已停止")