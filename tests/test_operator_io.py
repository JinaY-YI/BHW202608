"""operator_io 报文处理逻辑单元测试（不依赖真实 socket）。"""
import unittest

from ems.database import Database
from ems.operator_io import OperatorIO
from tests.util import make_temp_db


class TestOperatorIOHandlers(unittest.TestCase):
    def setUp(self):
        self.db = Database(make_temp_db())
        # 不启动线程，仅测试报文处理逻辑
        self.io = OperatorIO(self.db, host="127.0.0.1", port=1)

    def tearDown(self):
        self.db.close()

    def test_yc_yx_response_applies_delta(self):
        self.io._handle_yc_yx_response({
            "seq": 1,
            "yc": {"wind_speed": 8.5, "load_power": 45.2,
                   "wtg_power": 30.1, "diesel_power": 15.0},
            "yx": {"wtg_status": 1, "diesel_status": 1},
        })
        self.assertEqual(self.db.get_last_seq(), 1)
        yc = self.db.get_scada_yc()
        self.assertAlmostEqual(yc["wtg_power"], 30.1)
        self.assertEqual(self.db.get_scada_yx()["wtg_status"], 1)
        self.assertFalse(self.io._need_retransmit)

    def test_yc_yx_empty_no_change(self):
        self.io._handle_yc_yx_response({"seq": 1, "yc": {"wtg_power": 30.1}, "yx": {}})
        self.io._handle_yc_yx_response({"seq": 1, "yc": {}, "yx": {}})
        # 无变化时 seq 不变，缓存保留
        self.assertEqual(self.db.get_last_seq(), 1)
        self.assertAlmostEqual(self.db.get_scada_yc()["wtg_power"], 30.1)

    def test_gap_detection_sets_retransmit(self):
        self.io._handle_yc_yx_response({"seq": 1, "yc": {"wtg_power": 30.1}, "yx": {}})
        # 序号从 1 跳到 3 → 漏包：数据仍按绝对值应用，但标记需要全量补传
        self.io._handle_yc_yx_response({"seq": 3, "yc": {"wtg_power": 31.0}, "yx": {}})
        self.assertTrue(self.io._need_retransmit)
        self.assertEqual(self.db.get_last_seq(), 3)      # 数据已应用、序号已推进
        self.assertAlmostEqual(self.db.get_scada_yc()["wtg_power"], 31.0)

    def test_retransmit_rebuilds_snapshot(self):
        self.io._handle_yc_yx_response({"seq": 1, "yc": {"wtg_power": 30.1}, "yx": {}})
        self.io._handle_yc_yx_response({
            "seq": 5, "retransmit": True,
            "yc": {"wind_speed": 9.0, "load_power": 50.0,
                   "wtg_power": 33.0, "diesel_power": 17.0},
            "yx": {"wtg_status": 1, "diesel_status": 1},
        })
        self.assertFalse(self.io._need_retransmit)
        self.assertEqual(self.db.get_last_seq(), 5)
        self.assertAlmostEqual(self.db.get_scada_yc()["wtg_power"], 33.0)

    def test_command_response_matches_inflight(self):
        cid = self.db.add_command("scada_yt", "wtg_power", 35.0)
        self.io._inflight.append([cid])
        self.io._handle_command_response(
            {"result": "success", "failed_commands": [], "executed_seq": 9001}
        )
        cmds = self.db.get_commands("scada_yt")
        self.assertEqual(cmds[0]["status"], "executed")
        self.assertEqual(cmds[0]["executed_seq"], 9001)

    def test_command_response_failure(self):
        cid = self.db.add_command("scada_yt", "wtg_power", 999.0)
        self.io._inflight.append([cid])
        self.io._handle_command_response(
            {"result": "failure", "failed_commands": [{"target": "wtg_power"}],
             "executed_seq": 9002}
        )
        self.assertEqual(self.db.get_commands("scada_yt")[0]["status"], "failed")

    def test_param_notify_updates_device_params(self):
        self.io._handle_param_notify({
            "diesel_max": 200.0, "diesel_min": 20.0,
            "cut_in_wind": 3.5, "cut_out_wind": 26.0,
            "rated_wind": 13.0, "rated_power": 110.0,
        })
        dev = self.db.get_device_params()
        self.assertAlmostEqual(dev["diesel_max"], 200.0)
        self.assertAlmostEqual(dev["rated_power"], 110.0)

    def test_disconnect_marks_inflight_failed(self):
        cid = self.db.add_command("scada_yt", "wtg_power", 35.0)
        self.io._inflight.append([cid])
        self.io._disconnect("测试断开")
        self.assertEqual(self.db.get_commands("scada_yt")[0]["status"], "failed")
        self.assertEqual(self.db.get_comm_status()["connected"], 0)


if __name__ == "__main__":
    unittest.main()
