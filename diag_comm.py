"""联调诊断工具：以最小 EMS 客户端直连电网模拟器，逐条打印原始收发报文。

用于在和学生 A 联调时定位协议不匹配：能看到对端实际返回的报文类型与字段，
从而判断是"序号机制"还是"命令回执"不一致。

用法：
    python diag_comm.py --host 192.168.1.10 --port 8888 --seconds 20
"""
import argparse
import json
import select
import socket
import sys
import time

_last_ms = 0


def build(msg_type, data, src="ems", dst="grid_simulator"):
    global _last_ms
    ms = int(time.time() * 1000)
    if ms <= _last_ms:
        ms = _last_ms + 1
    _last_ms = ms
    msg = {"msg_id": str(ms), "timestamp": int(time.time()),
           "src": src, "dst": dst, "type": msg_type, "data": data}
    return (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="EMS 联调诊断工具")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8888)
    ap.add_argument("--seconds", type=int, default=20)
    args = ap.parse_args()

    print(f"连接电网模拟器 {args.host}:{args.port} ...")
    try:
        sock = socket.create_connection((args.host, args.port), timeout=5)
    except OSError as e:
        print(f"连接失败: {e}")
        return 1
    sock.settimeout(0.3)
    print("已连接。开始收发报文，Ctrl+C 结束。\n")

    def send(t, d):
        b = build(t, d)
        print(f">>> [TX] {b.decode('utf-8').strip()}")
        sock.sendall(b)

    # 握手 + 参数查询
    send("hello", {"role": "ems", "reset_seq": True})
    send("param_query", {})

    last_seq = 0
    buf = b""
    t0 = time.time()
    last_hb = t0
    last_req = 0.0
    sent_cmd = False
    stats = {}

    try:
        while time.time() - t0 < args.seconds:
            now = time.time()
            # 每 1s 请求遥测（首次 last_seq=0，之后按对端返回的 seq 递增）
            if now - last_req >= 1.0:
                send("yc_yx_request", {"last_seq": last_seq, "need_retransmit": False})
                last_req = now
            # 每 5s 心跳
            if now - last_hb >= 5.0:
                send("heartbeat", {})
                last_hb = now
            # 运行 3 秒后下发一条测试遥调，观察是否有 command_response
            if not sent_cmd and now - t0 >= 3.0:
                send("command", {"commands": [
                    {"cmd_type": "remote_adjust", "target": "wtg_power", "value": 35.0},
                    {"cmd_type": "remote_adjust", "target": "diesel_power", "value": 20.0},
                ]})
                sent_cmd = True

            # 读对端返回
            try:
                r, _, _ = select.select([sock], [], [], 0.05)
                if r:
                    chunk = sock.recv(65536)
                    if not chunk:
                        print("<<< 对端关闭连接")
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        print(f"<<< [RX] {line.decode('utf-8', errors='replace')}")
                        try:
                            m = json.loads(line.decode("utf-8"))
                            stats[m.get("type", "?")] = stats.get(m.get("type", "?"), 0) + 1
                            if m.get("type") == "yc_yx_response":
                                last_seq = int(m.get("data", {}).get("seq", last_seq))
                        except Exception:
                            pass
            except socket.timeout:
                pass
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()

    print("\n" + "=" * 60)
    print("统计（收到的报文类型 → 条数）：")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    if "command_response" not in stats:
        print("⚠ 未收到 command_response —— 检查对端是否实现了 command 回执，或回执类型名是否一致")
    if stats.get("yc_yx_response", 0) <= 1:
        print("⚠ yc_yx_response 只有 1 条 —— 检查对端的序号(seq)机制是否与协议一致")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
