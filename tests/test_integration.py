"""端到端集成测试：完整 EMS（operator_io + operator_core）对接 MockGrid。

验证"测量-决策-执行-反馈"闭环：EMS 连接电网模拟器 → 采集遥测遥信 →
调度决策 → 下发遥调 → 电网模拟器执行 → EMS 观测到新的运行断面。
"""
import time
import unittest

from ems import mock_grid
from ems.database import Database
from ems.operator_core import OperatorCore
from ems.operator_io import OperatorIO
from tests.util import make_temp_db


def wait_until(predicate, timeout=15.0, interval=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class TestFullEMSIntegration(unittest.TestCase):
    def setUp(self):
        self.grid = mock_grid.MockGrid(port=0).start()
        self.db = Database(make_temp_db())
        # 加快调度：采集 1s、决策 2s、闭环
        self.db.set_sched_params(collect_period=1, decision_period=2, ctrl_mode=1)
        self.io = OperatorIO(self.db, host=self.grid.host, port=self.grid.port)
        self.core = OperatorCore(self.db)

    def tearDown(self):
        self.io.stop()
        self.core.stop()
        self.io.join(timeout=3)
        self.core.join(timeout=3)
        self.grid.stop()
        self.db.close()

    def test_closed_loop_connects_and_dispatches(self):
        # 恒定无风场景，保证测试确定性（无风：风机 0、柴发补足负荷 50）
        self.grid.scenario = lambda t: (0.0, 50.0)

        self.io.start()
        self.core.start()

        # 1) 建立连接
        self.assertTrue(
            wait_until(lambda: self.db.get_comm_status().get("connected") == 1, timeout=8),
            "EMS 未能在超时内连接电网模拟器")

        # 2) 收到真实运行断面（负荷 50、柴发 50）
        self.assertTrue(
            wait_until(
                lambda: abs((self.db.get_scada_yc().get("load_power") or 0) - 50.0) < 0.01
                and abs((self.db.get_scada_yc().get("diesel_power") or 0) - 50.0) < 0.01,
                timeout=10),
            "EMS 未收到真实遥测断面")

        # 3) 调度决策收敛：无风下柴发设定收敛到 50、风机设定 0
        self.assertTrue(
            wait_until(
                lambda: abs((self.db.get_device_realtime().get("diesel_power_set") or -1) - 50.0) < 0.5,
                timeout=10),
            "调度决策未收敛到预期设定值")

        # 4) 指令已下发并收到回执（executed）
        self.assertTrue(
            wait_until(
                lambda: any(c["status"] == "executed" for c in self.db.get_commands("scada_yt")),
                timeout=5),
            "调度指令未被执行回执")

        # 5) 闭环收敛校验
        yc = self.db.get_scada_yc()
        self.assertAlmostEqual(yc["load_power"], 50.0, places=1)
        self.assertAlmostEqual(yc["wtg_power"], 0.0, places=1)
        self.assertAlmostEqual(yc["diesel_power"], 50.0, places=1)

        dev = self.db.get_device_realtime()
        self.assertAlmostEqual(dev["wtg_power_set"], 0.0, places=1)
        self.assertAlmostEqual(dev["diesel_power_set"], 50.0, places=1)

        # 电网模拟器已实际应用 EMS 下发的风机设定
        self.assertAlmostEqual(self.grid.wtg_power_set, dev["wtg_power_set"], places=1)

        # 历史与日志齐全（可观察与追溯）
        self.assertGreater(len(self.db.get_history()), 0)
        self.assertGreater(len(self.db.get_logs()), 0)

    def test_reconnect_after_grid_stops(self):
        self.io.start()
        self.core.start()
        self.assertTrue(
            wait_until(lambda: self.db.get_comm_status().get("connected") == 1, timeout=8))

        # 模拟电网模拟器断开 → EMS 应识别并进入重连
        self.grid.stop()
        self.assertTrue(
            wait_until(lambda: self.db.get_comm_status().get("connected") == 0, timeout=20),
            "EMS 未识别对端断开")

        # 日志中应记录断开告警
        logs = [l["message"] for l in self.db.get_logs()]
        self.assertTrue(any("断开" in m or "失败" in m for m in logs))


if __name__ == "__main__":
    unittest.main()
