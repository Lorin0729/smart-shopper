"""优惠叠加计算器单元测试。"""
import unittest

from optimizer.deals import CartItem, DiscountRule, best_deal, best_price, demo_rules, optimize_cart


def rule(rid, kind, value, **kw):
    return DiscountRule(rid=rid, name=rid, kind=kind, value=value, **kw)


class TestBestDeal(unittest.TestCase):
    def test_full_off_threshold_not_met(self):
        rules = [rule("c1", "full_off", 50, threshold=300)]
        res = best_price(200, rules)
        self.assertEqual(res.final, 200)

    def test_full_off_met(self):
        rules = [rule("c1", "full_off", 50, threshold=300)]
        res = best_price(350, rules)
        self.assertEqual(res.final, 300)

    def test_exclusive_group_picks_best(self):
        rules = [
            rule("big", "full_off", 60, threshold=399, stack_group="store"),
            rule("small", "full_off", 40, threshold=299, stack_group="store"),
        ]
        # 350：小券可用，大券不可用
        self.assertEqual(best_price(350, rules).final, 310)
        # 400：大券更优
        self.assertEqual(best_price(400, rules).final, 340)

    def test_every_full_off_multi_tier(self):
        rules = [rule("cross", "every_full_off", 50, threshold=300, step=300)]
        # 650 触发两档
        self.assertEqual(best_price(650, rules).final, 550)

    def test_percent_and_fixed_stack(self):
        rules = [
            rule("vip", "percent", 0.9, priority=10),
            rule("red", "fixed", 20, priority=20),
        ]
        res = best_price(100, rules)
        self.assertEqual(res.final, 70)  # 100*0.9-20

    def test_points_capped(self):
        rules = [rule("bean", "points", 100, max_off=0.02)]
        res = best_price(100, rules)
        self.assertEqual(res.final, 98)  # 最多抵 2%

    def test_ranked_results_sorted(self):
        results = best_deal(500, demo_rules("jd"), top_n=3)
        self.assertTrue(len(results) >= 1)
        finals = [r.final for r in results]
        self.assertEqual(finals, sorted(finals))
        self.assertTrue(all(r.final <= 500 for r in results))

    def test_demo_rules_beat_plain_price(self):
        res = best_price(899, demo_rules("taobao"))
        self.assertLess(res.final, 899)


class TestCart(unittest.TestCase):
    def test_cart_cross_store_and_tips(self):
        items = [
            CartItem("羽绒服", 800, "店A"),
            CartItem("围巾", 100, "店B"),
        ]
        rules = [
            DiscountRule("a1", "店A券 满600减80", "full_off", 80, 600, store="店A"),
            DiscountRule("cross", "跨店每满300减50", "every_full_off", 50, 300, step=300),
        ]
        out = optimize_cart(items, rules)
        self.assertAlmostEqual(out["stores"]["店A"].final, 720)
        # 店A 720 + 店B 100 = 820，触发 2 档跨店满减
        self.assertAlmostEqual(out["grand"].final, 720)
        self.assertTrue(any("凑" in t for t in out["tips"]))


if __name__ == "__main__":
    unittest.main()
