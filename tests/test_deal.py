"""deal 在无真券时必须 fail closed，不得编造优惠。"""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import main


class TestDealFailClosed(unittest.TestCase):
    def _run(self, coupons=None, demo=False, price=899):
        args = SimpleNamespace(price=price, platform="taobao", coupons=coupons, demo=demo)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main.cmd_deal(args, None, {})
        return code, buf.getvalue()

    def test_no_coupons_quotes_full_price(self):
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("按 0 券计算", out)
        self.assertIn("到手 ¥899.00", out)
        self.assertNotIn("省 ¥", out)

    def test_missing_file_fails(self):
        code, out = self._run(coupons="no-such-file.json")
        self.assertEqual(code, 1)
        self.assertIn("券配置读取失败", out)
        self.assertNotIn("到手 ¥", out)

    def test_demo_flag_is_labeled(self):
        code, out = self._run(demo=True)
        self.assertEqual(code, 0)
        self.assertIn("演示/估算券", out)
        self.assertIn("到手 ¥", out)

    def test_user_coupon_is_calculated(self):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "coupons.json"
        path.write_text(
            '[{"rid":"s1","name":"店铺券 满500减80","kind":"full_off",'
            '"value":80,"threshold":500,"source":"checkout_verified"}]',
            encoding="utf-8")
        try:
            code, out = self._run(coupons=str(path))
        finally:
            tmp.cleanup()
        self.assertEqual(code, 0)
        self.assertIn("到手 ¥819.00", out)
        self.assertIn("结算页实测", out)


if __name__ == "__main__":
    unittest.main()
