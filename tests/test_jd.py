"""京东移动端兜底解析测试（离线，不触网）。"""
import unittest
from pathlib import Path
from unittest import mock

import requests

from monitors.jd import JdMonitor, _parse_mobile_price, _parse_mobile_stock, _parse_title

FIXTURE = Path(__file__).parent / "fixtures" / "jd" / "mobile.html"


class _Resp:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def json(self):
        return []


class TestJdMobileParse(unittest.TestCase):
    def setUp(self):
        self.html = FIXTURE.read_text(encoding="utf-8")

    def test_price_from_real_amount(self):
        self.assertEqual(_parse_mobile_price(self.html), 1101.0)

    def test_masked_price_is_ignored(self):
        self.assertIsNone(_parse_mobile_price('{"jdPrice":"1??9","price":"1??9"}'))

    def test_stock_state(self):
        stock = _parse_mobile_stock(self.html)
        self.assertIsNotNone(stock)
        self.assertTrue(stock.in_stock)
        self.assertEqual(stock.state, "现货")
        soldout = _parse_mobile_stock('{"stock":{"StockState":40}}')
        self.assertFalse(soldout.in_stock)

    def test_title_cleanup(self):
        self.assertEqual(_parse_title(self.html), "测试羽绒服")

    def test_fetch_falls_back_to_mobile_page(self):
        def fake_get(url, **kw):
            if "p.3.cn" in url or "c0.3.cn" in url:
                raise requests.exceptions.SSLError("blocked")
            return _Resp(self.html)

        with mock.patch.object(JdMonitor, "http_get", side_effect=fake_get):
            mon = JdMonitor({})
            price, stock = mon.fetch("100012043978")
        self.assertEqual(price.price, 1101.0)
        self.assertTrue(stock.in_stock)
        self.assertEqual(mon.last_name, "测试羽绒服")


if __name__ == "__main__":
    unittest.main()
