"""组合购买、报告生成、历史数据导入、变体存储的单元测试。"""
import csv
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

from core.models import InventorySnapshot, PricePoint, Product, VariantSnapshot
from core.report import ProductReport, price_svg, render_report
from core.storage import Store
from datasource import load_csv, dedupe
from optimizer.bundle import plan_bundle
from optimizer.deals import CartItem, DiscountRule
from predictor.forecast import PriceHistory, predict_drop, recommend
from predictor import season


def _rules():
    return [
        DiscountRule("a1", "店A券 满600减80", "full_off", 80, 600, store="店A"),
        DiscountRule("cross", "跨店每满300减50", "every_full_off", 50, 300, step=300),
    ]


class TestBundle(unittest.TestCase):
    def test_bundle_triggers_tiers_and_suggests_filler(self):
        items = [CartItem("羽绒服", 800, "店A"), CartItem("围巾", 100, "店B")]
        plan = plan_bundle(items, _rules(), filler_max=150)
        # 店A 券后 720 + 店B 100 = 820，跨店 2 档
        self.assertEqual(plan.final_total, 720)
        self.assertTrue(any("120" in t or "×2" in t for t in plan.achieved_tiers))

    def test_filler_net_positive_preferred(self):
        items = [CartItem("外套", 500, "店A")]
        plan = plan_bundle(items, _rules(), filler_max=200)
        # 500 未达店A 600 门槛，差 100 → 减 80，净亏 20
        self.assertTrue(plan.fillers)
        self.assertLess(plan.fillers[0].net, 0)

    def test_separate_total_and_bundle_bonus(self):
        items = [CartItem("A", 400, "店A"), CartItem("B", 400, "店B")]
        plan = plan_bundle(items, _rules(), filler_max=100)
        self.assertGreater(plan.separate_total, 0)
        self.assertEqual(plan.bundle_bonus, round(plan.separate_total - plan.final_total, 2))

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            plan_bundle([], _rules())


class TestReport(unittest.TestCase):
    def test_svg_contains_polyline_and_target(self):
        now = time.time()
        pts = [PricePoint(now - (10 - i) * 86400, p) for i, p in enumerate([899, 860, 819])]
        svg = price_svg(pts, target=800)
        self.assertIn("<svg", svg)
        self.assertIn("polyline", svg)
        self.assertIn("目标", svg)

    def test_empty_points_placeholder(self):
        self.assertIn("暂无价格数据", price_svg([]))

    def test_render_report_writes_html(self):
        now = time.time()
        pts = [PricePoint(now - i * 86400, 700 + i) for i in range(5)][::-1]
        hist = PriceHistory(pts)
        stats = hist.stats()
        rec = recommend(hist, InventorySnapshot(now, True, "现货", 8),
                        predict_drop(hist), [])
        item = ProductReport(
            title="测试羽绒服", url="https://example.com", points=pts, stats=stats,
            recommendation=rec, inventory=InventorySnapshot(now, True, "现货", 8),
            variants=[VariantSnapshot(now, "M", True, "有货")], target_price=700,
        )
        with tempfile.TemporaryDirectory() as d:
            out = render_report([item], Path(d) / "r.html")
            html = out.read_text(encoding="utf-8")
        self.assertIn("测试羽绒服", html)
        self.assertIn("<svg", html)
        self.assertIn("目标价", html)
        self.assertIn("建议理由", html)


class TestDatasourceCSV(unittest.TestCase):
    def test_load_csv_with_header(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "h.csv"
            with open(p, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["datetime", "price"])
                w.writerow(["2026-09-01", 899])
                w.writerow(["2026-10-01", 819])
            pts = load_csv(p)
        self.assertEqual(len(pts), 2)
        self.assertEqual(pts[0].price, 899)
        self.assertLess(pts[0].ts, pts[1].ts)

    def test_dedupe_keeps_unique_ts(self):
        pts = [PricePoint(1.0, 100), PricePoint(1.0, 90), PricePoint(2.0, 80)]
        out = dedupe(pts)
        self.assertEqual(len(out), 2)


class TestStoreVariants(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.p = Product("jd", "100012043978", "https://item.jd.com/100012043978.html",
                         category="down_jacket", target_price=500, watched_variant="M")
        self.store.add_product(self.p)

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def test_product_roundtrip_with_new_fields(self):
        got = self.store.get_product(self.p.display_id())
        self.assertEqual(got.target_price, 500)
        self.assertEqual(got.watched_variant, "M")

    def test_latest_variants(self):
        now = time.time()
        self.store.add_variants(self.p.display_id(), [
            VariantSnapshot(now - 60, "M", False, "无货"),
            VariantSnapshot(now - 60, "L", True, "有货"),
            VariantSnapshot(now, "M", True, "有货"),
        ])
        latest = {v.variant: v for v in self.store.latest_variants(self.p.display_id())}
        self.assertTrue(latest["M"].in_stock)   # 取最新一条
        self.assertEqual(latest["L"].variant, "L")
        self.assertIsNone(self.store.last_variant(self.p.display_id(), "X"))


class TestSeasonSanity(unittest.TestCase):
    def test_down_jacket_window_months(self):
        w = season.best_buy_windows("羽绒服", date(2026, 9, 1), date(2026, 10, 31))
        clearance = next(x for x in w if "季末清仓" in x.tag)
        self.assertIn(clearance.start.month, (2, 3))


if __name__ == "__main__":
    unittest.main()
