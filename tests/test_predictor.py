"""价格预测模块单元测试。"""
import time
import unittest
from datetime import date, timedelta

from core.models import InventorySnapshot, PricePoint
from predictor.forecast import (PriceHistory, predict_drop, recommend,
                                upcoming_promos)
from predictor.season import best_buy_windows, normalize_category, profile_of


def points_from_days(ago_to_price: dict[int, float]) -> list[PricePoint]:
    now = time.time()
    return [PricePoint(ts=now - d * 86400, price=p) for d, p in
            sorted(ago_to_price.items(), reverse=True)]


class TestHistory(unittest.TestCase):
    def test_stats_percentile_and_drops(self):
        pts = points_from_days({30: 899, 20: 869, 10: 759, 5: 799, 1: 779})
        st = PriceHistory(pts).stats()
        self.assertEqual(st.current, 779)
        self.assertEqual(st.low, 759)
        self.assertEqual(st.high, 899)
        self.assertEqual(st.drop_count, 3)
        self.assertTrue(0 <= st.percentile <= 1)

    def test_trend_down(self):
        pts = points_from_days({10: 900, 5: 850, 1: 800})
        self.assertEqual(PriceHistory(pts).trend(), "down")

    def test_empty_stats(self):
        self.assertIsNone(PriceHistory([]).stats())


class TestPromoCalendar(unittest.TestCase):
    def test_upcoming_contains_double11_in_october(self):
        today = date(2026, 10, 31)
        promos = upcoming_promos(today, ahead_days=90)
        names = [p.name for p in promos]
        self.assertIn("双11", names)
        self.assertIn("双12", names)

    def test_not_in_past(self):
        today = date(2026, 7, 1)
        promos = upcoming_promos(today, ahead_days=45)
        self.assertTrue(all(p.end >= today for p in promos))
        names = [p.name for p in promos]
        self.assertNotIn("618", names)  # 已结束
        self.assertIn("88会员节", names)


class TestSeason(unittest.TestCase):
    def test_normalize_chinese_alias(self):
        self.assertEqual(normalize_category("羽绒服"), "down_jacket")
        self.assertEqual(normalize_category("unknown-x"), "general")

    def test_down_jacket_clearance_window(self):
        launch = date(2026, 9, 1)
        windows = best_buy_windows("down_jacket", launch, today=date(2026, 10, 31))
        tags = [w.tag for w in windows]
        self.assertTrue(any("季末清仓" in t for t in tags))
        self.assertTrue(any("发售" in t for t in tags))
        clearance = next(w for w in windows if "季末清仓" in w.tag)
        self.assertEqual(clearance.start.month, 2)
        # 预期折扣 = 1 - 0.35 ≈ 6.5 折
        self.assertAlmostEqual(clearance.expected_discount, 0.65)

    def test_days_after_launch_window(self):
        launch = date(2026, 9, 1)
        windows = best_buy_windows("down_jacket", launch, today=date(2026, 10, 31))
        deep = next(w for w in windows if "发售" in w.tag)
        self.assertEqual(deep.start, launch + timedelta(days=90))
        self.assertEqual(deep.end, launch + timedelta(days=150))


class TestRecommend(unittest.TestCase):
    def _hist(self, **kw):
        return PriceHistory(points_from_days({30: 899, 20: 869, 10: 759, 5: 799, 1: 779}))

    def test_out_of_stock_watches_restock(self):
        hist = self._hist()
        pred = predict_drop(hist, today=date(2026, 10, 31))
        rec = recommend(hist, InventorySnapshot(time.time(), False, "无货"), pred, [])
        self.assertEqual(rec.action, "关注补货")

    def test_low_price_low_stock_act_fast(self):
        pts = points_from_days({30: 899, 20: 869, 10: 759, 1: 769})
        hist = PriceHistory(pts)
        inv = InventorySnapshot(time.time(), True, "现货", quantity=5)
        pred = predict_drop(hist, today=date(2026, 10, 31))
        rec = recommend(hist, inv, pred, [])
        self.assertEqual(rec.action, "尽快入手")

    def test_high_price_near_promo_suggests_wait(self):
        pts = points_from_days({30: 700, 10: 750, 1: 899})
        hist = PriceHistory(pts)
        inv = InventorySnapshot(time.time(), True, "现货")
        pred = predict_drop(hist, today=date(2026, 10, 31))
        rec = recommend(hist, inv, pred, [], today=date(2026, 10, 31))
        self.assertEqual(rec.action, "建议等待")


if __name__ == "__main__":
    unittest.main()
