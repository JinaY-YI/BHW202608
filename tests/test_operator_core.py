"""operator_core 调度进程单元测试。"""
import unittest

from ems.database import Database
from ems.operator_core import OperatorCore
from tests.util import make_temp_db


class TestOperatorCore(unittest.TestCase):
    def setUp(self):
        self.db = Database(make_temp_db())
        self.core = OperatorCore(self.db)

    def tearDown(self):
        self.db.close()

    def _seed_scada(self, wind_speed, load_power, wtg_power, diesel_power, sim_time=1):
        self.db.update_scada_yc(
            seq=1, sim_time=sim_time, wind_speed=wind_speed, load_power=load_power,
            wtg_power=wtg_power, diesel_power=diesel_power,
        )
        self.db.update_scada_yx(wtg_status=1 if wtg_power > 0 else 0,
                                diesel_status=1 if diesel_power > 0 else 0)

    def test_collect_snapshot_updates_env_and_device(self):
        self._seed_scada(8.5, 45.2, 30.1, 15.0)
        self.core._collect_snapshot()
        env = self.db.get_env_realtime()
        self.assertAlmostEqual(env["wind_speed"], 8.5)
        self.assertAlmostEqual(env["load_power"], 45.2)
        dev = self.db.get_device_realtime()
        self.assertAlmostEqual(dev["wtg_power"], 30.1)
        self.assertAlmostEqual(dev["diesel_power"], 15.0)
        self.assertEqual(len(self.db.get_history()), 1)

    def test_dispatch_closed_loop_writes_commands(self):
        self._seed_scada(0.0, 60.0, 0.0, 60.0)
        self.db.set_sched_params(ctrl_mode=1)
        self.core._dispatch(1)
        # 闭环 → 写遥调表（风机 + 柴发各一条）
        pending = self.db.get_pending_commands("scada_yt")
        targets = {c["target"] for c in pending}
        self.assertEqual(targets, {"wtg_power", "diesel_power"})
        # 无风场景：风机设定 0，柴发设定 60
        values = {c["target"]: c["value"] for c in pending}
        self.assertAlmostEqual(values["wtg_power"], 0.0)
        self.assertAlmostEqual(values["diesel_power"], 60.0)
        # 调度历史已记录
        self.assertEqual(len(self.db.get_dispatch_history()), 1)

    def test_dispatch_open_loop_does_not_write_commands(self):
        self._seed_scada(0.0, 60.0, 0.0, 60.0)
        self.core._dispatch(0)
        self.assertEqual(self.db.get_pending_commands("scada_yt"), [])
        # 但调度历史仍记录
        self.assertEqual(len(self.db.get_dispatch_history()), 1)

    def test_dispatch_no_duplicate_when_unchanged(self):
        self._seed_scada(12.0, 40.0, 30.0, 10.0)
        self.core._dispatch(1)
        first = len(self.db.get_pending_commands("scada_yt"))
        self.assertEqual(first, 2)
        # 同断面再次调度，设定值不变 → 不新增命令
        self.core._dispatch(1)
        self.assertEqual(len(self.db.get_pending_commands("scada_yt")), 2)

    def test_dispatch_changed_setpoint_adds_new_command(self):
        self._seed_scada(12.0, 40.0, 30.0, 10.0)
        self.core._dispatch(1)
        # 负荷变化 → 风机设定值变化（30→50），柴发设定不变（10→10）
        self._seed_scada(12.0, 60.0, 30.0, 30.0)
        self.core._dispatch(1)
        pending = self.db.get_pending_commands("scada_yt")
        # 第一次 2 条（风机+柴发），第二次仅风机变化 1 条 → 共 3 条
        self.assertEqual(len(pending), 3)
        values = {c["target"]: c["value"] for c in pending}
        self.assertAlmostEqual(values["wtg_power"], 50.0)
        self.assertAlmostEqual(values["diesel_power"], 10.0)

    def test_dispatch_sets_device_realtime_setpoints(self):
        self._seed_scada(12.0, 40.0, 30.0, 10.0)
        self.core._dispatch(1)
        dev = self.db.get_device_realtime()
        self.assertAlmostEqual(dev["wtg_power_set"], 30.0)
        self.assertAlmostEqual(dev["diesel_power_set"], 10.0)
        self.assertAlmostEqual(dev["available_wind"], 100.0)


if __name__ == "__main__":
    unittest.main()
