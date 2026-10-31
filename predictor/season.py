"""季节性类目模型：预测"发售多久后买最划算"。

每个类目档案描述：
- 上架月 / 需求旺季（价格最硬）/ 季末清仓月（价格最低）
- 大促月（普遍性折扣）
- 发售后多少天进入合理折扣窗口
- 预期清仓折扣深度
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class CategoryProfile:
    key: str
    name: str
    launch_months: tuple[int, ...]     # 通常上新月
    peak_months: tuple[int, ...]       # 旺季（最不建议原价买，但价格最高）
    clearance_months: tuple[int, ...]  # 季末清仓（全年最低价窗口）
    expected_clearance_discount: float  # 相对发售价的清仓折扣（0.35 = 65折水平）
    days_after_launch: tuple[int, int]  # 发售后折扣深水期（天）
    tip: str


PROFILES: dict[str, CategoryProfile] = {
    "down_jacket": CategoryProfile(
        "down_jacket", "羽绒服",
        launch_months=(8, 9), peak_months=(11, 12, 1),
        clearance_months=(2, 3),
        expected_clearance_discount=0.35,  # 季末常见 5~6.5 折
        days_after_launch=(90, 150),
        tip="羽绒服 9 月上新定价最硬；双11 出现全年首个深折扣；"
            "发售约 3~5 个月后（次年 2 月中~3 月底）进入季末清仓，"
            "常见 5~6.5 折，是全年最低价窗口。反季囤（6~8 月）也常有超低价。",
    ),
    "winter_coat": CategoryProfile(
        "winter_coat", "大衣/棉服",
        launch_months=(8, 9), peak_months=(11, 12, 1),
        clearance_months=(2, 3), expected_clearance_discount=0.4,
        days_after_launch=(80, 150),
        tip="与羽绒服节奏接近，季末清仓（2~3 月）折扣最深。",
    ),
    "summer_wear": CategoryProfile(
        "summer_wear", "夏装/T恤",
        launch_months=(3, 4), peak_months=(5, 6, 7),
        clearance_months=(7, 8), expected_clearance_discount=0.4,
        days_after_launch=(90, 140),
        tip="夏装 7~8 月进入清仓，基础款可等；爆款尺码易断。",
    ),
    "sneaker": CategoryProfile(
        "sneaker", "运动鞋",
        launch_months=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12),
        peak_months=(), clearance_months=(), expected_clearance_discount=0.25,
        days_after_launch=(60, 120),
        tip="球鞋上市 2~4 个月后普遍进入折扣期，大促节点（618/双11）叠加平台券更划算。",
    ),
    "general": CategoryProfile(
        "general", "通用商品",
        launch_months=(), peak_months=(), clearance_months=(),
        expected_clearance_discount=0.2, days_after_launch=(45, 90),
        tip="通用策略：上架 1.5~3 个月进入常规折扣期，618 / 双11 / 双12 前后是集中降价窗口。",
    ),
}

_ALIASES = {
    "羽绒": "down_jacket", "羽绒服": "down_jacket", "down": "down_jacket",
    "jacket": "winter_coat", "大衣": "winter_coat", "棉服": "winter_coat",
    "夏装": "summer_wear", "t": "summer_wear", "短袖": "summer_wear",
    "鞋": "sneaker", "sneakers": "sneaker",
}


def normalize_category(category: str) -> str:
    c = (category or "general").strip().lower()
    if c in PROFILES:
        return c
    for k, v in _ALIASES.items():
        if k in c or c in k:
            return v
    return "general"


def profile_of(category: str) -> CategoryProfile:
    return PROFILES[normalize_category(category)]


@dataclass
class BuyWindow:
    start: date
    end: date
    tag: str
    expected_discount: float  # 相对原价预估折扣（0.6 = 6折）
    reason: str

    def overlaps_today(self, today: date) -> bool:
        return self.start <= today <= self.end


def best_buy_windows(category: str, launch_date: date | None = None,
                     today: date | None = None, horizon_days: int = 400) -> list[BuyWindow]:
    """给出未来 horizon_days 内按预期折扣深度排序的购买窗口。"""
    today = today or date.today()
    prof = profile_of(category)
    windows: list[BuyWindow] = []

    # 1) 季末清仓窗口（未来 13 个月内出现的月份区间）
    if prof.clearance_months:
        for year in (today.year, today.year + 1):
            lo_m, hi_m = min(prof.clearance_months), max(prof.clearance_months)
            start = date(year, lo_m, 10)
            end = date(year, hi_m, 28) if hi_m != 12 else date(year, 12, 28)
            if end >= today and (start - today).days <= horizon_days:
                disc = 1 - prof.expected_clearance_discount
                windows.append(BuyWindow(
                    start, end, f"{prof.name}季末清仓", disc,
                    f"季节需求退坡，商家清库存，预期约 {disc * 10:g} 折，为全年最低价窗口",
                ))

    # 2) 发售后深水期（若有发售日）
    if launch_date:
        lo, hi = prof.days_after_launch
        start, end = launch_date + timedelta(days=lo), launch_date + timedelta(days=hi)
        if end >= today and (start - today).days <= horizon_days:
            disc = 1 - prof.expected_clearance_discount * 0.8
            windows.append(BuyWindow(
                start, end, f"发售 {lo}~{hi} 天折扣深水期", disc,
                f"上市初期定价最硬，{lo} 天后进入常规降价通道，{hi} 天内大概率出现阶段低价（约 {disc * 10:g} 折）",
            ))

    # 3) 平台大促窗口（forecast.upcoming_promos，避免循环导入在函数内引入）
    from .forecast import PROMOS, _mk_date
    for name, (sm, sd), (pm, pd), (em, ed) in PROMOS:
        for year in (today.year, today.year + 1):
            start, end = _mk_date(year, sm, sd), _mk_date(year, em, ed)
            if end >= today and (start - today).days <= horizon_days:
                windows.append(BuyWindow(
                    start, end, name, 0.75,
                    "平台大促：跨店满减 + 平台券集中释放，多数商品出现阶段低价",
                ))

    # 去重 + 排序：预期折扣低者优先
    uniq: dict[tuple, BuyWindow] = {}
    for w in windows:
        uniq[(w.tag, w.start)] = w
    return sorted(uniq.values(), key=lambda w: (w.expected_discount, w.start))
