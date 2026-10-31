"""sparkline 与配置加载单元测试。"""
import os
import unittest

from core.sparkline import sparkline, trend_line


class TestSparkline(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(sparkline([]), "")

    def test_flat_series_uses_lowest_bar(self):
        out = sparkline([100.0] * 5)
        self.assertEqual(out, "▁▁▁▁▁")

    def test_output_length_capped_by_width(self):
        values = list(range(200))
        self.assertEqual(len(sparkline(values, width=40)), 40)

    def test_rising_series_ends_with_tallest_bar(self):
        out = sparkline([1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(out[-1], "█")

    def test_trend_line_contains_endpoints(self):
        out = trend_line([899, 850, 819])
        self.assertIn("899", out)
        self.assertIn("819", out)


class TestConfigEnv(unittest.TestCase):
    def test_env_overrides_file(self):
        from main import load_config
        os.environ["SMART_SHOPPER_CONFIG"] = '{"check_interval_seconds": 60}'
        try:
            cfg = load_config(None)
            self.assertEqual(cfg["check_interval_seconds"], 60)
        finally:
            del os.environ["SMART_SHOPPER_CONFIG"]


if __name__ == "__main__":
    unittest.main()
