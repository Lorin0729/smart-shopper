"""优惠叠加最优解计算器。

支持券种（kind）：
- full_off        满 threshold 减 value（不叠加同组其他券）
- every_full_off  每(step跨店)满 threshold 减 value，可多档触发（跨店满减）
- percent         打 value 折（0.95 = 95折），可带 threshold
- fixed           立减 value（红包/购物金，无门槛）
- points          积分抵扣，value 为最多可抵金额（元），max_off 限制抵扣比例上限

叠加规则：
- 同一 stack_group 的券互斥（例如店铺券只能用一张），取组合中的最优；
- 其余券默认可叠加，按 priority 从低到高（商品级 -> 店铺级 -> 跨店 -> 平台）依次计算。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field


@dataclass
class DiscountRule:
    rid: str
    name: str
    kind: str  # full_off / every_full_off / percent / fixed / points
    value: float
    threshold: float = 0.0
    step: float = 0.0      # every_full_off 的"每满"步长
    stack_group: str | None = None  # 同组互斥
    priority: int = 100    # 越小越先计算
    max_off: float | None = None    # points 抵扣上限（元）
    store: str | None = None        # 限定店铺（购物车模式）
    enabled: bool = True
    note: str = ""

    @staticmethod
    def from_dict(d: dict) -> "DiscountRule":
        return DiscountRule(**{k: v for k, v in d.items()
                               if k in DiscountRule.__dataclass_fields__})


@dataclass
class DealResult:
    original: float
    final: float
    applied: list = field(default_factory=list)  # [(rule, before, after)]

    @property
    def saved(self) -> float:
        return round(self.original - self.final, 2)

    def breakdown(self) -> str:
        lines = [f"原价 ¥{self.original:.2f}"]
        for r, before, after in self.applied:
            off = before - after
            lines.append(f"  - {r.name}: -¥{off:.2f}  (¥{before:.2f} → ¥{after:.2f})")
        lines.append(f"到手 ¥{self.final:.2f}，共省 ¥{self.saved:.2f}"
                     f"（{(self.saved / self.original * 100) if self.original else 0:.1f}%）")
        return "\n".join(lines)


# ------------------------------------------------------------------ 单品
def _apply_rule(amount: float, r: DiscountRule) -> float | None:
    """返回应用后金额；不满足门槛返回 None（表示该券用不上）。"""
    eps = 1e-9
    if r.kind == "full_off":
        if amount >= r.threshold - eps:
            return max(0.0, round(amount - r.value, 2))
        return None
    if r.kind == "every_full_off":
        if r.step > 0 and amount >= r.threshold - eps:
            times = int(amount // r.step)
            return max(0.0, round(amount - times * r.value, 2))
        return None
    if r.kind == "percent":
        if amount >= r.threshold - eps:
            return round(amount * r.value, 2)
        return None
    if r.kind == "fixed":
        return max(0.0, round(amount - r.value, 2))
    if r.kind == "points":
        cap = r.max_off if r.max_off is not None else amount
        # max_off <= 1 视为比例上限（如 0.02 = 抵扣不超过当前金额的 2%），> 1 为绝对金额
        cap = amount * cap if cap <= 1 else min(cap, amount)
        off = min(amount, r.value, cap)
        return round(amount - off, 2)
    return None


def _apply_sequence(amount: float, rules: list[DiscountRule]) -> DealResult:
    applied = []
    for r in sorted(rules, key=lambda x: x.priority):
        after = _apply_rule(amount, r)
        if after is None or after >= amount:
            continue
        applied.append((r, amount, after))
        amount = after
    return DealResult(original=None, final=round(amount, 2), applied=applied)  # type: ignore


def _candidate_sets(rules: list[DiscountRule], cap: int = 2_000_000):
    """枚举合法组合：互斥组内至多一张，独立券自由选。"""
    exclusive: dict[str, list[DiscountRule]] = {}
    singles: list[DiscountRule] = []
    for r in rules:
        if not r.enabled:
            continue
        (exclusive.setdefault(r.stack_group, []).append(r)
         if r.stack_group else singles.append(r))

    group_options = []
    for members in exclusive.values():
        group_options.append([()] + [(m,) for m in members])
    n = len(singles)
    count = 0
    for combo in itertools.product(*group_options):
        base = [r for t in combo for r in t]
        for mask in range(1 << n):
            count += 1
            if count > cap:
                return
            yield base + [singles[i] for i in range(n) if mask >> i & 1]


def best_deal(price: float, rules: list[DiscountRule],
              top_n: int = 5) -> list[DealResult]:
    """返回最优的 top_n 个叠券方案（最终价升序）。"""
    results: list[DealResult] = []
    seen: set[tuple] = set()
    for sel in _candidate_sets(rules):
        res = _apply_sequence(price, sel)
        key = tuple(sorted(r.rid for r, _, _ in res.applied))
        if key in seen:
            continue
        seen.add(key)
        res.original = price
        results.append(res)
    results.sort(key=lambda x: (x.final, -len(x.applied)))
    return results[:top_n]


def best_price(price: float, rules: list[DiscountRule]) -> DealResult:
    return best_deal(price, rules, top_n=1)[0]


# ------------------------------------------------------------------ 购物车
@dataclass
class CartItem:
    name: str
    price: float
    store: str = "默认店铺"


def optimize_cart(items: list[CartItem], rules: list[DiscountRule]) -> dict:
    """跨店凑单优化：店铺级券按店取最优，跨店满减与平台券作用总价。

    返回 {"stores": {店: DealResult}, "grand": DealResult, "tips": [凑单建议]}
    """
    tips: list[str] = []
    store_rules: dict[str, list[DiscountRule]] = {}
    for r in rules:
        if r.store and r.enabled:
            store_rules.setdefault(r.store, []).append(r)

    store_results: dict[str, DealResult] = {}
    raw_by_store: dict[str, float] = {}
    for it in items:
        raw_by_store[it.store] = raw_by_store.get(it.store, 0.0) + it.price

    final_by_store: dict[str, float] = {}
    for store, subtotal in raw_by_store.items():
        srules = store_rules.get(store, [])
        if srules:
            res = best_price(subtotal, srules)
        else:
            res = DealResult(original=subtotal, final=subtotal, applied=[])
        store_results[store] = res
        final_by_store[store] = res.final
        # 凑单建议：未达门槛的满减券
        for r in srules:
            if r.kind == "full_off" and subtotal < r.threshold:
                gap = round(r.threshold - subtotal, 2)
                if gap <= r.threshold * 0.4:
                    tips.append(f"[{store}] 再凑 ¥{gap:.2f} 可用「{r.name}」减 ¥{r.value:.0f}")

    grand_base = sum(final_by_store.values())

    # 跨店满减 + 平台级券作用于总价：同样走组合枚举，保证互斥组只取最优一张
    global_rules = [r for r in rules if not r.store and r.enabled]
    if global_rules:
        grand = best_price(grand_base, global_rules)
        grand.original = round(grand_base, 2)
    else:
        grand = DealResult(original=round(grand_base, 2), final=round(grand_base, 2), applied=[])

    total_saved = round(
        sum(res.saved for res in store_results.values()) + grand.saved, 2
    )
    raw_total = round(sum(raw_by_store.values()), 2)
    tips_total = [
        f"合计原价 ¥{raw_total:.2f}，券后到手约 ¥{raw_total - total_saved:.2f}，"
        f"共省 ¥{total_saved:.2f}"
    ]
    # 跨店满减凑单建议
    for r in global_rules:
        if r.kind == "every_full_off" and r.step > 0 and grand_base >= r.threshold:
            rem = grand_base % r.step
            if rem > 0:
                tips.append(f"再凑 ¥{r.step - rem:.2f} 可多触发一档「{r.name}」(每满¥{r.step:.0f}减¥{r.value:.0f})")
    return {
        "stores": store_results,
        "grand": grand,
        "tips": tips_total + tips,
    }


# ------------------------------------------------------------------ 内置示例
def demo_rules(platform: str = "taobao") -> list[DiscountRule]:
    """一组典型大促券，用于 deal --demo 演示。"""
    if platform == "jd":
        return [
            DiscountRule("jd_plus", "PLUS会员95折", "percent", 0.95, priority=10),
            DiscountRule("jd_item", "单品直降", "fixed", 30, priority=20),
            DiscountRule("jd_store1", "店铺东券 满399减60", "full_off", 60, 399, stack_group="store", priority=30),
            DiscountRule("jd_store2", "店铺东券 满299减40", "full_off", 40, 299, stack_group="store", priority=31),
            DiscountRule("jd_cross", "跨店满减 每299减50", "every_full_off", 50, threshold=299, step=299, priority=40),
            DiscountRule("jd_red", "平台红包", "fixed", 20, priority=50),
            DiscountRule("jd_bean", "京豆抵扣(上限2%)", "points", 15, max_off=0.02, priority=60),
        ]
    if platform == "uniqlo":
        # 演示券：优衣库没有淘宝式跨店满减，这里只放一条平台满减占位。
        # 真实店铺券/会员券请用 `advise --coupons` 传入，避免把演示价当成交价。
        return [
            DiscountRule("uni_cross", "跨店满减 300-50", "full_off", 50, 300, priority=40),
        ]
    return [
        DiscountRule("tb_item", "单品优惠", "fixed", 25, priority=20),
        DiscountRule("tb_store1", "店铺券 满300减50", "full_off", 50, 300, stack_group="store", priority=30),
        DiscountRule("tb_store2", "店铺券 满200减20", "full_off", 20, 200, stack_group="store", priority=31),
        DiscountRule("tb_cross", "跨店满减 每300减50", "every_full_off", 50, threshold=300, step=300, priority=40),
        DiscountRule("tb_vip", "88VIP 95折", "percent", 0.95, priority=50),
        DiscountRule("tb_red", "大促红包", "fixed", 18, priority=55),
        DiscountRule("tb_coin", "淘金币抵扣(上限1%)", "points", 10, max_off=0.01, priority=60),
    ]
