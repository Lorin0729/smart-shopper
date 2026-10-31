"""多商品组合购买优化：一起买怎么凑最划算。

回答这类问题：
- 我这几件一起下单，能吃到几档跨店满减？
- 再凑多少钱能多触发一档，净赚多少？
- 一起买比分开买省多少？
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .deals import CartItem, DealResult, DiscountRule, best_price, optimize_cart


@dataclass
class FillerSuggestion:
    gap: float            # 还差多少钱
    gain: float           # 多拿到的优惠
    net: float            # 净收益（gain - gap）
    rule_name: str

    def describe(self) -> str:
        if self.net > 0:
            return (f"再凑 ¥{self.gap:.2f} 可多触发「{self.rule_name}」，"
                    f"多减 ¥{self.gain:.0f}，净赚 ¥{self.net:.2f}")
        return (f"再凑 ¥{self.gap:.2f} 可多触发「{self.rule_name}」，多减 ¥{self.gain:.0f}"
                f"（净亏 ¥{-self.net:.2f}，不建议硬凑）")


@dataclass
class BundlePlan:
    items_total: float                  # 商品原价合计
    final_total: float                  # 组合到手价（用全部可叠加券）
    separate_total: float               # 只用各店券（不含跨店/平台券）的合计
    store_results: dict = field(default_factory=dict)
    grand: DealResult | None = None
    achieved_tiers: list[str] = field(default_factory=list)
    fillers: list[FillerSuggestion] = field(default_factory=list)

    @property
    def saved(self) -> float:
        return round(self.items_total - self.final_total, 2)

    @property
    def bundle_bonus(self) -> float:
        """跨店满减 + 平台券带来的额外优惠（相对只用店铺券）。"""
        return round(self.separate_total - self.final_total, 2)

    def describe(self) -> str:
        lines = [
            f"商品原价合计 ¥{self.items_total:.2f}",
            f"只用各店券：¥{self.separate_total:.2f}",
            f"组合下单并用全部可叠加券：¥{self.final_total:.2f}（共省 ¥{self.saved:.2f}）",
        ]
        if self.bundle_bonus > 0:
            lines.append(f"其中跨店满减/平台券额外贡献 ¥{self.bundle_bonus:.2f}")
        else:
            lines.append("当前跨店满减/平台券未带来额外优惠，按店铺券下单即可")
        if self.achieved_tiers:
            lines.append("已触发：" + "；".join(self.achieved_tiers))
        for f in self.fillers:
            lines.append("· " + f.describe())
        return "\n".join(lines)


def plan_bundle(items: list[CartItem], rules: list[DiscountRule],
                filler_max: float = 150.0) -> BundlePlan:
    """items 为打算一起买的商品；filler_max 为可接受的凑单上限。"""
    if not items:
        raise ValueError("购物车为空")

    cart = optimize_cart(items, rules)
    grand: DealResult = cart["grand"]

    # 只用各店券的基准（不含跨店/平台券），用于衡量组合下单的额外收益
    separate = 0.0
    for it in items:
        local = [r for r in rules if r.store and r.store == it.store]
        sep = best_price(it.price, local) if local else None
        separate += sep.final if sep else it.price

    # 已触发的跨店档位 + 下一档凑单建议（按券生效当时的金额计算）
    achieved: list[str] = []
    fillers: list[FillerSuggestion] = []
    last_cross_before: float | None = None
    for rule, before, _after in grand.applied:
        if rule.kind == "every_full_off" and rule.step > 0:
            last_cross_before = before
            times = int(before // rule.step)
            if times > 0:
                achieved.append(f"「{rule.name}」×{times} 档（共减 ¥{times * rule.value:.0f}）")
                gap = round(rule.step - (before % rule.step), 2)
                if 0 < gap <= filler_max:
                    fillers.append(FillerSuggestion(
                        gap, rule.value, round(rule.value - gap, 2), rule.name))

    # 未使用的满减券：看还差多少能启用
    used_ids = {rule.rid for rule, _b, _a in grand.applied}
    base = last_cross_before if last_cross_before is not None else grand.original
    for r in rules:
        if not r.enabled or r.store or r.kind != "full_off" or r.rid in used_ids:
            continue
        gap = round(r.threshold - base, 2)
        if 0 < gap <= filler_max:
            fillers.append(FillerSuggestion(gap, r.value, round(r.value - gap, 2), r.name))

    fillers.sort(key=lambda f: -f.net)
    return BundlePlan(
        items_total=round(sum(i.price for i in items), 2),
        final_total=grand.final,
        separate_total=round(separate, 2),
        store_results=cart["stores"],
        grand=grand,
        achieved_tiers=achieved,
        fillers=fillers,
    )
