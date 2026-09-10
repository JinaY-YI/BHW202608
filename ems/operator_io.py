"""EMS 通信进程 operator_io。

负责与电网模拟器（TCP Server）进行数据通信，自身不做调度计算：
1. 实时运行数据获取：以 1s 为周期发送 yc_yx_request，增量/变位 + 断点补传；
2. 调度命令下发：以 1s 为周期检查本地遥调表，有更新则打包下发 command；
3. 通信状态管理：心跳保活、断线识别、退避重连、参数同步、日志记录。

作为 TCP Client 主动连接电网模拟器。
"""
from __future__ import annotations

import collections
import json
import select
import socket
import threading
import time
from typing import Any, Deque, Dict, List, Optional

from . import config, protocol
from .database import Database


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


class OperatorIO(threading.Thread):
    """通信进程（线程实现，独立 DB 连接）。"""

    def __init__(self, db: Database, host: Optional[str] = None,
                 port: Optional[int] = None, trace: bool = False):
        super().__init__(daemon=True, name="operator_io")
        self.db = db
        self.host = host or config.GRID_HOST
        self.port = port or config.GRID_PORT
        self.trace = trace  # 为 True 时把每条收发报文全文写入日志，便于联调排障

        self._stop = threading.Event()
        self._sock: Optional[socket.socket] = None
        self._buffer = b""
        self._backoff = config.RECONNECT_BASE
        self._need_retransmit = False
        self._inflight: Deque[List[int]] = collections.deque()

        # 周期性任务时间基准
        self._last_rx = 0.0
        self._last_yc_yx = 0.0
        self._last_heartbeat = 0.0
        self._last_command_check = 0.0

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def run(self) -> None:
        self.db.add_log("INFO", "operator_io", "通信进程启动")
        while not self._stop.is_set():
            try:
                if self._sock is None:
                    self._connect()
                else:
                    self._serve_cycle()
            except (ConnectionError, OSError, TypeError, ValueError, AttributeError) as exc:
                self._disconnect(f"{type(exc).__name__}: {exc}")
        self._disconnect("进程退出")
        self.db.add_log("INFO", "operator_io", "通信进程退出")

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    def _connect(self) -> None:
        self.db.set_comm_status(connected=0)
        self.db.add_log(
            "INFO", "operator_io", f"尝试连接电网模拟器 {self.host}:{self.port}"
        )
        try:
            sock = socket.create_connection((self.host, self.port), timeout=5.0)
            sock.settimeout(None)  # 阻塞模式；读由 select 门控
            self._sock = sock
            self._buffer = b""
            self._backoff = config.RECONNECT_BASE
            self._need_retransmit = False
            # 重连复位：seq 从 0 起，请求全量数据
            self.db.set_last_seq(0)
            now = time.time()
            self._last_rx = now
            self._last_yc_yx = 0.0  # 立即触发首次请求
            self._last_heartbeat = now
            self._last_command_check = now
            self._send_bytes(protocol.build_hello())
            self._send_bytes(protocol.build_param_query())
            self.db.set_comm_status(
                connected=1, remote_addr=f"{self.host}:{self.port}"
            )
            self.db.add_log("INFO", "operator_io", "已连接电网模拟器，握手与参数查询已发送")
        except OSError as exc:
            self.db.set_comm_status(connected=0)
            self.db.add_log("WARN", "operator_io", f"连接失败: {exc}，{self._backoff:.0f}s 后重试")
            self._stop.wait(self._backoff)
            self._backoff = min(self._backoff * 2, config.RECONNECT_MAX)

    def _disconnect(self, reason: str) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._buffer = b""
        # 未回执的命令标记失败
        for batch in self._inflight:
            for cid in batch:
                self.db.mark_command_status("scada_yt", cid, "failed")
        self._inflight.clear()
        self.db.set_comm_status(connected=0)
        self.db.add_log("WARN", "operator_io", f"连接断开: {reason}")

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def _serve_cycle(self) -> None:
        now = time.time()
        sock = self._sock
        if sock is None:
            return

        # 1) 读取可用数据（select 门控，避免阻塞）
        try:
            readable, _, _ = select.select([sock], [], [], config.SOCKET_READ_TIMEOUT)
            if readable:
                chunk = sock.recv(65536)
                if not chunk:
                    raise ConnectionError("对端关闭连接")
                self._on_data(chunk)
        except BlockingIOError:
            pass

        # 2) 心跳超时判定（15s 内无任何报文 → 断线）
        if now - self._last_rx > config.HEARTBEAT_TIMEOUT:
            self._disconnect("心跳超时（15s 无报文）")
            return

        # 3) 周期性发送
        if now - self._last_yc_yx >= config.YC_YX_PERIOD:
            self._send_yc_yx_request()
        if now - self._last_heartbeat >= config.HEARTBEAT_PERIOD:
            self._send_bytes(protocol.build_heartbeat())
        if now - self._last_command_check >= config.COMMAND_CHECK_PERIOD:
            self._send_pending_commands()

    # ------------------------------------------------------------------
    # 收发底层
    # ------------------------------------------------------------------
    def _send_bytes(self, data: bytes) -> None:
        sock = self._sock
        if sock is None:
            return
        sock.sendall(data)
        if self.trace:
            try:
                self.db.add_log("DEBUG", "operator_io",
                                f"[TX] {data.decode('utf-8').strip()}")
            except (ValueError, UnicodeDecodeError):
                pass
        self.db.set_comm_status(last_tx_time=_now())

    def _on_data(self, chunk: bytes) -> None:
        self._buffer += chunk
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            self._last_rx = time.time()
            try:
                msg = protocol.parse_message(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                self.db.add_log("ERROR", "operator_io", f"报文解析失败: {exc}")
                continue
            self._handle_message(msg)

    # ------------------------------------------------------------------
    # 周期性发送动作
    # ------------------------------------------------------------------
    def _send_yc_yx_request(self) -> None:
        last_seq = self.db.get_last_seq()
        self._send_bytes(protocol.build_yc_yx_request(last_seq, self._need_retransmit))
        self._last_yc_yx = time.time()

    def _send_pending_commands(self) -> None:
        self._last_command_check = time.time()
        pending = self.db.get_pending_commands("scada_yt")
        if not pending:
            return
        commands = [
            protocol.build_remote_adjust_command(c["target"], c["value"])
            for c in pending
        ]
        ids = [c["id"] for c in pending]
        self._send_bytes(protocol.build_command(commands))
        self._inflight.append(ids)
        for cid in ids:
            self.db.mark_command_status("scada_yt", cid, "sent")
        self.db.add_log(
            "INFO", "operator_io",
            f"下发调度指令 {len(commands)} 条: "
            + ", ".join(f"{c['target']}={c['value']}" for c in commands),
        )

    # ------------------------------------------------------------------
    # 报文处理
    # ------------------------------------------------------------------
    def _handle_message(self, msg: Dict[str, Any]) -> None:
        if self.trace:
            self.db.add_log(
                "DEBUG", "operator_io",
                f"[RX] {json.dumps(msg, ensure_ascii=False)}",
            )
        if not protocol.validate_message(msg):
            self.db.add_log("WARN", "operator_io", "收到结构非法报文，已忽略")
            return
        mtype = msg.get("type")
        data = msg.get("data") or {}

        if mtype == protocol.TYPE_HELLO_ACK:
            self.db.add_log("INFO", "operator_io", "收到 hello_ack 握手确认")
        elif mtype == protocol.TYPE_HEARTBEAT:
            self._send_bytes(protocol.build_heartbeat())  # 立即回复
        elif mtype == protocol.TYPE_YC_YX_RESPONSE:
            self._handle_yc_yx_response(data)
        elif mtype == protocol.TYPE_COMMAND_RESPONSE:
            self._handle_command_response(data)
        elif mtype == protocol.TYPE_PARAM_NOTIFY:
            self._handle_param_notify(data)
        else:
            self.db.add_log(
                "WARN", "operator_io",
                f"收到未识别报文类型 {mtype}：{json.dumps(msg, ensure_ascii=False)}",
            )

    def _handle_yc_yx_response(self, data: Dict[str, Any]) -> None:
        seq = int(data.get("seq", self.db.get_last_seq()))
        retransmit = bool(data.get("retransmit", False))
        yc = data.get("yc") or {}
        yx = data.get("yx") or {}
        last_seq = self.db.get_last_seq()

        # 1) 只要带数据就应用——报文内的 yc/yx 均为绝对值，直接覆盖本地缓存，
        #    保证曲线即使在对端 seq 语义有差异时也能持续更新（不再"维持直线"）。
        if yc:
            self.db.update_scada_yc(seq=seq, **yc)
        if yx:
            self.db.update_scada_yx(**yx)

        # 2) 推进序号 / 复位重传标记（收到数据即认为链路在正常推进）
        if yc or yx or retransmit:
            self.db.set_last_seq(seq)
            self._need_retransmit = False
            if retransmit:
                self.db.add_log("INFO", "operator_io", f"全量快照重建完成 seq={seq}")

        # 3) 序号跳变（漏包）检测：非阻塞，仅请求一次全量补传，数据照常更新。
        #    重传响应本身就是补全，不再重复标记。
        if not retransmit and last_seq > 0 and seq > last_seq + 1:
            self._need_retransmit = True
            self.db.add_log(
                "WARN", "operator_io",
                f"遥测序号跳变 {last_seq} -> {seq}，已请求全量补传（数据已按绝对值更新）",
            )

        # 无论是否有变化，都刷新数据更新时间
        self.db.set_comm_status(last_data_update=_now())

    def _handle_command_response(self, data: Dict[str, Any]) -> None:
        result = str(data.get("result", "unknown"))
        failed = data.get("failed_commands") or []
        executed_seq = data.get("executed_seq")

        batch = self._inflight.popleft() if self._inflight else []
        for cid in batch:
            status = "executed" if result == "success" else "failed"
            self.db.mark_command_status("scada_yt", cid, status, executed_seq)

        self.db.set_comm_status(last_command_result=result)
        level = "INFO" if result == "success" else "ERROR"
        self.db.add_log(
            level, "operator_io",
            f"命令回执 result={result}, failed={failed}, executed_seq={executed_seq}",
        )

    def _handle_param_notify(self, data: Dict[str, Any]) -> None:
        self.db.update_device_params(**data)
        self.db.add_log(
            "INFO", "operator_io",
            f"收到设备参数同步: {sorted(data.keys())}",
        )
