import socket
import json
import time

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(('127.0.0.1', 8888))

hello = {"type": "hello", "data": {"role": "ems"}}
sock.send((json.dumps(hello) + '\n').encode())
time.sleep(1)

count = 0
while True:
    count += 1
    cmd = {
        "type": "command",
        "src": "ems",
        "dst": "grid_simulator",
        "msg_id": f"test_{count}",
        "data": {
            "commands": [
                {"cmd_type": "remote_adjust", "target": "wtg_power", "value": 10.0 + count},
                {"cmd_type": "remote_adjust", "target": "diesel_power", "value": 30.0}
            ]
        }
    }
    sock.send((json.dumps(cmd) + '\n').encode())
    print(f"[{time.strftime('%H:%M:%S')}] 已发送命令 #{count}")
    time.sleep(5)