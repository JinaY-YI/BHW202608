"""database 模块单元测试。"""
import unittest

from ems.database import Database
from tests.util import make_temp_db


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.db = Database(make_temp_db())

    def tearDown(self):
        self.db.close()

    def test_schema_created_and_defaults(self):
        params = self.db.get_sched_params()
        self.assertEqual(params["data_collect_period"], 1)
        self.assertEqual(params["decision_period"], 5)
        self.assertEqual(params["ctrl_mode_ems"], 1)

        dev = self.db.get_device_params()
        self.assertAlmostEqual(dev["rated_power"], 100.0)
        self.assertAlmostEqual(dev["diesel_max"], 150.0)

    def test_sched_params_roundtrip(self):
        self.db.set_sched_params(collect_period=2, decision_period=10, ctrl_mode=0)
        p = self.db.get_sched_params()
        self.assertEqual(p["data_collect_period"], 2)
        self.assertEqual(p["decision_period"], 10)
        self.assertEqual(p["ctrl_mode_ems"], 0)

    def test_device_params_update(self):
        self.db.update_device_params(diesel_max=200.0, rated_power=120.0)
        dev = self.db.get_device_params()
        self.assertAlmostEqual(dev["diesel_max"], 200.0)
        self.assertAlmostEqual(dev["rated_power"], 120.0)
        # 未提供的字段保持不变
        self.assertAlmostEqual(dev["cut_in_wind"], 3.0)

    def test_device_params_ignore_unknown(self):
        self.db.update_device_params(no_such_field=99.0, diesel_min=5.0)
        dev = self.db.get_device_params()
        self.assertAlmostEqual(dev["diesel_min"], 5.0)

    def test_env_realtime(self):
        self.db.set_env_realtime(sim_time=10, wind_speed=8.5, load_power=45.2)
        env = self.db.get_env_realtime()
        self.assertEqual(env["sim_time"], 10)
        self.assertAlmostEqual(env["wind_speed"], 8.5)
        self.assertAlmostEqual(env["load_power"], 45.2)

    def test_scada_yc_incremental_and_seq(self):
        self.assertEqual(self.db.get_last_seq(), 0)
        self.db.update_scada_yc(seq=1, wind_speed=8.5, load_power=45.2,
                                wtg_power=30.1, diesel_power=15.0)
        yc = self.db.get_scada_yc()
        self.assertEqual(yc["seq"], 1)
        self.assertAlmostEqual(yc["wtg_power"], 30.1)
        self.assertEqual(self.db.get_last_seq(), 1)

        # 增量更新只改给定字段
        self.db.update_scada_yc(seq=2, wtg_power=31.0)
        yc = self.db.get_scada_yc()
        self.assertEqual(yc["seq"], 2)
        self.assertAlmostEqual(yc["wtg_power"], 31.0)
        self.assertAlmostEqual(yc["wind_speed"], 8.5)  # 保留旧值

    def test_scada_yx(self):
        self.db.update_scada_yx(wtg_status=1, diesel_status=1)
        yx = self.db.get_scada_yx()
        self.assertEqual(yx["wtg_status"], 1)
        self.assertEqual(yx["diesel_status"], 1)
        self.db.update_scada_yx(wtg_status=0)
        self.assertEqual(self.db.get_scada_yx()["wtg_status"], 0)
        self.assertEqual(self.db.get_scada_yx()["diesel_status"], 1)

    def test_command_lifecycle(self):
        cid = self.db.add_command("scada_yt", "wtg_power", 35.0)
        pending = self.db.get_pending_commands("scada_yt")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["status"], "pending")
        self.db.mark_command_status("scada_yt", cid, "executed", executed_seq=9001)
        self.assertEqual(self.db.get_pending_commands("scada_yt"), [])
        cmds = self.db.get_commands("scada_yt")
        self.assertEqual(cmds[0]["status"], "executed")
        self.assertEqual(cmds[0]["executed_seq"], 9001)

    def test_history_and_dispatch_history(self):
        self.db.add_history(sim_time=1, wind_speed=8.5, load_power=45.2,
                            wtg_power=30.1, diesel_power=15.0)
        self.db.add_dispatch_history(
            sim_time=5, wind_speed=8.5, load_power=45.2, wtg_power=30.1,
            diesel_power=15.0, available_wind=100.0, wtg_power_set=35.0,
            diesel_power_set=10.2, diesel_reserve=139.8, renewable_share=0.77,
            balance_error=0.0, shortfall=0.0,
        )
        self.assertEqual(len(self.db.get_history()), 1)
        dh = self.db.get_dispatch_history()
        self.assertEqual(len(dh), 1)
        self.assertAlmostEqual(dh[0]["wtg_power_set"], 35.0)
        self.assertAlmostEqual(dh[0]["diesel_reserve"], 139.8)

    def test_log_and_comm_status(self):
        self.db.add_log("INFO", "test", "hello")
        self.assertEqual(len(self.db.get_logs()), 1)
        self.assertEqual(self.db.get_logs()[0]["message"], "hello")

        self.db.set_comm_status(connected=1, last_command_result="success")
        cs = self.db.get_comm_status()
        self.assertEqual(cs["connected"], 1)
        self.assertEqual(cs["last_command_result"], "success")


if __name__ == "__main__":
    unittest.main()
