"""
报文分发与处理

职责：
- 握手处理（含风机无hello兼容）
- 报文分发（按 type 路由）
- 各类型报文的业务处理
- 遥控指令接收与存储
"""
import time
from . import DST_GRID


class MessageHandler:
    """报文处理器"""

    def __init__(self, db, engine, clients, client_lock, log_callback=None):
        """
        参数:
            db: GridDB 实例
            engine: ComputeEngine 实例
            clients: 客户端字典 {role: ClientSession}
            client_lock: 客户端锁
            log_callback: 日志回调函数
        """
        self.db = db
        self.engine = engine
        self.clients = clients
        self.client_lock = client_lock
        self.log = log_callback or print

    # ==================== 握手处理 ====================

    def handle_handshake(self, session, addr):
        """
        处理握手，返回角色

        支持：
            1. 标准 hello 握手（EMS 必须）
            2. 风机无 hello 自动识别（兼容模式）

        参数:
            session: ClientSession 实例
            addr: 客户端地址 (ip, port)

        返回:
            str: 角色 ('ems' 或 'wind_ctrl')，失败返回 None
        """
        from . import PORT_WIND

        msgs = session.recv_all_json(timeout=1.0)
        role = None

        if msgs and len(msgs) > 0:
            first = msgs[0]
            if first.get('type') == 'hello':
                role = first.get('data', {}).get('role')

        # 如果没有收到 hello，根据端口判断
        if role is None:
            if addr[1] == PORT_WIND:
                role = 'wind_ctrl'
                self.log(f"⚠️ 风机未发送hello，自动识别为 wind_ctrl", "WARNING")
            else:
                return None

        if role not in ['ems', 'wind_ctrl']:
            return None

        session.role = role
        session.hello_received = (role == 'ems')
        session.last_heartbeat = time.time()

        # EMS 必须发送 hello
        if role == 'ems' and not session.hello_received:
            self.log("❌ EMS未发送hello，拒绝连接", "ERROR")
            return None

        # 回复 hello_ack
        if session.hello_received:
            ack = {
                "msg_id": f"ack_{int(time.time() * 1000)}",
                "timestamp": int(time.time()),
                "src": "grid_simulator",
                "dst": role,
                "type": "hello_ack",
                "data": {"result": "ok", "server_time": time.time()}
            }
            session.send_json(ack)
            self.log(f"{role} 握手成功 ({addr[0]}:{addr[1]})")
        else:
            self.log(f"风机连接已建立 (无hello) ({addr[0]}:{addr[1]})")

        return role

    # ==================== 报文分发 ====================

    def dispatch(self, session, msg):
        """
        分发报文到对应的处理函数

        参数:
            session: ClientSession 实例
            msg: 解析后的 JSON 报文
        """
        msg_type = msg.get('type')
        dst = msg.get('dst')

        if dst != 'grid_simulator':
            return

        if msg_type == 'heartbeat':
            self._handle_heartbeat(session)
        elif msg_type == 'yc_yx_request' and session.role == 'ems':
            self._handle_yc_yx_request(session)
        elif msg_type == 'command' and session.role == 'ems':
            self._handle_command(session, msg)
        elif msg_type == 'wind_output' and session.role == 'wind_ctrl':
            self._handle_wind_output(session, msg)
        elif msg_type == 'param_sync' and session.role == 'wind_ctrl':
            self._handle_param_sync(session, msg)
        elif msg_type == 'param_query' and session.role == 'ems':
            self.send_param_notify(session)

    # ==================== 具体报文处理 ====================

    def _handle_heartbeat(self, session):
        """处理心跳（立即回复）"""
        session.last_heartbeat = time.time()
        session.send_json({
            "msg_id": f"hb_ack_{int(time.time() * 1000)}",
            "timestamp": int(time.time()),
            "src": "grid_simulator",
            "dst": session.role,
            "type": "heartbeat",
            "data": {}
        })

    def _handle_yc_yx_request(self, session):
        """处理遥测/遥信请求（增量机制）"""
        yc = self.db.get_scada_yc()
        yx = self.db.get_scada_yx()
        seq = session.last_seq + 1

        resp = {
            "msg_id": f"yc_resp_{int(time.time() * 1000)}",
            "timestamp": int(time.time()),
            "src": "grid_simulator",
            "dst": "ems",
            "type": "yc_yx_response",
            "data": {
                "seq": seq,
                "retransmit": False,
                "yc": {k: yc.get(k, 0) for k in
                       ['wind_speed', 'load_power', 'wtg_power', 'diesel_power']} if yc else {},
                "yx": {k: yx.get(k, 1) for k in ['wtg_status', 'diesel_status']} if yx else {}
            }
        }
        session.send_json(resp)
        session.last_seq = seq

    def _handle_command(self, session, msg):
        """
        处理 EMS 下发指令

        支持：
            1. 遥调指令: wtg_power, diesel_power
            2. 遥控指令: wtg_on_off, diesel_on_off
        """
        data = msg.get('data', {})
        commands = data.get('commands', [])
        msg_id = msg.get('msg_id', 'unknown')

        cmd_summary = ', '.join([f"{c.get('target')}={c.get('value')}" for c in commands])
        self.log(f"📩 收到 EMS 命令 (msg_id={msg_id}): {cmd_summary}")

        failed = []
        executed_seq = 0

        for idx, cmd in enumerate(commands):
            cmd_type = cmd.get('cmd_type')
            target = cmd.get('target')
            value = cmd.get('value')

            # ===== 遥控指令处理 =====
            if target in ['wtg_on_off', 'diesel_on_off']:
                self.db.add_remote_command(msg_id, target, int(value), 'ems', 'received')
                if target == 'wtg_on_off':
                    yx = self.db.get_scada_yx()
                    self.db.update_scada_yx(int(value), yx.get('diesel_status', 1) if yx else 1)
                    self.log(f"   ✅ 执行风机启停: {'启动' if int(value) else '停机'}")
                else:
                    yx = self.db.get_scada_yx()
                    self.db.update_scada_yx(yx.get('wtg_status', 0) if yx else 0, int(value))
                    self.log(f"   ✅ 执行柴发启停: {'启动' if int(value) else '停机'}")
                self.db.update_remote_status(msg_id, 'executed')
                executed_seq += 1
                continue

            # ===== 遥调指令处理 =====
            if cmd_type != 'remote_adjust':
                failed.append({"index": idx, "reason": f"unsupported cmd_type: {cmd_type}"})
                continue

            if target == 'wtg_power':
                self.db.update_scada_yt_wtg_power_set(value)
                executed_seq += 1
                self.log(f"   ✅ 执行风机目标功率: {value:.2f} kW")
            elif target == 'diesel_power':
                self.db.update_scada_yt_diesel_power_set(value)
                executed_seq += 1
                self.log(f"   ✅ 执行柴发目标功率: {value:.2f} kW")
            else:
                failed.append({"index": idx, "target": target, "reason": f"unknown target: {target}"})

        # 发送回执
        response = {
            "msg_id": f"cmd_resp_{msg_id}",
            "timestamp": int(time.time()),
            "src": "grid_simulator",
            "dst": "ems",
            "type": "command_response",
            "data": {
                "result": "success" if not failed else "partial_success",
                "failed_commands": failed,
                "executed_seq": executed_seq
            }
        }
        session.send_json(response)
        self.log(f"📤 发送命令回执: result={response['data']['result']}, executed={executed_seq}, failed={len(failed)}")

    def _handle_wind_output(self, session, msg):
        """处理风机数据回传 + 回传 wind_result"""
        data = msg.get('data', {})

        # 更新遥信
        if data.get('wtg_status') is not None:
            yx = self.db.get_scada_yx()
            self.db.update_scada_yx(data['wtg_status'], yx.get('diesel_status', 1) if yx else 1)

        # 更新桨距角
        if data.get('pitch_angle_set') is not None:
            self.db.update_scada_yt_pitch_angle(data['pitch_angle_set'])

        # 回传实际出力
        yc = self.db.get_scada_yc()
        if yc:
            session.send_json({
                "msg_id": f"wind_res_{int(time.time() * 1000)}",
                "timestamp": int(time.time()),
                "src": "grid_simulator",
                "dst": "wind_ctrl",
                "type": "wind_result",
                "data": {
                    "wtg_power": yc.get('wtg_power', 0),
                    "diesel_power": yc.get('diesel_power', 0),
                    "load_power": yc.get('load_power', 0)
                }
            })

    def _handle_param_sync(self, session, msg):
        """处理风机参数同步"""
        data = msg.get('data', {})
        if all(k in data for k in ['cut_in_wind', 'cut_out_wind', 'rated_wind', 'rated_power']):
            self.db.update_wind_params(
                data['cut_in_wind'], data['cut_out_wind'],
                data['rated_wind'], data['rated_power']
            )
            session.send_json({
                "msg_id": f"param_ack_{int(time.time() * 1000)}",
                "timestamp": int(time.time()),
                "src": "grid_simulator",
                "dst": "wind_ctrl",
                "type": "param_ack",
                "data": {"result": "ok"}
            })
            self.log("风机参数已同步")
            # 通知 EMS
            with self.client_lock:
                if 'ems' in self.clients:
                    self.send_param_notify(self.clients['ems'])

    # ==================== 主动发送 ====================

    def send_wind_input(self, session):
        """向风机控制器下发 wind_input"""
        yc = self.db.get_scada_yc()
        yt = self.db.get_scada_yt()
        if yc and yt:
            session.send_json({
                "msg_id": f"wind_in_{int(time.time() * 1000)}",
                "timestamp": int(time.time()),
                "src": "grid_simulator",
                "dst": "wind_ctrl",
                "type": "wind_input",
                "data": {
                    "wind_speed": yc.get('wind_speed', 0),
                    "wtg_power_set": yt.get('wtg_power_set', 0),
                    "sim_time": yc.get('sim_time', 0)
                }
            })

    def send_param_notify(self, session):
        """向 EMS 推送设备参数"""
        params = self.db.get_device_params()
        if params:
            session.send_json({
                "msg_id": f"param_notify_{int(time.time() * 1000)}",
                "timestamp": int(time.time()),
                "src": "grid_simulator",
                "dst": "ems",
                "type": "param_notify",
                "data": params
            })