"""mock_grid 仿真桩单元测试。"""
import json
import math
import socket
import unittest

from ems import mock_grid


class FakeConn:
    """记录发送字节的假连接。"""
    def __init__(self):
        self.sent = []

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, *a):
        raise RuntimeError("unused")


def parse_sent(conn, index=0):
    return json.loads(conn.sent[index][:-1].decode("utf-8"))


class TestMockGridSimulation(unittest.TestCase):
    def setUp(self):
        self.grid = mock_grid.MockGrid()

    def test_tick_updates_and_seq_increments(self):
        # 首次 tick：正弦场景产生初值 → seq 递增
        self.grid._tick()
        self.assertEqual(self.grid.seq, 1)
        expected_wind = round(8.0 + 7.0 * math.sin(2 * math.pi * 1 / 50.0), 4)
        expected_load = round(40.0 + 20.0 * math.sin(2 * math.pi * 1 / 30.0), 4)
        self.assertAlmostEqual(self.grid.yc["wind_speed"], expected_wind, places=4)
        self.assertAlmostEqual(self.grid.yc["load_power"], expected_load, places=4)
        self.assertAlmostEqual(self.grid.yc["wtg_power"], 0.0)
        # 风机设定为 0，柴油补足负荷
        self.assertAlmostEqual(self.grid.yc["diesel_power"], expected_load, places=4)

    def test_tick_constant_scenario_no_seq_increment(self):
        self.grid.scenario = lambda t: (8.0, 60.0)
        self.grid._tick()
        seq_after_first = self.grid.seq
        self.assertGreaterEqual(seq_after_first, 1)
        # 设定值不变时，后续 tick 数值不变 → seq 不增
        self.grid._tick()
        self.grid._tick()
        self.assertEqual(self.grid.seq, seq_after_first)

    def test_command_updates_setpoints(self):
        fake = FakeConn()
        self.grid._handle_command(fake, {
            "commands": [
                {"cmd_type": "remote_adjust", "target": "wtg_power", "value": 35.0},
                {"cmd_type": "remote_adjust", "target": "diesel_power", "value": 20.0},
            ]
        }, "ems")
        self.assertAlmostEqual(self.grid.wtg_power_set, 35.0)
        self.assertAlmostEqual(self.grid.diesel_power_set, 20.0)
        resp = parse_sent(fake)
        self.assertEqual(resp["type"], "command_response")
        self.assertEqual(resp["data"]["result"], "success")

    def test_wtg_power_computed_by_grid(self):
        # 额定风、设定 40 → 实际出力 = min(100, 40) = 40
        self.grid.scenario = lambda t: (12.0, 50.0)
        self.grid.wtg_power_set = 40.0
        self.grid._tick()
        self.assertAlmostEqual(self.grid.yc["wtg_power"], 40.0)
        self.assertAlmostEqual(self.grid.yc["diesel_power"], 10.0)  # clamp(50-40,10,150)

    def test_yc_yx_request_full_snapshot(self):
        fake = FakeConn()
        self.grid._tick()
        self.grid._handle_yc_yx_request(fake, {"last_seq": 0, "need_retransmit": False}, "ems")
        resp = parse_sent(fake)
        self.assertEqual(resp["type"], "yc_yx_response")
        self.assertEqual(set(resp["data"]["yc"].keys()),
                         {"sim_time", "wind_speed", "load_power", "wtg_power", "diesel_power"})
        self.assertEqual(set(resp["data"]["yx"].keys()), {"wtg_status", "diesel_status"})

    def test_yc_yx_request_incremental_and_no_change(self):
        fake = FakeConn()
        self.grid._tick()   # seq -> 1
        self.grid._handle_yc_yx_request(fake, {"last_seq": 0, "need_retransmit": False}, "ems")
        self.assertEqual(parse_sent(fake)["data"]["seq"], 1)

        # 用最新 seq 再请求 → 无变位（仅附带当前仿真时刻）
        fake2 = FakeConn()
        self.grid._handle_yc_yx_request(fake2, {"last_seq": self.grid.seq, "need_retransmit": False}, "ems")
        resp = parse_sent(fake2)
        self.assertEqual(set(resp["data"]["yc"].keys()), {"sim_time"})
        self.assertEqual(resp["data"]["yx"], {})
        self.assertEqual(resp["data"]["seq"], self.grid.seq)

    def test_yc_yx_retransmit(self):
        fake = FakeConn()
        self.grid._tick()
        self.grid._handle_yc_yx_request(fake, {"last_seq": 999, "need_retransmit": True}, "ems")
        resp = parse_sent(fake)
        self.assertTrue(resp["data"]["retransmit"])

    def test_param_query(self):
        fake = FakeConn()
        self.grid._handle(fake, {"type": "param_query", "src": "ems", "data": {}})
        resp = parse_sent(fake)
        self.assertEqual(resp["type"], "param_notify")
        self.assertIn("diesel_max", resp["data"])


class TestMockGridSocket(unittest.TestCase):
    def test_socket_roundtrip(self):
        grid = mock_grid.MockGrid(port=0).start()
        try:
            s = socket.create_connection(grid.address, timeout=5)
            s.settimeout(5)
            # hello -> hello_ack
            s.sendall(b'{"msg_id":"1","timestamp":1,"src":"ems","dst":"grid_simulator",'
                      b'"type":"hello","data":{"role":"ems","reset_seq":true}}\n')
            ack = json.loads(_read_line(s))
            self.assertEqual(ack["type"], "hello_ack")

            # yc_yx_request -> response
            s.sendall(b'{"msg_id":"2","timestamp":1,"src":"ems","dst":"grid_simulator",'
                      b'"type":"yc_yx_request","data":{"last_seq":0,"need_retransmit":false}}\n')
            resp = json.loads(_read_line(s))
            self.assertEqual(resp["type"], "yc_yx_response")
            self.assertIn("yc", resp["data"])

            # command -> command_response
            s.sendall(b'{"msg_id":"3","timestamp":1,"src":"ems","dst":"grid_simulator",'
                      b'"type":"command","data":{"commands":[{"cmd_type":"remote_adjust",'
                      b'"target":"wtg_power","value":35.0}]}}\n')
            cmd_resp = json.loads(_read_line(s))
            self.assertEqual(cmd_resp["type"], "command_response")
            self.assertEqual(cmd_resp["data"]["result"], "success")
            s.close()
        finally:
            grid.stop()


def _read_line(sock):
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            raise EOFError("connection closed")
        buf += chunk
    line, _ = buf.split(b"\n", 1)
    return line.decode("utf-8")


if __name__ == "__main__":
    unittest.main()
