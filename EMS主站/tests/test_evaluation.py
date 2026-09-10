"""evaluation 评分模块单元测试。"""
import unittest

from ems.database import Database
from ems.evaluation import _larger_better, _smaller_better, compute_scores
from tests.util import make_temp_db


def _item_map(role_scores, key):
    """把 items 列表 [(名称, 得分, 满分), ...] 转成 {名称: 得分}。"""
    return {name: score for name, score, _full in role_scores[key]["items"]}


class TestScoreHelpers(unittest.TestCase):
    def test_smaller_better(self):
        # 越小越好：<=1 满分，>=15 零分
        self.assertAlmostEqual(_smaller_better(0.0, 1.0, 15.0), 100.0)
        self.assertAlmostEqual(_smaller_better(1.0, 1.0, 15.0), 100.0)
        self.assertAlmostEqual(_smaller_better(15.0, 1.0, 15.0), 0.0)
        self.assertAlmostEqual(_smaller_better(20.0, 1.0, 15.0), 0.0)
        mid = _smaller_better(8.0, 1.0, 15.0)
        self.assertGreater(mid, 0.0)
        self.assertLess(mid, 100.0)

    def test_larger_better(self):
        # 越大越好：>=0.95 满分，<=0 零分
        self.assertAlmostEqual(_larger_better(0.95, 0.95, 0.0), 100.0)
        self.assertAlmostEqual(_larger_better(1.0, 0.95, 0.0), 100.0)
        self.assertAlmostEqual(_larger_better(0.0, 0.95, 0.0), 0.0)
        mid = _larger_better(0.5, 0.95, 0.0)
        self.assertGreater(mid, 0.0)
        self.assertLess(mid, 100.0)


class TestComputeScores(unittest.TestCase):
    def setUp(self):
        self.db = Database(make_temp_db())

    def tearDown(self):
        self.db.close()

    def _seed(self, wind=8.0, load=50.0, wtg=30.0, diesel=20.0, wtg_status=1,
              connected=1, last_cmd="success", avail=40.0):
        self.db.update_scada_yc(seq=1, sim_time=10, wind_speed=wind, load_power=load,
                                wtg_power=wtg, diesel_power=diesel)
        self.db.update_scada_yx(wtg_status=wtg_status, diesel_status=1)
        self.db.set_device_realtime(wtg_power_set=wtg, diesel_power_set=diesel,
                                    available_wind=avail)
        self.db.set_comm_status(connected=connected, last_command_result=last_cmd)
        # 无缺额历史
        self.db.add_dispatch_history(sim_time=10, wind_speed=wind, load_power=load,
                                     wtg_power=wtg, diesel_power=diesel, available_wind=avail,
                                     wtg_power_set=wtg, diesel_power_set=diesel,
                                     diesel_reserve=60.0, renewable_share=wtg / load,
                                     balance_error=0.0, shortfall=0.0)

    def test_structure_and_range(self):
        s = compute_scores(self.db)
        for role in ("ems", "grid", "wind"):
            self.assertIn(role, s)
            self.assertIn("score", s[role])
            self.assertIn("items", s[role])
            self.assertGreaterEqual(s[role]["score"], 0.0)
            self.assertLessEqual(s[role]["score"], 100.0)
        self.assertIn("overall", s)

    def test_empty_db_no_crash(self):
        s = compute_scores(self.db)  # 空库，应能算出低分而不抛异常
        self.assertGreaterEqual(s["overall"], 0.0)
        self.assertLessEqual(s["overall"], 100.0)

    def test_balanced_dispatch_high_ems_score(self):
        # 功率完全平衡（wtg+diesel==load），风电利用率高，无缺额 → EMS 分应高
        self._seed(wind=12.0, load=50.0, wtg=45.0, diesel=5.0, avail=50.0)
        s = compute_scores(self.db)
        self.assertGreaterEqual(s["ems"]["score"], 80.0)

    def test_unbalanced_dispatch_low_balance(self):
        # 功率不平衡（缺 20kW）→ 平衡子项应低
        self._seed(load=50.0, wtg=10.0, diesel=20.0)  # wtg+diesel=30 != 50
        s = compute_scores(self.db)
        balance_item = _item_map(s, "ems")["功率平衡"]
        self.assertLess(balance_item, 50.0)

    def test_grid_offline_low_score(self):
        self._seed(connected=0, last_cmd=None)
        s = compute_scores(self.db)
        self.assertEqual(_item_map(s, "grid")["通信在线"], 0.0)

    def test_grid_online_ack_high_score(self):
        self._seed(connected=1, last_cmd="success")
        s = compute_scores(self.db)
        self.assertEqual(_item_map(s, "grid")["命令回执"], 100.0)

    def test_wind_offline_low_score(self):
        self._seed(wtg_status=0)
        s = compute_scores(self.db)
        self.assertEqual(_item_map(s, "wind")["风机在线"], 0.0)


if __name__ == "__main__":
    unittest.main()
