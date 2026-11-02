"""本轮新增能力测试：提醒冷却、通用站点、Web 面板数据、清单导入导出、尺码提醒。"""
import json
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import main
from core.models import (InventorySnapshot, PricePoint, Product, VariantSnapshot)
from core.storage import Store
from monitors.generic import GenericMonitor, _extract_name, _extract_price
from webui.dashboard import build_summary, product_payload


class TestAlertCooldown(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def test_first_alert_allowed_then_cooldown_blocks(self):
        cd = {"drop": 12}
        now = time.time()
        self.assertTrue(main._alert_allowed(self.store, "jd:1", "drop", cd, now))
        self.store.mark_alert("jd:1", "drop", now)
        self.assertFalse(main._alert_allowed(self.store, "jd:1", "drop", cd, now + 60))
        # 超过冷却期后恢复
        self.assertTrue(main._alert_allowed(self.store, "jd:1", "drop", cd, now + 13 * 3600))

    def test_zero_cooldown_always_allowed(self):
        cd = {"target": 0}
        self.store.mark_alert("jd:1", "target")
        self.assertTrue(main._alert_allowed(self.store, "jd:1", "target", cd, time.time()))

    def test_config_can_override_globally_or_partially(self):
        self.assertEqual(main._cooldowns({})["drop"], 12)
        self.assertEqual(main._cooldowns({"alert_cooldown_hours": 2})["drop"], 2)
        cd = main._cooldowns({"alert_cooldown_hours": {"drop": 1}})
        self.assertEqual(cd["drop"], 1)
        self.assertEqual(cd["low"], 24)

    def test_variant_restock_detected(self):
        p = Product("jd", "1", "u", watched_variant="M")
        self.store.add_product(p)
        now = time.time()
        self.store.add_variant(p.display_id(), VariantSnapshot(now - 300, "M", False, "无货"))

        class FakeMonitor:
            def variants(self, pid):
                return [VariantSnapshot(now, "M", True, "有货")]

        texts = main._check_variants(self.store, FakeMonitor(), p)
        self.assertTrue(any("尺码 M" in t and "补货" in t for t in texts))


class _FakeResp:
    def __init__(self, text):
        self.text = text
        self.encoding = "utf-8"
        self.status_code = 200


class TestGenericMonitor(unittest.TestCase):
    CFG = {"sites": {"shop.example.com": {
        "price_regex": r'"price"\s*:\s*"?([0-9.]+)',
        "stock_regex": "有货|立即购买",
        "stockout_regex": "售罄|已抢光",
        "name_regex": "<title>(.+?)</title>",
    }}}

    def test_extract_price_with_currency(self):
        self.assertEqual(_extract_price("价格 ¥1,299.00 元", r"¥([0-9,.]+)"), 1299.0)

    def test_extract_price_no_group(self):
        self.assertEqual(_extract_price("49.90", r"[0-9.]+"), 49.9)

    def test_extract_price_invalid(self):
        self.assertIsNone(_extract_price("abc", r"([0-9.]+)"))
        self.assertIsNone(_extract_price("0", r"([0-9.]+)"))

    def test_extract_name(self):
        self.assertEqual(_extract_name("<title>羽绒服 - 商城</title>", "<title>(.+?)</title>"),
                         "羽绒服 - 商城")

    def test_fetch_price_and_stock(self):
        html = '<html><title>测试羽绒服</title><script>{"price":"599.5"}</script>有货 立即购买</html>'
        mon = GenericMonitor(self.CFG)
        with mock.patch.object(GenericMonitor, "http_get", return_value=_FakeResp(html)):
            point, stock = mon.fetch("https://shop.example.com/item/1")
        self.assertEqual(point.price, 599.5)
        self.assertTrue(stock.in_stock)
        self.assertEqual(mon.last_name, "测试羽绒服")

    def test_fetch_stockout(self):
        html = '<html><script>{"price":"599"}</script>已抢光</html>'
        mon = GenericMonitor(self.CFG)
        with mock.patch.object(GenericMonitor, "http_get", return_value=_FakeResp(html)):
            _point, stock = mon.fetch("https://shop.example.com/item/2")
        self.assertFalse(stock.in_stock)

    def test_missing_site_rule_raises(self):
        mon = GenericMonitor({"sites": {}})
        with self.assertRaises(Exception):
            mon.fetch("https://unknown.example.org/p/1")


class TestDashboardPayload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.p = Product("jd", "100", "https://item.jd.com/100.html", name="羽绒服",
                         category="down_jacket", launch_date="2026-09-01", target_price=500,
                         watched_variant="M")
        self.store.add_product(self.p)
        now = time.time()
        for i, price in enumerate([899, 869, 819, 759, 779]):
            self.store.add_price(self.p.display_id(),
                                 PricePoint(now - (4 - i) * 86400, float(price)))
        self.store.add_inventory(self.p.display_id(), InventorySnapshot(now, True, "现货", 12))
        self.store.add_variant(self.p.display_id(), VariantSnapshot(now, "M", True, "有货"))

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def test_summary_shape(self):
        data = build_summary(self.store, date(2026, 10, 31))
        self.assertGreaterEqual(data["count"], 1)
        item = data["products"][0]
        self.assertEqual(item["current"], 779)
        self.assertEqual(item["low"], 759)
        self.assertEqual(len(item["points"]), 5)
        self.assertTrue(item["points"][0][0] < item["points"][-1][0])
        self.assertEqual(item["inventory"]["quantity"], 12)
        self.assertEqual(item["variants"][0]["variant"], "M")
        self.assertIn(item["action"], ("立即购买", "尽快入手", "建议等待", "关注补货"))
        self.assertTrue(item["spark"])

    def test_product_payload_missing_data(self):
        p2 = Product("jd", "200", "https://item.jd.com/200.html")
        self.store.add_product(p2)
        data = product_payload(self.store, p2, date(2026, 10, 31))
        self.assertEqual(data["points"], [])
        self.assertIsNone(data["current"])
        self.assertEqual(data["samples"], 0)


class TestImportExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbdir = Path(self.tmp.name)
        self.store = Store(self.dbdir / "a.db")
        self.p = Product("jd", "300", "https://item.jd.com/300.html", name="外套",
                         category="winter_coat", target_price=400, watched_variant="L")
        self.store.add_product(self.p)

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def test_export_then_import_roundtrip(self):
        out = self.dbdir / "wish.json"
        main.cmd_export(SimpleNamespace(out=str(out), with_prices=True), self.store, {})
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["products"][0]["target_price"], 400)

        other = Store(self.dbdir / "b.db")
        main.cmd_import(SimpleNamespace(file=str(out), overwrite=False), other, {})
        got = other.get_product("jd:300")
        self.assertIsNotNone(got)
        self.assertEqual(got.watched_variant, "L")
        other.conn.close()

    def test_import_skips_existing_without_overwrite(self):
        out = self.dbdir / "wish.json"
        main.cmd_export(SimpleNamespace(out=str(out), with_prices=False), self.store, {})
        cmd = main.cmd_import(SimpleNamespace(file=str(out), overwrite=False), self.store, {})
        self.assertEqual(cmd, 0)
        # 再覆盖导入应更新字段
        data = json.loads(out.read_text(encoding="utf-8"))
        data["products"][0]["target_price"] = 350
        out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        main.cmd_import(SimpleNamespace(file=str(out), overwrite=True), self.store, {})
        self.assertEqual(self.store.get_product("jd:300").target_price, 350)


if __name__ == "__main__":
    unittest.main()
