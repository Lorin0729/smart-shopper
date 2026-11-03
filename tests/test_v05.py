"""v0.5 能力测试：币种、尺码助手、趋势回归、监控健康度、通知渠道、面板字段。"""
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

from core.currency import Currency, from_config
from core.models import PricePoint, Product
from core.storage import Store
from notifier import Notifier
from predictor import sizing
from predictor.forecast import PriceHistory, trend_forecast
from webui.dashboard import build_summary


class TestCurrency(unittest.TestCase):
    def test_cny_default(self):
        c = Currency("CNY")
        self.assertEqual(c.format(1299), "¥1,299")
        self.assertEqual(c.format(None), "-")

    def test_usd_conversion(self):
        c = Currency("USD")
        self.assertAlmostEqual(c.convert(100), 14.0)
        self.assertEqual(c.format(100), "$14.00")

    def test_custom_rate_overrides(self):
        c = Currency("JPY", {"JPY": 20.0})
        self.assertEqual(c.format(100), "¥2,000")

    def test_from_config_string_and_dict(self):
        self.assertEqual(from_config({"currency": "EUR"}).code, "EUR")
        c = from_config({"currency": {"code": "USD", "rates": {"USD": 7.2}}})
        self.assertEqual(c.code, "USD")
        self.assertAlmostEqual(c.rate, 7.2)
        self.assertEqual(from_config({}).code, "CNY")

    def test_describe(self):
        self.assertIn("人民币", Currency("CNY").describe())
        self.assertIn("USD", Currency("USD").describe())


class TestSizing(unittest.TestCase):
    def test_recommend_top_by_measurements(self):
        m = sizing.recommend_top("men", height=178, chest=100)
        self.assertEqual(m.row["alpha"], "L")
        self.assertTrue(m.exact)

    def test_recommend_top_out_of_range_has_note(self):
        m = sizing.recommend_top("men", chest=130)
        self.assertFalse(m.exact)
        self.assertTrue(m.note)

    def test_alpha_lookup_chain(self):
        row = next(r for r in sizing.tops_table("women") if r["alpha"] == "M")
        self.assertEqual(row["cn"], "160/84A")
        self.assertIn("美码", sizing.format_top_row(row))

    def test_shoe_cn_to_eu(self):
        m = sizing.convert_shoe("men", cn=260)
        self.assertEqual(m.row["eu"], 42)
        self.assertTrue(m.exact)

    def test_shoe_eu_to_others(self):
        m = sizing.convert_shoe("women", eu=38)
        self.assertEqual(m.row["cn"], 240)
        self.assertEqual(m.row["us"], 7.5)

    def test_shoe_nonstandard_value(self):
        m = sizing.convert_shoe("men", eu=43.5)
        self.assertFalse(m.exact)
        self.assertTrue(m.note)

    def test_missing_input_raises(self):
        with self.assertRaises(ValueError):
            sizing.convert_shoe("men")
        with self.assertRaises(ValueError):
            sizing.recommend_top("men")


class TestTrendForecast(unittest.TestCase):
    def _history(self, start_price=900, step=-5, days=20, samples=11):
        now = time.time()
        pts = [PricePoint(now - (days - i * days / (samples - 1)) * 86400,
                          start_price + step * (i * days / (samples - 1)))
               for i in range(samples)]
        return PriceHistory(sorted(pts, key=lambda p: p.ts))

    def test_linear_downtrend_projection(self):
        fc = trend_forecast(self._history(), days=30)
        self.assertIsNotNone(fc)
        self.assertEqual(fc.direction, "down")
        self.assertAlmostEqual(fc.projected, 650, delta=6)
        self.assertLess(fc.low, fc.projected)
        self.assertGreater(fc.high, fc.projected)
        self.assertIn("天后约", fc.describe())

    def test_uptrend_direction(self):
        fc = trend_forecast(self._history(700, 5, 20, 11), days=30)
        self.assertEqual(fc.direction, "up")
        self.assertGreater(fc.projected, 800)

    def test_insufficient_data_returns_none(self):
        now = time.time()
        pts = [PricePoint(now - 86400 * i, 800.0) for i in range(3)]
        self.assertIsNone(trend_forecast(PriceHistory(pts), days=30, min_points=4))

    def test_zero_span_returns_none(self):
        now = time.time()
        pts = [PricePoint(now, float(p)) for p in (900, 880, 860, 840)]
        self.assertIsNone(trend_forecast(PriceHistory(pts)))


class TestHealth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.p = Product("jd", "1", "https://item.jd.com/1.html")
        self.store.add_product(self.p)

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def test_failures_accumulate_and_reset(self):
        h1 = self.store.record_fetch("jd:1", False, "超时")
        self.assertEqual(h1.consecutive_failures, 1)
        h2 = self.store.record_fetch("jd:1", False, "超时2")
        self.assertEqual(h2.consecutive_failures, 2)
        self.assertEqual(h2.last_error, "超时2")
        self.assertIsNone(h2.last_ok_ts)
        h3 = self.store.record_fetch("jd:1", True)
        self.assertEqual(h3.consecutive_failures, 0)
        self.assertTrue(h3.ok)
        self.assertIsNotNone(h3.last_ok_ts)
        self.assertEqual(h3.last_error, "")

    def test_all_health_and_cleanup(self):
        self.store.record_fetch("jd:1", False, "err")
        self.assertIn("jd:1", self.store.all_health())
        self.store.remove_product("jd:1")
        self.assertNotIn("jd:1", self.store.all_health())


class TestNotifierChannels(unittest.TestCase):
    def test_enabled_channels_lists_new_mobile_channels(self):
        n = Notifier({
            "bark_url": "https://api.day.app/key",
            "ntfy_topic": "https://ntfy.sh/topic",
            "pushdeer_key": "x",
            "serverchan_key": "s",
        })
        names = n.enabled_channels()
        for expected in ("Bark", "ntfy", "PushDeer", "Server酱"):
            self.assertIn(expected, names)

    def test_no_channels(self):
        self.assertEqual(Notifier({}).enabled_channels(), [])


class TestDashboardV05(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.p = Product("jd", "9", "https://item.jd.com/9.html", name="测试",
                         category="down_jacket", target_price=500)
        self.store.add_product(self.p)
        now = time.time()
        for i, price in enumerate([900, 880, 850, 820, 790, 770]):
            self.store.add_price("jd:9", PricePoint(now - (5 - i) * 2 * 86400, float(price)))
        self.store.record_fetch("jd:9", False, "接口超时")

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def test_summary_has_trend_health_currency(self):
        data = build_summary(self.store, date(2026, 10, 31),
                             cfg={"currency": {"code": "USD"}})
        item = data["products"][0]
        self.assertIsNotNone(item["trend"])
        self.assertEqual(item["trend"]["direction"], "down")
        self.assertGreater(item["trend"]["projected"], 0)
        self.assertEqual(item["health"]["failures"], 1)
        self.assertEqual(data["currency"]["code"], "USD")
        self.assertAlmostEqual(data["currency"]["rate"], 0.14)


if __name__ == "__main__":
    unittest.main()
