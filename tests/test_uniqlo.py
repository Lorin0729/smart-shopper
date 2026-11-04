"""优衣库链路测试：链接识别、解析（正常/缺货/结构变更/异常）、库存、缓存、限流重试。

全部使用本地 fixture，不依赖真实网络。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.models import Product, VariantSnapshot, PLATFORM_UNIQLO
from core.storage import Store
from core.variants import match_variants, split_watched
from monitors import MonitorError, parse_product_url, resolve_product_url
from monitors.uniqlo import (PRODUCT_PAGE, PRODINFO_JSON, SPU_API, SPU_JSON,
                             STOCK_API, UniqloMonitor)
from monitors.uniqlo_parse import (parse_page_html, parse_product_code,
                                   parse_spu_json, parse_stock_json)

FIXTURES = Path(__file__).parent / "fixtures" / "uniqlo"
PID = "u0000000066172"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str) -> dict:
    return json.loads(fixture(name))


class TestProductCode(unittest.TestCase):
    def test_supported_link_forms(self):
        cases = {
            "u0000000066172": "u0000000066172",
            "U0000000066172": "u0000000066172",
            "https://www.uniqlo.cn/product-detail.html?productCode=u0000000066172": PID,
            "https://www.uniqlo.cn/product-detail.html?utm_source=x&productCode=u0000000066172#detail": PID,
            "https://h.uniqlo.cn/?type=pc#/product?pid=u0000000066172": PID,
            "https://www.uniqlo.cn/zh_CN/u0000000066172.html": PID,
            "https%3A%2F%2Fwww.uniqlo.cn%2Fproduct-detail.html%3FproductCode%3Du0000000066172": PID,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_product_code(raw), expected)

    def test_invalid_link_has_clear_message(self):
        with self.assertRaises(MonitorError) as ctx:
            parse_product_code("https://www.uniqlo.cn/sale.html")
        message = str(ctx.exception)
        self.assertIn("无法从链接识别", message)
        self.assertIn("productCode", message)

    def test_parse_product_url_integration(self):
        platform, pid = parse_product_url("https://www.uniqlo.cn/product-detail.html?productCode=U0000000066172")
        self.assertEqual((platform, pid), (PLATFORM_UNIQLO, PID))

    def test_short_link_expanded_only_when_needed(self):
        response = mock.Mock(
            url=f"https://www.uniqlo.cn/product-detail.html?productCode={PID}", text=""
        )
        with mock.patch("monitors.uniqlo.requests.get", return_value=response) as get:
            platform, pid = resolve_product_url("https://u.uniqlo.cn/abc123")
        self.assertEqual((platform, pid), (PLATFORM_UNIQLO, PID))
        self.assertEqual(get.call_count, 1)

        # 普通链接完全本地解析，不触网
        with mock.patch("monitors.uniqlo.requests.get") as get:
            parse_product_url(f"https://www.uniqlo.cn/product-detail.html?productCode={PID}")
        get.assert_not_called()


class TestParseSpuJson(unittest.TestCase):
    def test_normal_matrix(self):
        product = parse_spu_json(fixture_json("spu_normal.json"), code=PID)
        self.assertIsNotNone(product)
        self.assertEqual(product.name, "摇粒绒高领上衣")
        self.assertEqual(product.price, 199)
        self.assertEqual(product.original_price, 299)
        self.assertEqual(len(product.skus), 6)
        self.assertTrue(product.limited)
        labels = [s.label for s in product.skus]
        self.assertIn("乳白色 / M", labels)
        self.assertEqual([s.size for s in product.skus if s.color == "乳白色"], ["S", "M", "L"])

        m = next(s for s in product.skus if s.label == "乳白色 / M")
        self.assertEqual(m.price, 199)
        self.assertAlmostEqual(m.discount, 1 - 199 / 299, places=3)
        # 没有库存数据时不要伪造有货
        self.assertIsNone(m.in_stock)
        self.assertEqual(product.snapshots(1.0), [])

    def test_stock_merge(self):
        product = parse_spu_json(fixture_json("spu_normal.json"), code=PID)
        info = parse_stock_json(fixture_json("stock_normal.json"))
        self.assertIsNotNone(info)
        self.assertEqual(len(info.mapping), 6)
        info.apply(product)

        self.assertTrue(product.in_stock)
        self.assertEqual(product.total_quantity, 17)
        by_label = {s.label: s for s in product.skus}
        self.assertFalse(by_label["乳白色 / M"].in_stock)
        self.assertEqual(by_label["乳白色 / M"].quantity, 0)
        self.assertEqual(by_label["乳白色 / L"].quantity, 12)
        self.assertTrue(by_label["黑色 / L"].in_stock)
        self.assertEqual(len(product.snapshots(1.0)), 6)

    def test_soldout_page(self):
        product = parse_spu_json(fixture_json("spu_normal.json"), code=PID)
        info = parse_stock_json(fixture_json("stock_soldout.json"))
        info.apply(product)
        self.assertFalse(product.in_stock)
        self.assertEqual(product.stock_state, "无货")
        self.assertTrue(all(not s.in_stock for s in product.skus))
        self.assertEqual(product.total_quantity, 0)

    def test_structure_change_payload(self):
        product = parse_spu_json(fixture_json("spu_v2.json"), code=PID)
        self.assertIsNotNone(product)
        self.assertEqual(product.name, "高级轻型羽绒服")
        self.assertEqual(product.price, 399)
        self.assertEqual(product.original_price, 599)
        self.assertEqual(len(product.skus), 3)
        sizes = sorted(s.size for s in product.skus if s.color == "藏青色")
        self.assertEqual(sizes, ["L", "M"])
        m = next(s for s in product.skus if s.label == "米色 / M")
        self.assertEqual(m.quantity, 3)
        self.assertTrue(m.in_stock)


class TestParseHtml(unittest.TestCase):
    def test_jsonld_page(self):
        product = parse_page_html(fixture("page_jsonld.html"), code=PID)
        self.assertIsNotNone(product)
        self.assertEqual(product.price, 599)
        self.assertEqual(product.name, "高级轻型羽绒服")
        self.assertTrue(product.in_stock)

    def test_embedded_page(self):
        product = parse_page_html(fixture("page_embedded.html"), code=PID)
        self.assertIsNotNone(product)
        self.assertEqual(product.price, 99)
        self.assertEqual(product.name, "法兰绒格子衬衫")
        self.assertEqual(len(product.skus), 2)

    def test_visible_price_fallback(self):
        product = parse_page_html("<title>某商品 - 优衣库</title><div class='price'>￥299</div>")
        self.assertIsNotNone(product)
        self.assertEqual(product.price, 299)

    def test_blocked_page_returns_none(self):
        self.assertIsNone(parse_page_html(fixture("page_blocked.html")))


class TestVariantMatching(unittest.TestCase):
    def setUp(self):
        product = parse_spu_json(fixture_json("spu_normal.json"), code=PID)
        parse_stock_json(fixture_json("stock_normal.json")).apply(product)
        self.variants = product.snapshots(1.0)

    def test_split_watched(self):
        self.assertEqual(split_watched("黑色 / M"), ("黑色", "M"))
        self.assertEqual(split_watched("M"), ("", "M"))
        self.assertEqual(split_watched("175/92A"), ("", "175/92A"))
        self.assertEqual(split_watched("黑色 M"), ("黑色", "M"))

    def test_match_size_only(self):
        matched = match_variants(self.variants, "M")
        self.assertEqual({v.color for v in matched}, {"乳白色", "黑色"})
        self.assertEqual(len(matched), 2)

    def test_match_color_and_size(self):
        matched = match_variants(self.variants, "乳白色 M")
        self.assertEqual([v.variant for v in matched], ["乳白色 / M"])

    def test_match_color_only(self):
        self.assertEqual(len(match_variants(self.variants, "黑色")), 3)


class _Transport:
    """把 URL 映射到固定的 (status, text, headers)，可记录调用次数。"""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls = []

    def __call__(self, method: str, url: str, body=None):
        self.calls.append((method, url))
        item = self.responses.get(url)
        if item is None:
            return 404, "not found", {}
        if callable(item):
            return item()
        return item


def _monitor(responses: dict, **overrides) -> tuple[UniqloMonitor, _Transport]:
    transport = _Transport(responses)
    config = {
        "use_builtin_apis": True,
        "stock_api": True,
        "mobile_page": False,
        "playwright_fallback": False,
        "retry_times": 0,
        "retry_backoff_seconds": 0,
        "cache_ttl_seconds": 300,
    }
    config.update(overrides)
    return UniqloMonitor(config, transport=transport), transport


class TestMonitorChain(unittest.TestCase):
    def _ok_responses(self):
        return {
            SPU_JSON.format(code=PID): (200, fixture("spu_normal.json"), {}),
            STOCK_API: (200, fixture("stock_normal.json"), {}),
        }

    def test_fetch_and_variants_share_cache(self):
        monitor, transport = _monitor(self._ok_responses())
        point, inventory = monitor.fetch(PID)
        self.assertEqual(point.price, 199)
        self.assertEqual(point.original_price, 299)
        self.assertTrue(inventory.in_stock)
        self.assertEqual(inventory.quantity, 17)

        variants = monitor.variants(PID)
        self.assertEqual(len(variants), 6)
        self.assertTrue(monitor.cache_hit)
        self.assertEqual(len(transport.calls), 2)  # 商品 JSON + 库存，各一次

    def test_repeated_fetch_uses_cache(self):
        monitor, transport = _monitor(self._ok_responses())
        monitor.fetch(PID)
        monitor.fetch(PID)
        self.assertTrue(monitor.cache_hit)
        self.assertEqual(len(transport.calls), 2)

    def test_cache_ttl_zero_refetches(self):
        monitor, transport = _monitor(self._ok_responses(), cache_ttl_seconds=0)
        monitor.fetch(PID)
        monitor.variants(PID)
        self.assertFalse(monitor.cache_hit)
        self.assertEqual(len(transport.calls), 4)

    def test_429_retry_then_success(self):
        state = {"count": 0}

        def flaky():
            state["count"] += 1
            if state["count"] == 1:
                return 429, "limited", {"Retry-After": "0"}
            return 200, fixture("spu_normal.json"), {}

        monitor, transport = _monitor(
            {SPU_JSON.format(code=PID): flaky, STOCK_API: (200, fixture("stock_normal.json"), {})},
            retry_times=1,
        )
        point, _ = monitor.fetch(PID)
        self.assertEqual(point.price, 199)
        self.assertEqual(state["count"], 2)

    def test_403_falls_back_to_page(self):
        responses = {
            SPU_JSON.format(code=PID): (403, "blocked", {}),
            SPU_API.format(code=PID): (404, "not found", {}),
            PRODINFO_JSON.format(code=PID): (404, "not found", {}),
            PRODUCT_PAGE.format(code=PID): (200, fixture("page_jsonld.html"), {}),
        }
        monitor, _ = _monitor(responses)
        point, inventory = monitor.fetch(PID)
        self.assertEqual(point.price, 599)
        self.assertEqual(monitor.last_source, "商品页 HTML")
        self.assertTrue(any("403" in detail for _n, detail, _k in monitor.last_attempts))

    def test_all_candidates_fail_with_diagnostics(self):
        responses = {
            SPU_JSON.format(code=PID): (404, "not found", {}),
            SPU_API.format(code=PID): (404, "not found", {}),
            PRODINFO_JSON.format(code=PID): (404, "not found", {}),
            PRODUCT_PAGE.format(code=PID): (200, fixture("page_blocked.html"), {}),
        }
        monitor, _ = _monitor(responses)
        with self.assertRaises(MonitorError) as ctx:
            monitor.fetch(PID)
        message = str(ctx.exception)
        self.assertIn("已尝试", message)
        self.assertIn("官方商品 JSON", message)
        self.assertIn("建议", message)
        self.assertTrue(monitor.last_attempts)


class TestStorageVariants(unittest.TestCase):
    def test_variant_roundtrip_keeps_new_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "test.db")
            product = Product(platform=PLATFORM_UNIQLO, pid=PID, url="", name="测试")
            store.add_product(product)
            snap = VariantSnapshot(
                ts=100.0, variant="黑色 / M", in_stock=True, state="有货", quantity=3,
                color="黑色", size="M", sku_id="S1", price=199,
                original_price=299, member_price=189, limit=2,
            )
            store.add_variants(product.display_id(), [snap])
            got = store.latest_variants(product.display_id())[0]
            self.assertEqual((got.color, got.size, got.sku_id), ("黑色", "M", "S1"))
            self.assertEqual(got.price, 199)
            self.assertEqual(got.original_price, 299)
            self.assertEqual(got.member_price, 189)
            self.assertEqual(got.limit, 2)
            self.assertAlmostEqual(got.discount, 1 - 199 / 299, places=3)
            store.close()


class TestVariantAlerts(unittest.TestCase):
    def test_restock_and_soldout_transitions(self):
        from main import _check_variants

        class FakeMonitor:
            def __init__(self, stock):
                self.stock = stock

            def variants(self, pid):
                return [VariantSnapshot(ts=1.0, variant="乳白色 / M", in_stock=self.stock,
                                         color="乳白色", size="M")]

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "test.db")
            p = Product(platform=PLATFORM_UNIQLO, pid=PID, url="", name="测试",
                        watched_variant="乳白色 M")
            store.add_product(p)
            did = p.display_id()

            self.assertEqual(_check_variants(store, FakeMonitor(False), p), [])
            alerts = _check_variants(store, FakeMonitor(True), p)
            self.assertEqual(len(alerts), 1)
            self.assertIn("restock", alerts[0][0])
            self.assertIn("已补货", alerts[0][1])

            self.assertEqual(_check_variants(store, FakeMonitor(True), p), [])
            alerts = _check_variants(store, FakeMonitor(False), p)
            self.assertEqual(len(alerts), 1)
            self.assertIn("soldout", alerts[0][0])
            store.close()


if __name__ == "__main__":
    unittest.main()
