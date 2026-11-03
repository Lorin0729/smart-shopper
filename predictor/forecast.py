"""价格历史分析、降价预测与购买建议。"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from core.models import InventorySnapshot, PricePoint

# ------------------------------------------------------------ 大促日历
# (名称, 开始(月,日), 峰值(月,日), 结束(月,日))
PROMOS = [
    ("年货节", (1, 5), (1, 10), (1, 20)),
    ("38节", (3, 1), (3, 4), (3, 8)),
    ("618", (5, 31), (6, 18), (6, 20)),
    ("88会员节", (8, 1), (8, 8), (8, 10)),
    ("99划算节", (9, 1), (9, 9), (9, 10)),
    ("双11", (10, 20), (11, 11), (11, 13)),
    ("双12", (12, 1), (12, 12), (12, 14)),
]


def _mk_date(year: int, month: int, day: int) -> date:
    import calendar
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


@dataclass
class PromoEvent:
    name: str
    start: date
    peak: date
    end: date


def upcoming_promos(today: date | None = None, ahead_days: int = 90) -> list[PromoEvent]:
    """未来 ahead_days 内的大促（含进行中），按峰值日排序。"""
    today = today or date.today()
    out = []
    for year in (today.year, today.year + 1):
        for name, s, p, e in PROMOS:
            ev = PromoEvent(name, _mk_date(year, *s), _mk_date(year, *p), _mk_date(year, *e))
            if ev.end >= today and (ev.start - today).days <= ahead_days:
                out.append(ev)
    return sorted(out, key=lambda x: x.peak)


# ------------------------------------------------------------ 历史分析
@dataclass
class HistoryStats:
    count: int
    low: float
    high: float
    avg: float
    median: float
    current: float
    percentile: float          # 当前价在历史中的分位（0=最低）
    days_since_low: int
    drop_count: int            # 有效降价次数
    avg_drop_interval_days: float | None
    avg_drop_depth: float | None  # 平均降幅比例


@dataclass
class DropPrediction:
    prob_30d: float            # 未来 30 天内出现阶段降价的概率
    likely_start: date | None  # 最可能的降价窗口起点
    likely_end: date | None
    reason: str = ""


@dataclass
class TrendForecast:
    """基于近期价格的线性回归外推（含置信区间）。"""

    days: int
    slope_per_day: float      # 每天价格变化（元）
    projected: float          # N 天后的中心预测价
    low: float                # 区间下界（约 80% 置信）
    high: float               # 区间上界
    residual_std: float
    samples: int
    direction: str            # down / up / flat
    current: float = field(default=0.0, repr=False)

    def describe(self, money=lambda v: f"¥{v:g}") -> str:
        pct = (self.projected - self.current) / self.current * 100 if self.current else 0
        arrow = {"down": "↘", "up": "↗", "flat": "→"}.get(self.direction, "→")
        return (f"{self.days} 天后约 {money(self.projected)}"
                f"（区间 {money(self.low)} ~ {money(self.high)}），"
                f"较当前 {pct:+.1f}% {arrow}")


def trend_forecast(history: PriceHistory, days: int = 30,
                   window_days: float = 90, min_points: int = 4) -> TrendForecast | None:
    """对最近 window_days 的价格做最小二乘拟合，外推 days 天后的价格。

    数据点太少或时间跨度过短时返回 None（避免给出假精度）。
    """
    pts = history.points
    if len(pts) < min_points:
        return None
    t_end = pts[-1].ts
    recent = [p for p in pts if p.ts >= t_end - window_days * 86400]
    if len(recent) < min_points:
        recent = pts[-min_points:]
    span_days = (recent[-1].ts - recent[0].ts) / 86400
    if span_days < 5:
        return None

    xs = [(p.ts - recent[0].ts) / 86400 for p in recent]
    ys = [p.price for p in recent]
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    std = (sum(r * r for r in resid) / max(1, n - 2)) ** 0.5

    future_x = xs[-1] + days
    projected = max(0.0, intercept + slope * future_x)
    # 区间至少 ±1%，避免完美线性数据给出"零误差"的假精度
    half = max(1.28 * std, projected * 0.01)
    low = max(0.0, projected - half)
    high = projected + half
    rel = slope * days / mean_y if mean_y else 0
    direction = "down" if rel <= -0.02 else ("up" if rel >= 0.02 else "flat")

    return TrendForecast(
        days=days, slope_per_day=round(slope, 3), projected=round(projected, 2),
        low=round(low, 2), high=round(high, 2), residual_std=round(std, 2),
        samples=n, direction=direction, current=ys[-1],
    )


class PriceHistory:
    def __init__(self, points: list[PricePoint]):
        self.points = sorted(points, key=lambda p: p.ts)

    # ---- 统计 ----
    def stats(self) -> HistoryStats | None:
        if len(self.points) < 2:
            return None
        prices = [p.price for p in self.points]
        low, high = min(prices), max(prices)
        current = prices[-1]
        below = sum(1 for p in prices if p < current)
        percentile = below / len(prices)
        low_ts = min(self.points, key=lambda p: p.price).ts
        days_since_low = int((self.points[-1].ts - low_ts) / 86400)

        drops = self.drops()
        # 按价格序列直接计算降价间隔与深度
        intervals, depths = [], []
        drop_ts: list[float] = []
        prev = self.points[0]
        for pt in self.points[1:]:
            if pt.price < prev.price * 0.995:  # 视为一次有效降价（>0.5%）
                if drop_ts:
                    intervals.append((pt.ts - drop_ts[-1]) / 86400)
                depths.append((prev.price - pt.price) / prev.price)
                drop_ts.append(pt.ts)
            prev = pt
        return HistoryStats(
            count=len(prices), low=low, high=high,
            avg=round(statistics.mean(prices), 2),
            median=round(statistics.median(prices), 2),
            current=current, percentile=round(percentile, 3),
            days_since_low=days_since_low,
            drop_count=len(depths),
            avg_drop_interval_days=round(statistics.mean(intervals), 1) if intervals else None,
            avg_drop_depth=round(statistics.mean(depths), 3) if depths else None,
        )

    def drops(self):
        out = []
        prev = None
        for pt in self.points:
            if prev and pt.price < prev.price * 0.995:
                out.append((prev.ts, pt.ts, prev.price, pt.price))
            prev = pt
        return out

    def trend(self, days: int = 14) -> str:
        """近 N 天趋势：down / up / flat / unknown。"""
        if len(self.points) < 2:
            return "unknown"
        cutoff = self.points[-1].ts - days * 86400
        recent = [p.price for p in self.points if p.ts >= cutoff]
        if len(recent) < 2:
            return "unknown"
        if recent[-1] < recent[0] * 0.99:
            return "down"
        if recent[-1] > recent[0] * 1.01:
            return "up"
        return "flat"


# ------------------------------------------------------------ 降价预测
def predict_drop(history: PriceHistory, today: date | None = None,
                 ahead_days: int = 30) -> DropPrediction:
    """综合三个信号估计未来 ahead_days 内降价概率：
    1. 历史降价节奏（平均间隔是否临近）
    2. 大促日历（窗口内是否有大促，尤其峰值前 10 天）
    3. 当前价格分位（分位越低，短期再降概率越小）
    """
    today = today or date.today()
    stats = history.stats()
    prob = 0.15
    reasons = []

    if stats and stats.avg_drop_interval_days:
        last_drop_ts = max((t1 for (_t0, t1, _a, _b) in history.drops()), default=None)
        if last_drop_ts:
            since = (datetime.combine(today, datetime.min.time()).timestamp() - last_drop_ts) / 86400
            ratio = since / stats.avg_drop_interval_days
            if ratio >= 0.8:
                prob += 0.30
                reasons.append(f"距上次降价已 {since:.0f} 天，接近历史平均间隔 {stats.avg_drop_interval_days:.0f} 天")
            elif ratio <= 0.25:
                prob -= 0.10
                reasons.append("刚降过价不久，短期再降概率较低")
            else:
                reasons.append(f"历史平均每 {stats.avg_drop_interval_days:.0f} 天出现一次降价")

    promos = upcoming_promos(today, ahead_days + 15)
    near = None
    for ev in promos:
        days_to_peak = (ev.peak - today).days
        if -3 <= days_to_peak <= ahead_days:
            near = ev
            break
    if near:
        prob += 0.35
        reasons.append(f"{ahead_days} 天内有大促「{near.name}」（峰值 {near.peak.strftime('%m-%d')}），商家普遍在峰值前 3~7 天调价")

    if stats:
        if stats.percentile >= 0.6:
            prob += 0.10
            reasons.append(f"当前价处于历史 {stats.percentile:.0%} 分位（偏高），有回调空间")
        elif stats.percentile <= 0.2:
            prob -= 0.15
            reasons.append(f"当前价已处历史 {stats.percentile:.0%} 分位（低价区），再降空间有限")

    prob = max(0.05, min(0.95, prob))
    window_start = near.start if near else (today + timedelta(days=round(ahead_days * (1 - prob))))
    window_start = max(window_start, today)  # 窗口不早于今天
    window_end = min(near.peak, today + timedelta(days=ahead_days)) if near else today + timedelta(days=ahead_days)
    if not reasons:
        reasons.append("历史数据不足，按平台促销节奏保守估计")
    return DropPrediction(
        prob_30d=round(prob, 2),
        likely_start=window_start,
        likely_end=max(window_end, window_start),
        reason="；".join(reasons),
    )


# ------------------------------------------------------------ 购买建议
BUY_NOW, ACT_FAST, WAIT, RESTOCK_WATCH = "立即购买", "尽快入手", "建议等待", "关注补货"


@dataclass
class Recommendation:
    action: str
    score: int                      # 0-100，越高越该买
    reasons: list[str] = field(default_factory=list)
    expected_low: str = ""          # 预期低价参考


def recommend(history: PriceHistory, inventory: InventorySnapshot | None,
              prediction: DropPrediction, windows: list,
              today: date | None = None,
              trend: "TrendForecast | None" = None) -> Recommendation:
    """融合价格分位 + 降价预测 + 库存 + 季节窗口 + 趋势外推 → 购买决策。"""
    today = today or date.today()
    stats = history.stats()
    reasons: list[str] = []
    if not stats:
        return Recommendation(WAIT, 40, ["历史数据不足（少于 2 个采样点），先积累几天监控数据"])

    score = 50
    # 价格分位
    if stats.percentile <= 0.15 or stats.current <= stats.low * 1.03:
        score += 35
        reasons.append(f"当前价 ¥{stats.current:g} 已是历史最低附近（最低 ¥{stats.low:g}）")
    elif stats.percentile <= 0.35:
        score += 15
        reasons.append(f"当前价处于历史 {stats.percentile:.0%} 分位，偏低")
    elif stats.percentile >= 0.7:
        score -= 25
        reasons.append(f"当前价处于历史 {stats.percentile:.0%} 分位，明显偏高")

    # 降价预测
    if prediction.prob_30d >= 0.5 and stats.percentile > 0.2:
        score -= 20
        reasons.append(f"未来 30 天降价概率 {prediction.prob_30d:.0%}：{prediction.reason}")
    elif prediction.prob_30d <= 0.2:
        score += 10

    # 季节窗口
    for w in windows:
        if w.overlaps_today(today):
            score += 15
            reasons.append(f"正处于「{w.tag}」窗口（{w.start} ~ {w.end}）：{w.reason}")
        elif 0 <= (w.start - today).days <= 10 and stats.percentile > 0.5:
            score -= 10
            reasons.append(f"{(w.start - today).days} 天后进入「{w.tag}」窗口，可再等等")
            break

    # 库存
    if inventory:
        if not inventory.in_stock:
            return Recommendation(RESTOCK_WATCH, 0, ["当前无货，开启监控等补货通知"] + reasons)
        if inventory.quantity is not None and inventory.quantity <= 10:
            if stats.percentile <= 0.4:
                return Recommendation(ACT_FAST, 85, reasons + [f"库存仅剩 {inventory.quantity} 件且价格不错，随时售罄"])
        if inventory.state and ("紧张" in inventory.state or "配货" in inventory.state):
            score += 5

    # 趋势
    tr = history.trend()
    if tr == "down":
        reasons.append("近两周价格处于下降通道")
        if stats.percentile > 0.5:
            score -= 5

    # 线性回归外推：给「再等等」或「该出手」一个量化依据
    if trend and trend.current:
        delta = trend.current - trend.projected
        drop_pct = delta / trend.current
        if trend.direction == "down" and drop_pct >= 0.08 and stats.percentile > 0.25:
            score -= 12
            reasons.append(f"趋势外推 {trend.days} 天后约 ¥{trend.projected:g}"
                           f"（-{drop_pct:.0%}），可再等")
        elif trend.direction == "down" and drop_pct >= 0.03:
            reasons.append(f"趋势温和下行：{trend.days} 天后约 ¥{trend.projected:g}")
        elif trend.direction == "up" and stats.percentile <= 0.35:
            score += 8
            reasons.append(f"趋势上行（{trend.days} 天后约 ¥{trend.projected:g}），当前低价宜入手")

    score = max(0, min(100, score))
    if score >= 70:
        action = BUY_NOW
    elif score >= 55:
        action = ACT_FAST
    else:
        action = WAIT
    if prediction.likely_start and action == WAIT:
        rec_low = f"预计 {prediction.likely_start.strftime('%m-%d')}~{prediction.likely_end.strftime('%m-%d')} 降价概率 {prediction.prob_30d:.0%}"
        if stats.avg_drop_depth:
            rec_low += f"，历史平均降幅 {stats.avg_drop_depth:.0%}"
    else:
        rec_low = ""
    return Recommendation(action, score, reasons, rec_low)
