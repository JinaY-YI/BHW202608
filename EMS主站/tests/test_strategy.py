"""strategy 模块单元测试：风电优先、柴发补偿、保留备用。"""
import unittest

from ems import strategy


class TestEstimateAvailablePower(unittest.TestCase):
    def test_below_cut_in(self):
        self.assertEqual(strategy.estimate_available_power(2.0, 3.0, 25.0, 12.0, 100.0), 0.0)

    def test_above_cut_out(self):
        self.assertEqual(strategy.estimate_available_power(26.0, 3.0, 25.0, 12.0, 100.0), 0.0)

    def test_at_rated(self):
        self.assertAlmostEqual(
            strategy.estimate_available_power(12.0, 3.0, 25.0, 12.0, 100.0), 100.0)

    def test_above_rated_below_cutout(self):
        self.assertAlmostEqual(
            strategy.estimate_available_power(18.0, 3.0, 25.0, 12.0, 100.0), 100.0)

    def test_cubic_interpolation(self):
        # 切入 3, 额定 12, 额定功率 100；风速 7.5 时 ratio=0.5 → 100*0.125=12.5
        p = strategy.estimate_available_power(7.5, 3.0, 25.0, 12.0, 100.0)
        self.assertAlmostEqual(p, 12.5, places=6)

    def test_zero_rated_wind_guard(self):
        # 参数异常时退化为线性，不抛异常
        p = strategy.estimate_available_power(5.0, 3.0, 25.0, 3.0, 100.0)
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 100.0)


class TestSchedulingDecision(unittest.TestCase):
    def _params(self, **kw):
        p = {"diesel_max": 150.0, "diesel_min": 10.0, "rated_power": 100.0,
             "cut_in_wind": 3.0, "cut_out_wind": 25.0, "rated_wind": 12.0}
        p.update(kw)
        return p

    def test_no_wind_diesel_compensates(self):
        # 无风 → 可用风电 0 → 风机 0，柴发补足负荷
        d = strategy.scheduling_decision(wind_speed=0.0, load_power=60.0, **self._params())
        self.assertAlmostEqual(d.wtg_power_set, 0.0)
        self.assertAlmostEqual(d.diesel_power_set, 60.0)
        self.assertAlmostEqual(d.available_wind, 0.0)
        self.assertAlmostEqual(d.shortfall, 0.0)

    def test_abundant_wind_curtail_and_diesel_min(self):
        # 额定风、低负荷：可用 100 > 负荷 40 → 柴发保持最小出力(旋转备用)，风机削减
        d = strategy.scheduling_decision(wind_speed=12.0, load_power=40.0, **self._params())
        self.assertAlmostEqual(d.diesel_power_set, 10.0)   # diesel_min
        self.assertAlmostEqual(d.wtg_power_set, 30.0)      # 40 - 10
        self.assertAlmostEqual(d.balance_error, 0.0)
        self.assertGreater(d.curtailed, 0.0)

    def test_moderate_wind_split(self):
        # 可用 12.5 kW，负荷 60 → 风机 12.5，柴发 47.5
        d = strategy.scheduling_decision(wind_speed=7.5, load_power=60.0, **self._params())
        self.assertAlmostEqual(d.wtg_power_set, 12.5, places=4)
        self.assertAlmostEqual(d.diesel_power_set, 47.5, places=4)
        self.assertAlmostEqual(d.balance_error, 0.0, places=4)

    def test_shortfall_when_diesel_capped(self):
        # 无风、负荷 200 > diesel_max 150 → 柴发顶格，出现缺口 50
        d = strategy.scheduling_decision(wind_speed=0.0, load_power=200.0, **self._params())
        self.assertAlmostEqual(d.diesel_power_set, 150.0)
        self.assertAlmostEqual(d.shortfall, 50.0, places=4)

    def test_wtg_set_within_rated_power(self):
        # 即使可用与负荷都很大，风机设定不超额定功率
        d = strategy.scheduling_decision(wind_speed=20.0, load_power=200.0, **self._params())
        self.assertLessEqual(d.wtg_power_set, 100.0)

    def test_diesel_within_bounds(self):
        for ws, lp in [(0.0, 0.0), (8.0, 50.0), (12.0, 300.0), (20.0, 10.0)]:
            d = strategy.scheduling_decision(wind_speed=ws, load_power=lp, **self._params())
            self.assertGreaterEqual(d.diesel_power_set, 10.0 - 1e-6)
            self.assertLessEqual(d.diesel_power_set, 150.0 + 1e-6)
            self.assertGreaterEqual(d.wtg_power_set, 0.0)
            self.assertLessEqual(d.wtg_power_set, 100.0 + 1e-6)

    def test_renewable_share(self):
        d = strategy.scheduling_decision(wind_speed=12.0, load_power=40.0, **self._params())
        self.assertAlmostEqual(d.renewable_share, 30.0 / 40.0, places=4)


class TestEvaluateDispatch(unittest.TestCase):
    def test_metrics(self):
        m = strategy.evaluate_dispatch(load_power=45.2, wtg_power=30.1, diesel_power=15.1,
                                       available_wind=100.0, diesel_max=150.0)
        self.assertAlmostEqual(m["balance_error"], 0.0, places=4)
        self.assertAlmostEqual(m["renewable_share"], 30.1 / 45.2, places=4)
        self.assertAlmostEqual(m["wind_utilization"], 30.1 / 100.0, places=4)
        self.assertAlmostEqual(m["diesel_reserve_ratio"], (150 - 15.1) / 150, places=4)


if __name__ == "__main__":
    unittest.main()
