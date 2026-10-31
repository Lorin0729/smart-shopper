"""自包含 HTML 报告与 SVG 走势图生成（零第三方依赖）。

`python main.py report` 会输出一个可直接双击打开、发给朋友的 report.html，
每个商品包含价格曲线、统计、预测、购买建议与季节窗口。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path

from core.models import InventorySnapshot, PricePoint, VariantSnapshot

ACCENT = "#2f6df6"
DANGER = "#e5484d"
GOOD = "#12a150"
WARN = "#f5a524"


@dataclass
class ProductReport:
    title: str
    subtitle: str = ""
    url: str = ""
    points: list[PricePoint] = field(default_factory=list)
    stats: object | None = None          # predictor.forecast.HistoryStats
    prediction: object | None = None     # DropPrediction
    recommendation: object | None = None # Recommendation
    inventory: InventorySnapshot | None = None
    variants: list[VariantSnapshot] = field(default_factory=list)
    windows: list = field(default_factory=list)
    target_price: float | None = None
    category_name: str = ""


# ------------------------------------------------------------------ SVG
def price_svg(points: list[PricePoint], width: int = 760, height: int = 230,
              target: float | None = None) -> str:
    """把价格序列画成 SVG 折线图（含最低点标注、目标价虚线）。"""
    if not points:
        return _empty_svg(width, height, "暂无价格数据")
    prices = [p.price for p in points]
    t0, t1 = points[0].ts, points[-1].ts
    if t1 - t0 < 1:  # 只有一个时间点或同一时刻
        t1 = t0 + 1
    lo, hi = min(prices), max(prices)
    if target:
        lo, hi = min(lo, target), max(hi, target)
    pad = max((hi - lo) * 0.12, 1)
    lo, hi = lo - pad, hi + pad

    left, right, top, bottom = 58, 18, 18, 34
    w, h = width - left - right, height - top - bottom

    def x(ts: float) -> float:
        return left + (ts - t0) / (t1 - t0) * w

    def y(v: float) -> float:
        return top + (hi - v) / (hi - lo) * h

    coords = [(x(p.ts), y(p.price)) for p in points]
    line = " ".join(f"{px:.1f},{py:.1f}" for px, py in coords)
    area = f"{left},{top + h} " + line + f" {left + w},{top + h}"
    low_i = min(range(len(prices)), key=lambda i: prices[i])
    low_x, low_y = coords[low_i]

    grid = []
    for i in range(5):
        yy = top + h * i / 4
        val = hi - (hi - lo) * i / 4
        grid.append(
            f'<line x1="{left}" y1="{yy:.1f}" x2="{left + w}" y2="{yy:.1f}" '
            f'stroke="#e8ebf1" stroke-width="1"/>'
            f'<text x="{left - 8}" y="{yy + 4:.1f}" text-anchor="end" '
            f'font-size="10" fill="#8b93a7">¥{val:,.0f}</text>'
        )
    dates = (
        f'<text x="{left}" y="{top + h + 22}" font-size="10" fill="#8b93a7">'
        f'{datetime.fromtimestamp(t0):%Y-%m-%d}</text>'
        f'<text x="{left + w}" y="{top + h + 22}" text-anchor="end" font-size="10" '
        f'fill="#8b93a7">{datetime.fromtimestamp(t1):%Y-%m-%d}</text>'
    )
    target_line = ""
    if target:
        ty = y(target)
        target_line = (
            f'<line x1="{left}" y1="{ty:.1f}" x2="{left + w}" y2="{ty:.1f}" '
            f'stroke="{GOOD}" stroke-width="1.5" stroke-dasharray="6 5" opacity="0.85"/>'
            f'<text x="{left + w - 4}" y="{ty - 5:.1f}" text-anchor="end" '
            f'font-size="10" fill="{GOOD}">目标 ¥{target:,.0f}</text>'
        )
    low_label = (
        f'<circle cx="{low_x:.1f}" cy="{low_y:.1f}" r="3.5" fill="{DANGER}"/>'
        f'<text x="{low_x + 7:.1f}" y="{low_y - 8:.1f}" font-size="10" fill="{DANGER}">'
        f'最低 ¥{prices[low_i]:,.0f}</text>'
    )
    return f"""<svg viewBox="0 0 {width} {height}" width="100%" role="img"
     style="max-width:{width}px;display:block" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{ACCENT}" stop-opacity="0.22"/>
      <stop offset="100%" stop-color="{ACCENT}" stop-opacity="0.02"/>
    </linearGradient>
  </defs>
  {''.join(grid)}
  <polygon points="{area}" fill="url(#g)"/>
  <polyline points="{line}" fill="none" stroke="{ACCENT}" stroke-width="2.2"
            stroke-linejoin="round" stroke-linecap="round"/>
  {target_line}
  {low_label}
  {dates}
</svg>"""


def _empty_svg(width: int, height: int, text: str) -> str:
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" xmlns="http://www.w3.org/2000/svg">'
        f'<rect width="{width}" height="{height}" fill="#f6f7f9" rx="10"/>'
        f'<text x="{width // 2}" y="{height // 2}" text-anchor="middle" '
        f'font-size="13" fill="#8b93a7">{escape(text)}</text></svg>'
    )


# ------------------------------------------------------------------ HTML
def _badge(action: str) -> str:
    color = {"立即购买": GOOD, "尽快入手": GOOD, "建议等待": WARN,
             "关注补货": DANGER}.get(action, "#6b7280")
    return (f'<span style="background:{color};color:#fff;border-radius:999px;'
            f'padding:3px 12px;font-size:12px;font-weight:600">{escape(action)}</span>')


def _stat(label: str, value: str) -> str:
    return (f'<div style="min-width:92px"><div style="font-size:11px;color:#8b93a7;'
            f'margin-bottom:2px">{escape(label)}</div>'
            f'<div style="font-size:15px;font-weight:600">{value}</div></div>')


def _card(item: ProductReport) -> str:
    s = item.stats
    rec = item.recommendation
    stats_html = ""
    if s:
        stats_html = (
            '<div style="display:flex;gap:22px;flex-wrap:wrap;margin:14px 0 6px">'
            + _stat("当前价", f"¥{s.current:,.0f}")
            + _stat("历史最低", f"¥{s.low:,.0f}")
            + _stat("历史最高", f"¥{s.high:,.0f}")
            + _stat("均价", f"¥{s.avg:,.0f}")
            + _stat("价格分位", f"{s.percentile:.0%}")
            + _stat("采样点", str(s.count))
            + "</div>"
        )
    target_html = ""
    if item.target_price:
        if s:
            gap = s.current - item.target_price
            state = "已达标 ✅" if gap <= 0 else f"还差 ¥{gap:,.0f}"
        else:
            state = "等待数据"
        target_html = (f'<div style="font-size:12px;color:#8b93a7">目标价 '
                       f'<b style="color:{GOOD}">¥{item.target_price:,.0f}</b> · {state}</div>')
    pred_html = ""
    if item.prediction:
        p = item.prediction
        pred_html = (
            f'<div style="background:#f6f7f9;border-radius:10px;padding:12px 14px;'
            f'font-size:13px;line-height:1.7;margin-top:12px">'
            f'<b>未来 30 天降价概率：{p.prob_30d:.0%}</b>'
            f'<div style="color:#5b6478;margin-top:4px">预计窗口 '
            f'{p.likely_start:%m-%d} ~ {p.likely_end:%m-%d}｜{escape(p.reason)}</div></div>'
        )
    reasons_html = ""
    if rec:
        reasons_html = (
            '<div style="margin-top:12px;font-size:13px;color:#3f4759">'
            '<div style="font-weight:600;margin-bottom:6px">建议理由</div><ul '
            'style="margin:0;padding-left:18px;line-height:1.75">'
            + "".join(f"<li>{escape(r)}</li>" for r in rec.reasons)
            + "</ul></div>"
        )
    inv_html = ""
    if item.inventory:
        inv = item.inventory
        qty = f"（剩 {inv.quantity} 件）" if inv.quantity is not None else ""
        inv_html = (f'<div style="font-size:12px;color:#8b93a7;margin-top:6px">库存：'
                    f'<b style="color:{"#3f4759" if inv.in_stock else DANGER}">'
                    f'{escape(inv.state or ("有货" if inv.in_stock else "无货"))}</b>{escape(qty)}</div>')
    variants_html = ""
    if item.variants:
        chips = "".join(
            f'<span style="display:inline-block;padding:2px 9px;border-radius:6px;'
            f'font-size:12px;margin:2px 4px 2px 0;'
            f'background:{"#e7f6ed" if v.in_stock else "#fdeaea"};'
            f'color:{"#0f7b46" if v.in_stock else DANGER}">{escape(v.variant)}</span>'
            for v in item.variants
        )
        variants_html = (f'<div style="margin-top:10px;font-size:12px;color:#8b93a7">'
                         f'尺码库存：{chips}</div>')
    win_html = ""
    if item.windows:
        rows = "".join(
            f'<tr><td style="padding:6px 8px;border-bottom:1px solid #eef0f4">{escape(w.tag)}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #eef0f4;color:#5b6478">'
            f'{w.start} ~ {w.end}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #eef0f4;text-align:right">'
            f'{w.expected_discount * 10:g} 折</td></tr>'
            for w in item.windows[:4]
        )
        win_html = (
            '<div style="margin-top:14px"><div style="font-weight:600;font-size:13px;'
            'margin-bottom:6px">购买窗口</div>'
            '<table style="width:100%;border-collapse:collapse;font-size:12px">'
            + rows + "</table></div>"
        )
    action = rec.action if rec else "数据不足"
    score = f"{rec.score}/100" if rec else "-"
    return f"""<section style="background:#fff;border-radius:16px;padding:22px 24px;
     box-shadow:0 1px 2px rgba(16,24,40,.05),0 8px 24px -12px rgba(16,24,40,.12);
     margin-bottom:20px">
  <div style="display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap">
    <div>
      <div style="font-size:17px;font-weight:700">{escape(item.title)}</div>
      <div style="font-size:12px;color:#8b93a7;margin-top:3px">{escape(item.subtitle)}</div>
      {target_html}
    </div>
    <div style="text-align:right">{_badge(action)}
      <div style="font-size:11px;color:#8b93a7;margin-top:6px">决策分 {score}</div></div>
  </div>
  {inv_html}
  <div style="margin-top:16px">{price_svg(item.points, target=item.target_price)}</div>
  {stats_html}
  {variants_html}
  {pred_html}
  {win_html}
  {reasons_html}
  <div style="margin-top:14px;font-size:11px;color:#a4abbc;word-break:break-all">
    来源：{escape(item.url)}</div>
</section>"""


def render_report(items: list[ProductReport], out_path: str | Path,
                  title: str = "Smart Shopper 购物分析报告") -> Path:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cards = "".join(_card(i) for i in items) or (
        '<div style="text-align:center;color:#8b93a7;padding:60px 0">暂无跟踪商品</div>'
    )
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title></head>
<body style="margin:0;background:#f2f4f8;font-family:-apple-system,BlinkMacSystemFont,
 'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;color:#101828">
<div style="max-width:880px;margin:0 auto;padding:32px 18px 60px">
  <header style="margin-bottom:22px">
    <div style="font-size:24px;font-weight:800;letter-spacing:-.02em">{escape(title)}</div>
    <div style="font-size:12px;color:#8b93a7;margin-top:6px">
      生成于 {now} · 共 {len(items)} 件商品 · 数据保存在本地 SQLite，价格仅供个人参考
    </div>
  </header>
  {cards}
  <footer style="text-align:center;font-size:11px;color:#a4abbc;margin-top:28px">
    Smart Shopper · 本地优先的降价监控与优惠叠加优化工具
  </footer>
</div></body></html>"""
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    return p


def write_png(points: list[PricePoint], out_path: str | Path,
              target: float | None = None, title: str = "") -> Path:
    """用 matplotlib 导出 PNG（可选依赖）。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as e:  # pragma: no cover - 依赖可选
        raise RuntimeError(
            "导出 PNG 需要 matplotlib：pip install matplotlib"
        ) from e

    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    dates = [datetime.fromtimestamp(pt.ts) for pt in points]
    prices = [pt.price for pt in points]
    fig, ax = plt.subplots(figsize=(9, 3.4), dpi=140)
    ax.plot(dates, prices, color=ACCENT, linewidth=2)
    ax.fill_between(dates, prices, min(prices), color=ACCENT, alpha=0.08)
    ax.scatter([dates[prices.index(min(prices))]], [min(prices)], color=DANGER, zorder=3, s=24)
    if target:
        ax.axhline(target, color=GOOD, linestyle="--", linewidth=1.5, label=f"目标 ¥{target:g}")
        ax.legend(frameon=False, fontsize=9)
    ax.set_title(title or "价格历史", fontsize=11)
    ax.grid(alpha=0.25, linewidth=0.6)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(labelsize=9, colors="#5b6478")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p
