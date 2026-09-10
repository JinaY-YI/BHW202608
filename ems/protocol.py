"""V3 TCP 通信协议（EMS 侧）报文构建与解析。

协议：JSON、UTF-8、每条报文以换行符 `\\n` 结尾。
通用头部：msg_id / timestamp / src / dst / type / data。

EMS 作为 TCP Client，src 固定为 "ems"，dst 为 "grid_simulator"。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List, Optional

from . import config

# 报文类型常量（协议第三部分）
TYPE_HELLO = "hello"
TYPE_HELLO_ACK = "hello_ack"
TYPE_HEARTBEAT = "heartbeat"
TYPE_YC_YX_REQUEST = "yc_yx_request"
TYPE_YC_YX_RESPONSE = "yc_yx_response"
TYPE_COMMAND = "command"
TYPE_COMMAND_RESPONSE = "command_response"
TYPE_PARAM_QUERY = "param_query"
TYPE_PARAM_NOTIFY = "param_notify"
# 其它模块间的报文（EMS 可能收到/忽略）
TYPE_WIND_INPUT = "wind_input"
TYPE_WIND_OUTPUT = "wind_output"
TYPE_WIND_RESULT = "wind_result"
TYPE_PARAM_SYNC = "param_sync"
TYPE_PARAM_ACK = "param_ack"
TYPE_PARAM_PUSH = "param_push"

_REQUIRED_HEADERS = ("msg_id", "timestamp", "src", "dst", "type")


_id_lock = threading.Lock()
_last_ms = 0


def make_msg_id() -> str:
    """唯一 ID：时间戳(ms)，单调递增保证唯一。

    保持为纯数字字符串（无字母/连字符），兼容对端可能将其按整数解析。
    """
    global _last_ms
    with _id_lock:
        ms = int(time.time() * 1000)
        if ms <= _last_ms:
            ms = _last_ms + 1
        _last_ms = ms
        return str(ms)


def build_message(msg_type: str, data: Dict[str, Any],
                  dst: str = config.DST_GRID, src: str = config.SRC,
                  msg_id: Optional[str] = None) -> str:
    """构建一条 JSON 报文字符串（不含换行符）。"""
    msg = {
        "msg_id": msg_id or make_msg_id(),
        "timestamp": int(time.time()),
        "src": src,
        "dst": dst,
        "type": msg_type,
        "data": data,
    }
    return json.dumps(msg, ensure_ascii=False)


def encode_message(msg_type: str, data: Dict[str, Any],
                   dst: str = config.DST_GRID, src: str = config.SRC) -> bytes:
    """构建并编码为带换行结尾的字节串，可直接发送。"""
    return (build_message(msg_type, data, dst=dst, src=src) + "\n").encode("utf-8")


def parse_message(line: str) -> Dict[str, Any]:
    """解析一条 JSON 报文（去除首尾空白）。非法 JSON 抛 ValueError。"""
    line = line.strip()
    if not line:
        raise ValueError("empty message line")
    return json.loads(line)


def validate_message(msg: Dict[str, Any]) -> bool:
    """校验通用报文头部是否完整。"""
    return isinstance(msg, dict) and all(k in msg for k in _REQUIRED_HEADERS)


# ---------------------------------------------------------------------------
# 具体报文构建器（EMS → 电网模拟器）
# ---------------------------------------------------------------------------
def build_hello() -> bytes:
    """连接建立后发送的握手报文。"""
    return encode_message(TYPE_HELLO, {"role": config.SRC, "reset_seq": True})


def build_heartbeat() -> bytes:
    """心跳保活报文（每 5s）。"""
    return encode_message(TYPE_HEARTBEAT, {})


def build_yc_yx_request(last_seq: int, need_retransmit: bool = False) -> bytes:
    """遥测/遥信增量请求报文（每 1s）。"""
    return encode_message(
        TYPE_YC_YX_REQUEST,
        {"last_seq": int(last_seq), "need_retransmit": bool(need_retransmit)},
    )


def build_command(commands: List[Dict[str, Any]]) -> bytes:
    """调度指令下发报文（遥调）。commands 形如
    [{"cmd_type": "remote_adjust", "target": "wtg_power", "value": 35.0}]"""
    return encode_message(TYPE_COMMAND, {"commands": commands})


def build_param_query() -> bytes:
    """设备参数查询报文。"""
    return encode_message(TYPE_PARAM_QUERY, {})


def build_remote_adjust_command(target: str, value: float) -> Dict[str, Any]:
    """构造一条遥调指令。"""
    return {"cmd_type": config.REMOTE_ADJUST, "target": target, "value": float(value)}
