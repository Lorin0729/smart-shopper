"""plan 完整购买方案与尺码推荐测试（离线，网络层用 FakeMonitor 注入）。"""
import io
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import main
from core.models import InventorySnapshot, PricePoint, Product, VariantSnapshot
from core.storage import Store
from predictor import sizing

TID = "679876543210"


def _variant(color, size, price, in_stock, qty=None):
    return VariantSnapshot(
        ts=time.time(), variant=f"{color} / {size}", in_stock=in_stock,
        state="有货" if in_stock else "无货", quantity=qty,
        color=color, size=size, price=price,
    )


class FakeMonitor:
    last_name = "测试羽绒服"

    def __init__(self, variants, price=899.0, in_stock=True):
        self._variants = variants
        self._price = price
        self._in_stock = in_stock

    def fetch(self, pid):
        return (PricePoint(time.time(), self._price),
                InventorySnapshot(time.time(), self._in_stock, "现货", 12))

    def variants(self, pid):
        return list(self._variants)


def _args(**kw):
    base = dict(key="taobao:" + TID, platform=None, coupons=None, vip=False,
                height=None, weight=None, waist=None, chest=None, gender="men",
                kind="auto", category=None)
    base.update(kw)
    return SimpleNamespace(**base)


class TestPlan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.p = Product("taobao", TID, f"https://item.taobao.com/item.htm?id={TID}",
                         name="测试羽绒服", category="羽绒服")
        self.store.add_product(self.p)

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def _run(self, variants, price=899.0, in_stock=True, **kw):
        buf = io.StringIO()
        with mock.patch("main.get_monitor",
                        return_value=FakeMonitor(variants, price, in_stock)), \
                redirect_stdout(buf):
            code = main.cmd_plan(_args(**kw), self.store, {})
        return code, buf.getvalue()

    def test_with_and_without_vip(self):
        variants = [
            _variant("乳白色", "M", 899, True, 4),
            _variant("乳白色", "L", 899, True, 6),
        ]
        code, out = self._run(variants, height=178, weight=70)
        self.assertEqual(code, 0)
        self.assertIn("【结论】", out)
        self.assertIn("无会员：", out)
        self.assertIn("有会员：", out)
        self.assertIn("88VIP", out)
        self.assertIn("尺码建议：身高 178 / 体重 70 → 推荐 L", out)
        self.assertIn("商品尺码「乳白色 / L」：有货，剩 6 件", out)
        self.assertIn("往年同类（羽绒服）", out)
        self.assertIn("季末清仓", out)

    def test_pants_waist_recommendation(self):
        p = Product("taobao", "2", "https://item.taobao.com/item.htm?id=2",
                    name="休闲长裤", category="裤装")
        self.store.add_product(p)
        variants = [_variant("黑色", "L", 299, True, 5),
                    _variant("黑色", "M", 299, True, 5)]
        code, out = self._run(variants, key=p.display_id(), waist=82, height=178)
        self.assertEqual(code, 0)
        self.assertIn("推荐 L", out)
        self.assertIn("腰围 82", out)
        self.assertIn("商品尺码「黑色 / L」：有货", out)

    def test_all_soldout_does_not_quote(self):
        variants = [_variant("乳白色", "L", 899, False, 0)]
        code, out = self._run(variants, in_stock=False, height=178)
        self.assertEqual(code, 0)
        self.assertIn("【结论】关注", out)
        self.assertIn("无货/无价格", out)
        self.assertIn("暂不计算叠券", out)
        self.assertNotIn("无会员：", out)

    def test_bare_number_without_platform_asks(self):
        other = Store(Path(self.tmp.name) / "bare.db")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main.cmd_plan(_args(key="478897"), other, {})
        out = buf.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("无法判断平台", out)
        self.assertIn("--platform", out)
        other.conn.close()

    def test_bare_number_with_platform_jd(self):
        other = Store(Path(self.tmp.name) / "jd.db")
        variants = [_variant("黑色", "L", 899, True, 3)]
        buf = io.StringIO()
        with mock.patch("main.get_monitor", return_value=FakeMonitor(variants)), \
                redirect_stdout(buf):
            code = main.cmd_plan(_args(key="478897", platform="jd"), other, {})
        out = buf.getvalue()
        self.assertEqual(code, 0)
        got = other.get_product("jd:478897")
        self.assertIsNotNone(got)
        self.assertTrue(got.url.startswith("https://item.jd.com/"))
        self.assertIn("无会员：", out)
        other.conn.close()


class TestSizing(unittest.TestCase):
    def test_top_bmi_upsize(self):
        m = sizing.recommend_by_body("top", "men", height=178, weight=95)
        self.assertEqual(m.row["alpha"], "XL")

    def test_top_height_only(self):
        m = sizing.recommend_by_body("top", "men", height=178)
        self.assertEqual(m.row["alpha"], "L")

    def test_pants_waist(self):
        m = sizing.recommend_by_body("pants", "men", waist=82)
        self.assertEqual(m.row["alpha"], "L")

    def test_infer_kind(self):
        self.assertEqual(sizing.infer_kind("加绒休闲裤", ""), "pants")
        self.assertEqual(sizing.infer_kind("跑步鞋", ""), "shoes")
        self.assertEqual(sizing.infer_kind("羽绒服", ""), "top")


if __name__ == "__main__":
    unittest.main()
