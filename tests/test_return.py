"""凑单后退货净价测算测试（对应真实手算场景）。"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import main


class TestReturn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, items, coupon, paid, return_sku, rule=None, qty=1):
        order = {"items": items, "coupon": coupon, "paid": paid}
        path = self.dir / "order.json"
        path.write_text(json.dumps(order, ensure_ascii=False), encoding="utf-8")
        buf = io.StringIO()
        args = SimpleNamespace(order=str(path), return_sku=return_sku, qty=qty, rule=rule)
        with redirect_stdout(buf):
            code = main.cmd_return(args, None, {})
        return code, buf.getvalue()

    def _jackets(self):
        return [{"sku": "489482", "name": "茄克", "price": 399, "qty": 2}]

    def test_full_clawback_socks(self):
        items = self._jackets() + [{"sku": "486885", "name": "袜子", "price": 20, "qty": 1}]
        code, out = self._run(items, {"name": "满800减80", "amount": 80, "threshold": 800},
                              738, "486885", rule="full_clawback")
        self.assertEqual(code, 0)
        self.assertIn("预计退款：¥0.00", out)
        self.assertIn("券整张扣回", out)
        self.assertIn("保留商品实际成本：¥738.00", out)

    def test_proportional_socks(self):
        items = self._jackets() + [{"sku": "486885", "name": "袜子", "price": 20, "qty": 1}]
        code, out = self._run(items, {"amount": 80, "threshold": 800},
                              738, "486885", rule="proportional")
        self.assertEqual(code, 0)
        self.assertIn("预计退款：¥18.04", out)
        self.assertIn("保留商品实际成本：¥719.96", out)

    def test_full_clawback_tshirt(self):
        items = self._jackets() + [{"sku": "482300", "name": "T恤", "price": 39, "qty": 1}]
        code, out = self._run(items, {"amount": 80, "threshold": 800},
                              757, "482300", rule="uniqlo")
        self.assertEqual(code, 0)
        self.assertIn("预计退款：¥0.00", out)
        self.assertIn("保留商品实际成本：¥757.00", out)

    def test_proportional_tshirt(self):
        items = self._jackets() + [{"sku": "482300", "name": "T恤", "price": 39, "qty": 1}]
        code, out = self._run(items, {"amount": 80, "threshold": 800},
                              757, "482300", rule="proportional")
        self.assertEqual(code, 0)
        self.assertIn("预计退款：¥35.27", out)
        self.assertIn("保留商品实际成本：¥721.73", out)

    def test_still_meets_threshold(self):
        items = [{"sku": "489482", "name": "茄克", "price": 399, "qty": 4}]
        code, out = self._run(items, {"amount": 80, "threshold": 800},
                              1516, "489482", rule="full_clawback")
        self.assertEqual(code, 0)
        self.assertIn("退货后仍满足门槛", out)
        self.assertIn("预计退款：¥379.00", out)

    def test_unknown_shows_both_scenarios(self):
        items = self._jackets() + [{"sku": "486885", "name": "袜子", "price": 20, "qty": 1}]
        code, out = self._run(items, {"amount": 80, "threshold": 800}, 738, "486885")
        self.assertEqual(code, 0)
        self.assertIn("规则未知", out)
        self.assertIn("按比例退券", out)
        self.assertIn("整张券扣回", out)

    def test_missing_sku(self):
        items = self._jackets()
        code, out = self._run(items, {"amount": 0, "threshold": 0}, 798, "nope")
        self.assertEqual(code, 1)
        self.assertIn("找不到退货 SKU", out)


if __name__ == "__main__":
    unittest.main()
