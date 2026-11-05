"""advise 子命令与「可购价」口径测试（全程离线，网络层用 FakeMonitor 注入）。"""
import io
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import main
from core.models import PricePoint, Product, VariantSnapshot
from core.storage import Store

PID = "u0000000066172"


def _variant(color: str, size: str, price: float, in_stock: bool, qty: int | None,
             original: float | None = 799) -> VariantSnapshot:
    return VariantSnapshot(
        ts=time.time(), variant=f"{color} / {size}", in_stock=in_stock,
        state="有货" if in_stock else "无货", quantity=qty,
        color=color, size=size, price=price, original_price=original,
    )


class FakeMonitor:
    def __init__(self, variants):
        self._variants = variants

    def variants(self, pid):
        return list(self._variants)


class TestAdvise(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.p = Product("uniqlo", PID, main.UNIQLO_PRODUCT_PAGE.format(pid=PID),
                         name="摇粒绒高领上衣", category="大衣",
                         target_price=599, watched_variant="乳白色 M")
        self.store.add_product(self.p)
        self.did = self.p.display_id()

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def _run(self, variants, coupons=None):
        buf = io.StringIO()
        with mock.patch("main.get_monitor", return_value=FakeMonitor(variants)), \
                redirect_stdout(buf):
            code = main.cmd_advise(
                SimpleNamespace(key=PID, coupons=coupons), self.store, {})
        return code, buf.getvalue()

    def test_partial_soldout_uses_in_stock_price(self):
        # 799 的乳白/黑色有货，699 的深橄榄色售罄：结论价必须是 799，不是 699
        variants = [
            _variant("深橄榄色", "M", 699, False, 0),
            _variant("乳白色", "M", 799, True, 9),
            _variant("黑色", "M", 799, True, 3),
        ]
        self.store.add_price(self.did, PricePoint(time.time() - 3600, 699.0))
        code, out = self._run(variants)
        self.assertEqual(code, 0)
        self.assertIn("可购最低价：¥799（乳白色 / M）", out)
        self.assertIn("你关注的「乳白色 M」：¥799 · 有货，剩 9 件", out)
        self.assertIn("目标价 ¥599：还差 ¥200（需再降 25%）", out)
        self.assertIn("叠券后估算：¥749", out)
        self.assertIn("未计入你的个人券", out)
        self.assertNotIn("可购最低价：¥699", out)
        self.assertIn("价差说明", out)

    def test_all_soldout_never_quotes_price(self):
        variants = [
            _variant("乳白色", "M", 799, False, 0),
            _variant("黑色", "M", 799, False, 0),
        ]
        code, out = self._run(variants)
        self.assertEqual(code, 0)
        self.assertIn("【结论】关注", out)
        self.assertIn("全部尺码售罄", out)
        self.assertIn("可购最低价：无货", out)
        self.assertIn("你关注的「乳白色 M」：无货", out)
        self.assertNotIn("¥", out)

    def test_no_history_prints_no_probability(self):
        variants = [_variant("乳白色", "M", 799, True, 9)]
        code, out = self._run(variants)
        self.assertEqual(code, 0)
        self.assertIn("【结论】等", out)
        self.assertIn("历史样本不足", out)
        self.assertNotIn("再降概率", out)

    def test_watched_soldout_offers_other_colors(self):
        variants = [
            _variant("乳白色", "M", 799, False, 0),
            _variant("黑色", "M", 799, True, 5),
        ]
        code, out = self._run(variants)
        self.assertEqual(code, 0)
        self.assertIn("【结论】关注", out)
        self.assertIn("关注尺码无货，可换色或等补货", out)
        self.assertIn("可购最低价：¥799（黑色 / M）", out)
        self.assertIn("你关注的「乳白色 M」：¥799 · 无货", out)

    def test_bare_code_auto_add(self):
        other = Store(Path(self.tmp.name) / "bare.db")
        variants = [_variant("乳白色", "M", 799, True, 9)]
        buf = io.StringIO()
        with mock.patch("main.get_monitor", return_value=FakeMonitor(variants)), \
                redirect_stdout(buf):
            code = main.cmd_advise(
                SimpleNamespace(key=PID, coupons=None), other, {})
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("已自动加入跟踪", out)
        got = other.get_product(f"uniqlo:{PID}")
        self.assertIsNotNone(got)
        self.assertTrue(got.url.startswith("https://www.uniqlo.cn/"))
        other.conn.close()

    def test_coupons_file_accepts_object_form(self):
        coupon_file = Path(self.tmp.name) / "coupons.json"
        coupon_file.write_text(
            '{"coupons": [{"rid": "s1", "name": "店铺券 满500减80", '
            '"kind": "full_off", "value": 80, "threshold": 500}]}',
            encoding="utf-8")
        variants = [_variant("乳白色", "M", 799, True, 9)]
        code, out = self._run(variants, coupons=str(coupon_file))
        self.assertEqual(code, 0)
        self.assertIn("叠券后估算：¥719", out)
        self.assertNotIn("未计入你的个人券", out)


class TestListPrice(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.p = Product("uniqlo", PID, main.UNIQLO_PRODUCT_PAGE.format(pid=PID),
                         name="摇粒绒高领上衣", target_price=599, watched_variant="乳白色 M")
        self.store.add_product(self.p)
        self.did = self.p.display_id()

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def test_list_shows_in_stock_price(self):
        now = time.time()
        self.store.add_price(self.did, PricePoint(now, 699.0))
        self.store.add_variants(self.did, [
            _variant("深橄榄色", "M", 699, False, 0),
            _variant("乳白色", "M", 799, True, 9),
        ])
        buf = io.StringIO()
        with redirect_stdout(buf):
            main.cmd_list(SimpleNamespace(), self.store, {})
        out = buf.getvalue()
        self.assertIn("¥799", out)
        self.assertIn("摇粒绒高领上衣", out)


if __name__ == "__main__":
    unittest.main()
