"""protocol 模块单元测试。"""
import json
import unittest

from ems import protocol


class TestProtocol(unittest.TestCase):
    def test_build_message_structure(self):
        s = protocol.build_message("heartbeat", {})
        msg = json.loads(s)
        for k in ("msg_id", "timestamp", "src", "dst", "type"):
            self.assertIn(k, msg)
        self.assertEqual(msg["src"], "ems")
        self.assertEqual(msg["dst"], "grid_simulator")
        self.assertEqual(msg["type"], "heartbeat")

    def test_encode_ends_with_newline(self):
        b = protocol.encode_message("heartbeat", {})
        self.assertTrue(b.endswith(b"\n"))
        # 去掉换行后可被 JSON 解析
        json.loads(b[:-1].decode("utf-8"))

    def test_parse_message(self):
        s = protocol.build_message("yc_yx_response", {"seq": 1, "yc": {}, "yx": {}})
        msg = protocol.parse_message(s)
        self.assertEqual(msg["type"], "yc_yx_response")
        self.assertEqual(msg["data"]["seq"], 1)

    def test_parse_invalid(self):
        with self.assertRaises(ValueError):
            protocol.parse_message("not json")

    def test_validate_message(self):
        good = {"msg_id": "1", "timestamp": 1, "src": "ems",
                "dst": "grid_simulator", "type": "x", "data": {}}
        self.assertTrue(protocol.validate_message(good))
        bad = {"type": "x"}
        self.assertFalse(protocol.validate_message(bad))

    def test_builders(self):
        hello = json.loads(protocol.build_hello()[:-1])
        self.assertEqual(hello["type"], "hello")
        self.assertEqual(hello["data"]["role"], "ems")
        self.assertTrue(hello["data"]["reset_seq"])

        hb = json.loads(protocol.build_heartbeat()[:-1])
        self.assertEqual(hb["type"], "heartbeat")
        self.assertEqual(hb["data"], {})

        req = json.loads(protocol.build_yc_yx_request(123, True)[:-1])
        self.assertEqual(req["type"], "yc_yx_request")
        self.assertEqual(req["data"], {"last_seq": 123, "need_retransmit": True})

        cmd = json.loads(protocol.build_command(
            [protocol.build_remote_adjust_command("wtg_power", 35.0)])[:-1])
        self.assertEqual(cmd["type"], "command")
        self.assertEqual(cmd["data"]["commands"][0]["target"], "wtg_power")
        self.assertEqual(cmd["data"]["commands"][0]["cmd_type"], "remote_adjust")

        pq = json.loads(protocol.build_param_query()[:-1])
        self.assertEqual(pq["type"], "param_query")

    def test_remote_adjust_command(self):
        c = protocol.build_remote_adjust_command("diesel_power", 20.5)
        self.assertEqual(c["value"], 20.5)


if __name__ == "__main__":
    unittest.main()
