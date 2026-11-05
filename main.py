#!/usr/bin/env python3
"""Smart Shopper —— 降价监控 · 优惠叠加优化 · 购买时机预测。

常用命令：
  add      添加跟踪商品（--target 目标价 / --size 关注尺码）
  edit     修改名称/类目/发售日/目标价/关注尺码
  check    抓取一轮价格与库存（--watch 持续监控）
  analyze  历史分析 + 降价预测 + 购买窗口 + 购买建议
  advise   一条命令出结论：买/等/关注 + 可购价 + 叠券 + 时机
  history  价格历史明细与 CSV 导出
  seed     补历史价格（慢慢买 / CSV），让预测立刻可用
  chart    导出走势图（svg / html / png）
  report   生成自包含 HTML 分析报告
  deal     单品叠券最优解（TOP3 方案）
  cart     购物车跨店凑单优化（JSON）
  bundle   多商品组合购买：怎么凑单最划算
  sizes    查看/刷新尺码库存
  fit      尺码换算助手（字母码 / 鞋码）
  serve    启动本地只读 Web 面板
  export / import-list  备份与分享购物清单
  status   监控健康度（抓取失败排查）
  test-notify 通知渠道自检
  init     生成配置文件
  demo     离线演示全部分析链路

示例：
  python main.py add "https://item.jd.com/100012043978.html" --category 羽绒服 --launch 2026-09-15 --target 500 --size M
  python main.py check --watch
  python main.py analyze 1
  python main.py deal --price 899 --platform jd
  python main.py report --out report.html
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

from core.models import (InventorySnapshot, PLATFORM_GENERIC, PLATFORM_UNIQLO,
                         PLATFORMS, PricePoint, Product, VariantSnapshot)
from core import report as core_report
from core import sparkline as core_sparkline
from core.currency import Currency, from_config as currency_from_config
from core.storage import Store
from core.variants import describe_watched, match_variants
from datasource import HistorySourceError, dedupe, fetch_manmanbuy, load_csv
from monitors import MonitorError, get_monitor, resolve_product_url
from monitors.uniqlo_parse import CODE_RE, size_sort_key
from notifier import Notifier
from optimizer.bundle import plan_bundle
from optimizer.deals import (CartItem, DiscountRule, best_deal, best_price,
                             demo_rules, optimize_cart)
from predictor import season, sizing
from predictor.forecast import (PriceHistory, predict_drop, recommend,
                                trend_forecast)
from webui.dashboard import serve as serve_dashboard

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = {
    "db": str(ROOT / "shopper.db"),
    "check_interval_seconds": 3600,
    "jd": {"area": "1_72_2799_0"},
    "uniqlo": {"api_template": ""},
    "taobao": {"cookie": ""},
    "notify": {},
}


# ------------------------------------------------------------------ 工具
def load_config(path: str | None) -> dict:
    """优先级：SMART_SHOPPER_CONFIG 环境变量（JSON）> config.json > 默认值。"""
    p = Path(path or ROOT / "config.json")
    cfg = dict(DEFAULT_CONFIG)
    if p.exists():
        cfg.update(json.loads(p.read_text(encoding="utf-8")))
    env = os.environ.get("SMART_SHOPPER_CONFIG")
    if env:
        cfg.update(json.loads(env))
    return cfg


UNIQLO_PRODUCT_PAGE = "https://www.uniqlo.cn/product-detail.html?productCode={pid}"


def resolve_product(store: Store, key: str) -> Product | None:
    key = (key or "").strip()
    if key.isdigit():
        prods = store.list_products()
        idx = int(key) - 1
        if 0 <= idx < len(prods):
            return prods[idx]
    p = store.get_product(key)
    if p:
        return p
    # 裸货号（u + 13 位数字）直接命中已跟踪的优衣库商品
    if CODE_RE.fullmatch(key):
        return store.get_product(f"{PLATFORM_UNIQLO}:{key.lower()}")
    return None


def _normalize_product_url(platform: str, pid: str, url: str) -> str:
    """裸优衣库货号补成商品页链接，方便后续 seed / 导出复用。"""
    if platform == PLATFORM_UNIQLO and CODE_RE.fullmatch((url or "").strip()):
        return UNIQLO_PRODUCT_PAGE.format(pid=pid)
    return url


def _stocked_price(variants: list[VariantSnapshot]) -> tuple[VariantSnapshot | None, float | None]:
    """可购最低价：只在有货且带价格的变体里取，售罄配色不参与决策。

    注意：商品级历史价可能来自已售罄配色（例如 ¥699），不能直接当作「现在能买到的价」。
    """
    candidates = [v for v in variants if v.in_stock and v.price]
    if not candidates:
        return None, None
    return min(candidates, key=lambda v: v.price), min(v.price for v in candidates)


def _history_ready(points: list[PricePoint]) -> bool:
    """历史是否够判断趋势：至少 3 个采样点且跨度 ≥ 3 天。"""
    return len(points) >= 3 and (points[-1].ts - points[0].ts) >= 3 * 86400


def _describe_deal(res) -> str:
    parts = []
    for rule, before, _after in res.applied:
        if rule.kind == "every_full_off" and rule.step:
            parts.append(f"{rule.name} ×{int(before // rule.step)}")
        else:
            parts.append(rule.name)
    return " + ".join(parts) or "无可用优惠"


def _trim_reason(text: str, limit: int = 25) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


# 展示币种：价格始终以人民币入库，仅输出时换算
_CURRENCY = Currency("CNY")


def money(value) -> str:
    return _CURRENCY.format(value)


def _configure_currency(cfg: dict, override: str | None) -> None:
    global _CURRENCY
    conf = cfg.get("currency")
    rates = conf.get("rates") if isinstance(conf, dict) else None
    _CURRENCY = Currency(override, rates) if override else currency_from_config(cfg)


# ------------------------------------------------------------------ 子命令
def cmd_add(args, store: Store, cfg: dict) -> int:
    if args.platform == PLATFORM_GENERIC:
        platform, pid = PLATFORM_GENERIC, args.url
    elif args.platform:
        _, pid = resolve_product_url(args.url)
        platform = args.platform
    else:
        platform, pid = resolve_product_url(args.url)
    p = Product(platform=platform, pid=pid,
                url=_normalize_product_url(platform, pid, args.url),
                name=args.name or "", category=season.normalize_category(args.category or "general"),
                launch_date=args.launch, target_price=args.target,
                watched_variant=(args.size or "").strip())
    if not store.add_product(p):
        print(f"已存在：{p.display_id()}（改名称/目标价请用 edit 命令）")
        return 1
    extras = []
    if args.target:
        extras.append(f"目标价 ¥{args.target:g}")
    if args.size:
        extras.append(f"关注尺码 {args.size}")
    tip = "，" + "，".join(extras) if extras else ""
    print(f"已添加跟踪：{p.display_id()}{tip}")
    if platform == PLATFORM_GENERIC:
        print("提示：通用站点需要在 config.json 的 generic.sites 里为 "
              "该域名配置 price_regex / stock_regex，详见 README「通用站点监控」。")
    print("提示：执行 `python main.py check` 立即抓取一次价格与库存。")
    return 0


def cmd_edit(args, store: Store, cfg: dict) -> int:
    p = resolve_product(store, args.key)
    if not p:
        print(f"未找到商品：{args.key}")
        return 1
    changed = []
    if args.name is not None:
        p.name = args.name
        changed.append("名称")
    if args.category is not None:
        p.category = season.normalize_category(args.category)
        changed.append(f"类目={p.category}")
    if args.launch is not None:
        p.launch_date = args.launch or None
        changed.append(f"发售日={p.launch_date}")
    if args.target is not None:
        p.target_price = args.target or None
        changed.append(f"目标价={p.target_price}")
    if args.size is not None:
        p.watched_variant = args.size
        changed.append(f"关注尺码={args.watched_variant or '（清空）'}")
    if not changed:
        print("没有指定要修改的字段（可用 --name/--category/--launch/--target/--size）")
        return 1
    store.update_product(p)
    print(f"已更新 {p.display_id()}：" + "，".join(changed))
    return 0


def cmd_list(args, store: Store, cfg: dict) -> int:
    prods = store.list_products()
    if not prods:
        print("暂无跟踪商品，用 add 命令添加。")
        return 0
    print(f"{'序号':<4} {'商品':<16} {'类目':<11} {'当前价':>8} {'最低':>8} {'目标':>8} {'库存':<5} 名称")
    for i, p in enumerate(prods, 1):
        last = store.last_price(p.display_id())
        low = store.lowest_price(p.display_id())
        inv = store.last_inventory(p.display_id())
        variants = store.latest_variants(p.display_id())
        _variant, buy_price = _stocked_price(variants)
        if buy_price is not None:
            cur = money(buy_price)  # 只展示「有货 SKU」的最低价，避免售罄配色误导
        elif variants and not any(v.in_stock for v in variants):
            cur = "售罄"
        else:
            cur = money(last.price) if last else "-"
        lo = money(low.price) if low else "-"
        tgt = money(p.target_price) if p.target_price else "-"
        stock = (inv.state or ("有货" if inv.in_stock else "无货")) if inv else "-"
        name = p.name + (f" [{p.watched_variant}]" if p.watched_variant else "")
        print(f"{i:<4} {p.display_id():<16} {p.category:<11} {cur:>8} {lo:>8} {tgt:>8} {stock:<5} {name}")
    return 0


def cmd_rm(args, store: Store, cfg: dict) -> int:
    ok = store.remove_product(args.key)
    print("已删除" if ok else "未找到该商品")
    return 0 if ok else 1


# 各类提醒的默认冷却时长（小时）；0 表示每次都提醒
DEFAULT_ALERT_COOLDOWN = {
    "drop": 12,      # 降价
    "low": 24,       # 历史新低
    "target": 0,     # 目标价（本身只在穿越时触发）
    "restock": 6,    # 补货
    "variant": 6,    # 尺码补货
    "stockout": 12,  # 变为无货
    "monitor": 24,   # 监控失效（连续抓取失败）
}


def _cooldowns(cfg: dict) -> dict:
    out = dict(DEFAULT_ALERT_COOLDOWN)
    custom = cfg.get("alert_cooldown_hours")
    if isinstance(custom, (int, float)):
        out = {k: float(custom) for k in out}
    elif isinstance(custom, dict):
        out.update({k: float(v) for k, v in custom.items() if isinstance(v, (int, float))})
    return out


def _alert_allowed(store: Store, did: str, kind: str, cooldowns: dict, now_ts: float) -> bool:
    # 尺码提醒的 kind 形如 variant:黑色 / M:restock，配置里只写基础类型 variant
    hours = cooldowns.get(kind)
    if hours is None:
        hours = cooldowns.get(kind.split(":", 1)[0], 12)
    if hours <= 0:
        return True
    last = store.last_alert(did, kind)
    return last is None or now_ts - last >= hours * 3600


def check_cycle(store: Store, cfg: dict, notifier: Notifier) -> None:
    """抓取所有跟踪商品一轮，记录价格/库存并发出降价、补货提醒。"""
    prods = store.list_products()
    if not prods:
        print("暂无跟踪商品。")
        return
    cooldowns = _cooldowns(cfg)
    fail_threshold = int(cfg.get("health_alert_after", 3))
    alerts: list[tuple[str, str, str]] = []  # (kind, display_id, 文案)

    def add_alert(kind: str, did: str, text: str) -> None:
        if _alert_allowed(store, did, kind, cooldowns, time.time()):
            alerts.append((kind, did, text))
            store.mark_alert(did, kind)

    for p in prods:
        did = p.display_id()
        try:
            monitor = get_monitor(p.platform, cfg)
            point, inv = monitor.fetch(p.pid)
        except MonitorError as e:
            health = store.record_fetch(did, False, str(e))
            print(f"[失败] {did}: {e}（连续 {health.consecutive_failures} 次）")
            if health.consecutive_failures >= fail_threshold:
                add_alert("monitor", did,
                          f"[监控异常] {p.name or did} 已连续 {health.consecutive_failures} 次抓取失败：{e}")
            continue
        if not point and not inv:
            health = store.record_fetch(did, False, "未取到任何数据")
            print(f"[失败] {did}: 未取到任何数据（连续 {health.consecutive_failures} 次）")
            if health.consecutive_failures >= fail_threshold:
                add_alert("monitor", did,
                          f"[监控异常] {p.name or did} 已连续 {health.consecutive_failures} 次抓取失败")
            continue
        store.record_fetch(did, True)
        prev = store.last_price(did)
        prev_inv = store.last_inventory(did)
        if point:
            store.add_price(did, point)
            line = f"[记录] {did} ¥{prev.price:g} → ¥{point.price:g}" if prev else f"[记录] {did} ¥{point.price:g}"
            print(line)
            if prev and point.price < prev.price - 0.01:
                pct = (prev.price - point.price) / prev.price * 100
                add_alert("drop", did,
                          f"[降价] {p.name or did}: ¥{prev.price:g} → ¥{point.price:g}（-{pct:.1f}%）")
                low = store.lowest_price(did)
                if low and point.price <= low.price + 0.01:
                    add_alert("low", did, f"[新低] {p.name or did} 刷新历史最低 ¥{point.price:g}")
            # 目标价只在「穿越」时提醒一次，避免每轮重复轰炸
            if p.target_price and point.price <= p.target_price:
                crossed = prev is None or prev.price > p.target_price
                if crossed:
                    add_alert("target", did,
                              f"[达标] {p.name or did} 当前 ¥{point.price:g} 已达到目标价 ¥{p.target_price:g}")
            if getattr(monitor, "last_name", "") and not p.name:
                p.name = monitor.last_name
                store.update_product(p)
        if inv:
            store.add_inventory(did, inv)
            if prev_inv and not prev_inv.in_stock and inv.in_stock:
                add_alert("restock", did, f"[补货] {p.name or did} 已补货（{inv.state}）")
            if prev_inv and prev_inv.in_stock and not inv.in_stock:
                add_alert("stockout", did, f"[无货] {p.name or did} 变为无货")

        # 尺码/SKU 级库存（优衣库、京东、通用站点支持）
        for kind, text in _check_variants(store, monitor, p):
            add_alert(kind, did, text)

    if alerts:
        notifier.send("Smart Shopper 价格提醒", [text for _k, _d, text in alerts])
        print(f"已发送 {len(alerts)} 条提醒（冷却期见 config.alert_cooldown_hours）")


def _check_variants(store: Store, monitor, p: Product) -> list[tuple[str, str]]:
    """抓取尺码库存并检测关注尺码的补货/售罄状态变化。"""
    try:
        variants = monitor.variants(p.pid) if hasattr(monitor, "variants") else []
    except Exception:  # noqa: BLE001
        return []
    if not variants:
        return []
    did = p.display_id()
    old = {v.variant: v for v in store.latest_variants(did)}
    store.add_variants(did, variants)
    alerts: list[tuple[str, str]] = []

    if p.watched_variant:
        targets = match_variants(variants, p.watched_variant)
        for cur in targets:
            before = old.get(cur.variant)
            if not before:
                continue
            qty = f"，剩 {cur.quantity} 件" if cur.quantity is not None else ""
            if not before.in_stock and cur.in_stock:
                alerts.append((
                    f"variant:{cur.variant}:restock",
                    f"[尺码补货] {p.name or did} 尺码 {cur.variant} 已补货（{cur.label}{qty}）",
                ))
            elif before.in_stock and not cur.in_stock:
                alerts.append((
                    f"variant:{cur.variant}:soldout",
                    f"[尺码售罄] {p.name or did} 尺码 {cur.variant} 已售罄",
                ))
    else:
        newly = [v for v in variants
                 if v.in_stock and old.get(v.variant) and not old[v.variant].in_stock]
        if newly:
            labels = "、".join(v.variant for v in newly)
            alerts.append(("variant:any:restock", f"[尺码补货] {p.name or did} {labels} 补货了"))
    return alerts


def cmd_check(args, store: Store, cfg: dict) -> int:
    notifier = Notifier(cfg.get("notify"))
    interval = args.interval or cfg.get("check_interval_seconds", 3600)
    if args.watch:
        print(f"持续监控中，每 {interval} 秒一轮（Ctrl+C 退出）...")
        while True:
            check_cycle(store, cfg, notifier)
            time.sleep(interval)
    check_cycle(store, cfg, notifier)
    return 0


def cmd_analyze(args, store: Store, cfg: dict) -> int:
    p = resolve_product(store, args.key)
    if not p:
        print(f"未找到商品：{args.key}（先用 list 查看序号）")
        return 1
    today = date.today()
    hist = PriceHistory(store.get_prices(p.display_id()))
    stats = hist.stats()
    prof = season.profile_of(p.category)
    windows = season.best_buy_windows(p.category, p.launch(), today)
    prediction = predict_drop(hist, today)
    trend = trend_forecast(hist)
    inv = store.last_inventory(p.display_id())
    rec = recommend(hist, inv, prediction, windows, today, trend=trend)

    print("=" * 62)
    print(f"商品分析：{p.name or p.display_id()}   [{p.platform}]   币种：{_CURRENCY.describe()}")
    print("=" * 62)
    if p.target_price:
        if stats:
            gap = stats.current - p.target_price
            gap_txt = f"还差 {money(gap)}" if gap > 0 else "已达标"
            print(f"目标价 {money(p.target_price)}（当前 {money(stats.current)}，{gap_txt}）")
        else:
            print(f"目标价 {money(p.target_price)}")
    if stats:
        print(f"当前价 {money(stats.current)} | 最低 {money(stats.low)} | 最高 {money(stats.high)} "
              f"| 均价 {money(stats.avg)} | 采样 {stats.count} 次")
        print(f"价格分位 {stats.percentile:.0%} | 有效降价 {stats.drop_count} 次"
              + (f" | 平均间隔 {stats.avg_drop_interval_days:g} 天 | 平均降幅 {stats.avg_drop_depth:.0%}"
                 if stats.avg_drop_interval_days and stats.avg_drop_depth else ""))
        print(f"走势 {core_sparkline.trend_line([pt.price for pt in hist.points])}")
    else:
        print("价格历史不足 2 个采样点，先执行 check 积累数据。以下基于类目模型给出建议。")
    if inv:
        print(f"库存：{'有货' if inv.in_stock else '无货'} {inv.state}"
              + (f"（剩 {inv.quantity} 件）" if inv.quantity is not None else ""))
    variants = store.latest_variants(p.display_id())
    if variants:
        watch = f"（关注 {p.watched_variant}）" if p.watched_variant else ""
        chips = "  ".join(f"{v.variant}:{v.label}" for v in variants)
        print(f"尺码库存{watch}：{chips}")

    print("-" * 62)
    print(f"◆ 降价预测（未来 30 天）：概率 {prediction.prob_30d:.0%}")
    if prediction.likely_start:
        print(f"  预计窗口：{prediction.likely_start.strftime('%m-%d')} ~ {prediction.likely_end.strftime('%m-%d')}")
    print(f"  依据：{prediction.reason}")
    if trend:
        print(f"◆ 趋势外推（线性回归）：{trend.describe(money)}")
        print(f"  斜率 {trend.slope_per_day:+g}/天 · 残差 σ={trend.residual_std:g} · 样本 {trend.samples}")

    print("-" * 62)
    print(f"◆ 购买时机（类目：{prof.name}）")
    for i, w in enumerate(windows[:5], 1):
        mark = "← 当前正处于该窗口" if w.overlaps_today(today) else ""
        print(f"  {i}. [{w.tag}] {w.start} ~ {w.end}  预期 {w.expected_discount * 10:g} 折")
        print(f"     {w.reason}{mark}")
    if prof.tip:
        print(f"  类目经验：{prof.tip}")

    print("-" * 62)
    print(f"◆ 购买建议：▶ {rec.action}（决策分 {rec.score}/100）")
    for r in rec.reasons:
        print(f"  - {r}")
    if rec.expected_low:
        print(f"  → {rec.expected_low}")
    return 0


def cmd_deal(args, store: Store, cfg: dict) -> int:
    rules = _load_rules(args.coupons) if args.coupons else demo_rules(args.platform)
    results = best_deal(args.price, rules, top_n=3)
    plat_name = {"taobao": "淘宝", "jd": "京东", "uniqlo": "优衣库"}.get(args.platform, args.platform)
    print(f"¥{args.price:g} 在 {plat_name} 的最优叠券方案 TOP3：")
    for i, res in enumerate(results, 1):
        used = " + ".join(r.name for r, _b, _a in res.applied) or "（无可用优惠）"
        print(f"\n#{i}  到手 ¥{res.final:.2f}（省 ¥{res.saved:.2f}）  叠加：{used}")
        if i == 1:
            print(res.breakdown())
    return 0


def cmd_cart(args, store: Store, cfg: dict) -> int:
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    items = [CartItem(**{k: v for k, v in d.items() if k in CartItem.__dataclass_fields__})
             for d in data.get("items", [])]
    rules = [DiscountRule.from_dict(d) for d in data.get("coupons", [])]
    if not items:
        print("购物车为空")
        return 1
    out = optimize_cart(items, rules)
    print("分店最优：")
    for store_name, res in out["stores"].items():
        print(f"  [{store_name}] 原 ¥{res.original:.2f} → 券后 ¥{res.final:.2f}")
    g = out["grand"]
    print(f"跨店/平台券后：¥{g.final:.2f}")
    print("\n凑单建议：")
    for t in out["tips"]:
        print(f"  - {t}")
    return 0


def _load_rules(path: str) -> list[DiscountRule]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("coupons", []) if isinstance(data, dict) else data
    return [DiscountRule.from_dict(d) for d in items]


def cmd_demo(args, store: Store, cfg: dict) -> int:
    """离线演示：生成一件羽绒服的完整数据并跑通 全部分析链路。"""
    print("=" * 62)
    print("Smart Shopper 离线演示（数据为模拟）")
    print("=" * 62)
    launch = date(date.today().year, 9, 1)
    base_ts = datetime.combine(launch, datetime.min.time()).timestamp()
    now_ts = time.time()
    # 模拟一件 899 元羽绒服的价格轨迹
    from core.models import PricePoint
    track = [(0.00, 899), (20, 869), (35, 899), (55, 819), (68, 759), (72, 799), (75, 779)]
    points = []
    for days, price in track:
        ts = base_ts + days * 86400
        if ts <= now_ts:
            points.append(PricePoint(ts=ts, price=float(price), original_price=899.0))
    hist = PriceHistory(points)
    today = date.today()
    prof = season.profile_of("down_jacket")
    windows = season.best_buy_windows("down_jacket", launch, today)
    prediction = predict_drop(hist, today)
    inv = InventorySnapshot(ts=now_ts, in_stock=True, state="现货", quantity=23)
    rec = recommend(hist, inv, prediction, windows, today)

    stats = hist.stats()
    print(f"\n商品：模拟羽绒服（{launch} 上架，吊牌价 ¥899）  库存：{inv.state}，剩 {inv.quantity} 件")
    print(f"当前价 ¥{stats.current:g} | 最低 ¥{stats.low:g} | 均价 ¥{stats.avg:g} | 分位 {stats.percentile:.0%}")
    print(f"\n◆ 降价预测：30 天内概率 {prediction.prob_30d:.0%}")
    print(f"  窗口 {prediction.likely_start.strftime('%m-%d')} ~ {prediction.likely_end.strftime('%m-%d')}")
    print(f"  依据：{prediction.reason}")
    print(f"\n◆ 羽绒服购买窗口 TOP：")
    for i, w in enumerate(windows[:4], 1):
        mark = "  ← 今天正处于该窗口" if w.overlaps_today(today) else ""
        print(f"  {i}. [{w.tag}] {w.start} ~ {w.end}  预期 {w.expected_discount * 10:g} 折{mark}")
    print(f"\n◆ 建议：▶ {rec.action}（{rec.score}/100）")
    for r in rec.reasons:
        print(f"  - {r}")

    print("\n" + "-" * 62)
    cmd_deal_price = 899
    print(f"叠券演示：吊牌 ¥{cmd_deal_price} 过大促优惠（淘宝示例）")
    results = best_deal(cmd_deal_price, demo_rules("taobao"), top_n=1)
    print(results[0].breakdown())

    print("\n" + "-" * 62)
    print("组合凑单演示：羽绒服 ¥779 + 围巾 ¥199")
    plan = plan_bundle(
        [CartItem("羽绒服", 779, "优衣库"), CartItem("围巾", 199, "优衣库")],
        demo_rules("taobao"), filler_max=120,
    )
    print(plan.describe())
    print("\n提示：python main.py report  可生成带价格曲线的 HTML 报告。")
    return 0


def cmd_history(args, store: Store, cfg: dict) -> int:
    p = resolve_product(store, args.key)
    if not p:
        print(f"未找到商品：{args.key}（先用 list 查看序号）")
        return 1
    points = store.get_prices(p.display_id())
    if args.days:
        cutoff = time.time() - args.days * 86400
        points = [pt for pt in points if pt.ts >= cutoff]
    if not points:
        print("暂无价格记录，先执行 check。")
        return 1
    print(f"{p.name or p.display_id()} 共 {len(points)} 条价格记录：")
    print(f"走势 {core_sparkline.trend_line([pt.price for pt in points])}")
    tail = points[-args.limit:] if args.limit else points
    for pt in tail:
        orig = f"（吊牌 {money(pt.original_price)}）" if pt.original_price else ""
        print(f"  {fmt_ts(pt.ts)}  {money(pt.price)}{orig}")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["datetime", "price", "original_price"])
            for pt in points:
                w.writerow([fmt_ts(pt.ts), pt.price, pt.original_price or ""])
        print(f"已导出：{args.csv}")
    return 0


def cmd_init(args, store: Store, cfg: dict) -> int:
    target = ROOT / "config.json"
    if target.exists() and not args.force:
        print(f"config.json 已存在（{target}）。如需重新生成请加 --force。")
        return 1
    example = ROOT / "config.example.json"
    if example.exists():
        target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        target.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {target}")
    print("下一步：")
    print("  1. 按需编辑 config.json（淘宝 Cookie、京东区域码、通知渠道、SMTP 邮件）")
    print("  2. python main.py add <商品链接> --target 500 --size M  # 目标价/关注尺码可选")
    print("  3. python main.py test-notify                        # 通知自检")
    print("  4. python main.py check --watch                      # 持续监控")
    print("  5. python main.py report / serve                     # HTML 报告 / 本地面板")
    print("  云端免费监控见 README「部署到 GitHub Actions」章节。")
    return 0


def _report_item(store: Store, p: Product, today: date) -> core_report.ProductReport:
    """把单个商品的监控数据打包成报告卡片数据。"""
    points = store.get_prices(p.display_id())
    hist = PriceHistory(points)
    stats = hist.stats()
    prof = season.profile_of(p.category)
    windows = season.best_buy_windows(p.category, p.launch(), today)
    pred = predict_drop(hist, today)
    trend = trend_forecast(hist)
    inv = store.last_inventory(p.display_id())
    rec = recommend(hist, inv, pred, windows, today, trend=trend)
    variants = store.latest_variants(p.display_id())
    sub = (f"{p.display_id()} · {prof.name} · 采样 "
           f"{stats.count if stats else 0} 次 · 更新 {datetime.fromtimestamp(points[-1].ts):%m-%d %H:%M}"
           if points else f"{p.display_id()} · {prof.name} · 暂无数据")
    return core_report.ProductReport(
        title=p.name or p.display_id(), subtitle=sub, url=p.url, points=points,
        stats=stats, prediction=pred, recommendation=rec, inventory=inv,
        variants=variants, windows=windows, target_price=p.target_price,
        category_name=prof.name, trend=trend,
    )


def cmd_seed(args, store: Store, cfg: dict) -> int:
    """给商品补历史价格（慢慢买 / CSV），让预测立刻有数据。"""
    p = resolve_product(store, args.key)
    if not p:
        print(f"未找到商品：{args.key}")
        return 1
    try:
        if args.csv:
            points = load_csv(args.csv)
            source = f"CSV（{args.csv}）"
        else:
            points = fetch_manmanbuy(args.url or p.url)
            source = "慢慢买"
    except HistorySourceError as e:
        print(f"导入失败：{e}")
        print("可用 --csv 提供自己的历史数据（表头 datetime,price）。")
        return 1

    points = dedupe(points)
    existing = {pt.ts for pt in store.get_prices(p.display_id())}
    added = 0
    for pt in points:
        if pt.ts in existing:
            continue
        store.add_price(p.display_id(), pt)
        added += 1
    all_points = store.get_prices(p.display_id())
    stats = PriceHistory(all_points).stats()
    print(f"已从{source}补入 {added} 条历史价格（共 {len(points)} 条，重复已跳过）")
    if stats:
        print(f"历史区间：{datetime.fromtimestamp(all_points[0].ts):%Y-%m-%d} ~ "
              f"{datetime.fromtimestamp(all_points[-1].ts):%Y-%m-%d}"
              f" | 最低 {money(stats.low)} | 最高 {money(stats.high)} | 均价 {money(stats.avg)}")
        print("现在可以执行 analyze 查看预测与购买建议。")
    return 0


def cmd_chart(args, store: Store, cfg: dict) -> int:
    p = resolve_product(store, args.key)
    if not p:
        print(f"未找到商品：{args.key}")
        return 1
    points = store.get_prices(p.display_id())
    if not points:
        print("暂无价格记录，先执行 check 或 seed。")
        return 1
    fmt = args.format.lower()
    out = Path(args.out or f"chart_{p.platform}_{p.pid}.{fmt}")
    title = p.name or p.display_id()
    try:
        if fmt == "png":
            core_report.write_png(points, out, target=p.target_price, title=title)
        elif fmt == "html":
            svg = core_report.price_svg(points, target=p.target_price)
            out.write_text(
                "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                f"<title>{title} 价格走势</title></head><body style=\"margin:24px;"
                "font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif\">"
                f"<h2 style=\"font-size:16px\">{title}</h2>{svg}</body></html>",
                encoding="utf-8",
            )
        else:
            out.write_text(core_report.price_svg(points, target=p.target_price), encoding="utf-8")
    except RuntimeError as e:
        print(str(e))
        return 1
    print(f"已导出走势图：{out}（{len(points)} 个采样点）")
    return 0


def cmd_report(args, store: Store, cfg: dict) -> int:
    prods = store.list_products()
    if not prods:
        print("暂无跟踪商品，先 add 几件再生成报告。")
        return 1
    if args.key:
        p = resolve_product(store, args.key)
        if not p:
            print(f"未找到商品：{args.key}")
            return 1
        prods = [p]
    today = date.today()
    items = [_report_item(store, p, today) for p in prods]
    out = core_report.render_report(items, args.out)
    print(f"已生成 HTML 报告：{out}（{len(items)} 件商品）")
    print("用浏览器打开即可查看价格曲线、预测与购买建议。")
    return 0


def cmd_bundle(args, store: Store, cfg: dict) -> int:
    """多商品组合购买：怎么凑单最划算。"""
    if args.file:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
        items = [CartItem(**{k: v for k, v in d.items()
                             if k in CartItem.__dataclass_fields__})
                 for d in data.get("items", [])]
        rules = [DiscountRule.from_dict(d) for d in data.get("coupons", [])]
        if not rules:
            rules = demo_rules(args.platform)
    else:
        prods = store.list_products()
        items = []
        for p in prods:
            last = store.last_price(p.display_id())
            if not last:
                continue
            items.append(CartItem(name=p.name or p.display_id(),
                                  price=last.price,
                                  store={"uniqlo": "优衣库", "jd": "京东",
                                         "taobao": "淘宝/天猫"}.get(p.platform, p.platform)))
        rules = ([DiscountRule.from_dict(d) for d in
                  json.loads(Path(args.coupons).read_text(encoding="utf-8"))]
                 if args.coupons else demo_rules(args.platform))
        if not items:
            print("没有可用的商品价格，先 add + check，或改用 --file 指定清单。")
            return 1
    plan = plan_bundle(items, rules, filler_max=args.filler_max)
    print("=" * 62)
    print("组合购买方案（商品：" + "、".join(f"{i.name} ¥{i.price:g}" for i in items) + "）")
    print("=" * 62)
    for store_name, res in plan.store_results.items():
        print(f"[{store_name}] ¥{res.original:.2f} → ¥{res.final:.2f}")
    print()
    print(plan.describe())
    print()
    print("明细：")
    print(plan.grand.breakdown() if plan.grand else "")
    return 0


def cmd_sizes(args, store: Store, cfg: dict) -> int:
    """查看/刷新尺码库存。"""
    p = resolve_product(store, args.key)
    if not p:
        print(f"未找到商品：{args.key}")
        return 1
    if args.refresh:
        try:
            monitor = get_monitor(p.platform, cfg)
            variants = monitor.variants(p.pid)
        except Exception as e:  # noqa: BLE001
            print(f"抓取尺码库存失败：{e}")
            return 1
        if variants:
            store.add_variants(p.display_id(), variants)
            print(f"已刷新 {len(variants)} 个尺码库存。")
        else:
            print("该商品/平台暂不支持尺码级库存（优衣库、京东、部分通用站点可用）。")
    variants = store.latest_variants(p.display_id())
    if not variants:
        print("暂无尺码库存数据（先 check 或加 --refresh）。")
        return 1
    watched = describe_watched(p.watched_variant) if p.watched_variant else "未设置"
    print(f"{p.name or p.display_id()} 尺码库存（关注：{watched}）")
    variants = sorted(
        variants, key=lambda v: (v.color or "", size_sort_key(v.size or v.variant))
    )
    for v in variants:
        mark = "  ← 关注" if p.watched_variant and match_variants([v], p.watched_variant) else ""
        qty = f"，剩 {v.quantity} 件" if v.quantity is not None else ""
        price = ""
        if v.price:
            price = f"  {money(v.price)}"
        if v.discount:
            price += f"（原价 {money(v.original_price)}，-{v.discount:.0%}）"
        if v.member_price:
            price += f"  会员 {money(v.member_price)}"
        if v.limit:
            price += f"  限购 {v.limit}"
        print(f"  {v.variant:<20} {v.label}{qty}{price}  {fmt_ts(v.ts)}{mark}")
    return 0


def cmd_advise(args, store: Store, cfg: dict) -> int:
    """一条命令给出结论：买 / 等 / 关注，附可购价、关注尺码、叠券与时机。

    价格口径红线：所有结论价都基于「有货」的 SKU；商品级历史价（可能来自售罄配色）
    只用于价格位置与趋势，不能直接当作现在能买到的价。
    """
    p, added = _ensure_product(store, args.key)
    if not p:
        return 1
    did = p.display_id()
    if added:
        print(f"已自动加入跟踪：{did}")

    now = time.time()
    variants: list[VariantSnapshot] = []
    refresh_error = ""
    try:
        monitor = get_monitor(p.platform, cfg)
        variants = list(monitor.variants(p.pid) or [])
    except Exception as e:  # noqa: BLE001
        refresh_error = str(e)
    if variants:
        store.add_variants(did, variants)
    else:
        variants = store.latest_variants(did)
        if refresh_error:
            print(f"[失败] {did}：{refresh_error}")
        if not variants:
            print("该商品/平台暂不支持尺码级库存，无法判断可购价。")
            return 1
        print(f"（刷新失败，以下使用上次记录的尺码数据，时间 {fmt_ts(max(v.ts for v in variants))}）")

    buy_variant, buy_price = _stocked_price(variants)
    watched_variants = match_variants(variants, p.watched_variant) if p.watched_variant else []
    watched_variant = None
    if watched_variants:
        stocked = [v for v in watched_variants if v.in_stock and v.price]
        watched_variant = min(stocked, key=lambda v: v.price) if stocked else watched_variants[0]

    points = store.get_prices(did)
    # 决策用当前可购价替换最后一条历史价，避免用售罄配色价判断买/等
    decision_points = list(points)
    if buy_price and decision_points:
        decision_points[-1] = PricePoint(decision_points[-1].ts, buy_price,
                                         decision_points[-1].original_price)
    hist = PriceHistory(decision_points)
    stats = hist.stats()
    ready = _history_ready(points)
    in_stock_any = any(v.in_stock for v in variants)
    qty = sum(v.quantity for v in variants if v.in_stock and v.quantity is not None) or None
    inv = InventorySnapshot(now, in_stock_any, "有货" if in_stock_any else "无货", qty)
    today = date.today()
    prediction = predict_drop(hist, today)
    trend = trend_forecast(hist)
    windows = season.best_buy_windows(p.category, p.launch(), today)
    rec = recommend(hist, inv, prediction, windows, today, trend=trend)

    if not in_stock_any:
        action, reason = "关注", "全部尺码售罄，等补货提醒"
    elif p.watched_variant and watched_variant and not watched_variant.in_stock:
        action, reason = "关注", "关注尺码无货，可换色或等补货"
    elif not ready:
        action, reason = "等", "历史数据不足，先跑几天 check"
    else:
        if rec.action in ("立即购买", "尽快入手"):
            action = "买"
        elif rec.action == "关注补货":
            action = "关注"
        else:
            action = "等"
        reason = _trim_reason(rec.reasons[0] if rec.reasons else "")

    print(f"【结论】{action} — {reason}")
    print()
    if buy_price:
        print(f"可购最低价：{money(buy_price)}（{buy_variant.variant}）")
    else:
        print("可购最低价：无货（全部尺码售罄）")

    if p.watched_variant and watched_variant:
        qty_txt = f"，剩 {watched_variant.quantity} 件" if watched_variant.quantity is not None else ""
        state = "有货" if watched_variant.in_stock else "无货"
        # 全部售罄时不报价，避免「无货却给一个可买价」的误导
        price_txt = money(watched_variant.price) + " · " if in_stock_any and watched_variant.price else ""
        same = (p.watched_variant.replace(" ", "").replace("/", "")
                == watched_variant.variant.replace(" ", "").replace("/", ""))
        extra = "" if same else f"（{watched_variant.variant}）"
        print(f"你关注的「{p.watched_variant}」：{price_txt}{state}{extra}{qty_txt}")
    elif p.watched_variant:
        print(f"你关注的「{p.watched_variant}」：本次数据中未找到对应尺码")
    else:
        print("关注尺码：未设置（可用 edit --size 指定颜色/尺码）")

    if p.target_price and buy_price:
        gap = buy_price - p.target_price
        if gap > 0:
            print(f"目标价 {money(p.target_price)}：还差 {money(gap)}（需再降 {gap / buy_price:.0%}）")
        else:
            print(f"目标价 {money(p.target_price)}：已达标")

    if buy_price:
        user_rules = bool(args.coupons)
        try:
            rules = _load_rules(args.coupons) if user_rules else demo_rules(p.platform)
        except Exception as e:  # noqa: BLE001
            print(f"券配置读取失败：{e}")
            return 1
        deal = best_price(buy_price, rules)
        detail = _describe_deal(deal) + ("" if user_rules else " · 未计入你的个人券")
        print(f"叠券后估算：{money(deal.final)}（{detail}）")

    if ready and stats:
        print(f"价格位置：历史 {stats.percentile:.0%} 分位 · 30 天内再降概率 {prediction.prob_30d:.0%}")
    else:
        print(f"价格位置：历史样本不足（{len(points)} 次采样），先跑几天 check 再给概率")

    print(f"数据时间：{fmt_ts(max(v.ts for v in variants))}")

    last = store.last_price(did)
    if last and buy_price and abs(last.price - buy_price) >= 1:
        print(f"价差说明：商品级记录 {money(last.price)} 来自已售罄配色，可购最低为 {money(buy_price)}")
    return 0


def _ensure_product(store: Store, key: str) -> tuple[Product | None, bool]:
    """按序号 / platform:pid / URL / 裸货号找商品；找不到则尝试自动添加。"""
    p = resolve_product(store, key)
    if p:
        return p, False
    raw = (key or "").strip()
    try:
        platform, pid = resolve_product_url(raw)
    except MonitorError as e:
        print(f"无法识别商品「{key}」：{e}")
        return None, False
    p = Product(platform=platform, pid=pid,
                url=_normalize_product_url(platform, pid, raw))
    if not store.add_product(p):
        p = store.get_product(p.display_id())
    return p, True


def cmd_test_notify(args, store: Store, cfg: dict) -> int:
    notifier = Notifier(cfg.get("notify"))
    enabled = notifier.enabled_channels()
    if not enabled:
        print("尚未配置任何通知渠道。请在 config.json 的 notify 中至少填写一项，")
        print("例如 serverchan_key / dingtalk_webhook / bark_url / ntfy_topic，详见 README。")
        return 1
    print("已配置渠道：" + "、".join(enabled) + "，正在发送测试消息…")
    notifier.send("Smart Shopper 通知测试",
                  ["如果你收到这条消息，说明通知渠道配置成功。",
                   f"当前共跟踪 {len(store.list_products())} 件商品。"])
    print("完成。若某个渠道没收到，请检查密钥/Webhook 与网络。")
    return 0


def cmd_status(args, store: Store, cfg: dict) -> int:
    """查看监控健康度：连续抓取失败的商品会标红。"""
    prods = store.list_products()
    if not prods:
        print("暂无跟踪商品。")
        return 0
    health = store.all_health()
    summary = store.summary()
    print(f"数据库：{summary['products']} 件商品 · {summary['price_points']} 条价格 · "
          f"{summary['inventory_points']} 条库存 · {summary['variant_points']} 条尺码采样")
    print(f"{'序号':<4} {'商品':<16} {'状态':<12} {'最近成功':<17} 最近错误")
    bad = 0
    for i, p in enumerate(prods, 1):
        h = health.get(p.display_id())
        if not h:
            state, last_ok, err = "未抓取", "-", ""
        elif h.ok:
            state, last_ok, err = "正常", fmt_ts(h.last_ok_ts) if h.last_ok_ts else "-", ""
        else:
            bad += 1
            state = f"连续失败 {h.consecutive_failures} 次"
            last_ok = fmt_ts(h.last_ok_ts) if h.last_ok_ts else "从未成功"
            err = h.last_error[:38]
        print(f"{i:<4} {p.display_id():<16} {state:<12} {last_ok:<17} {err}")
    if bad:
        print(f"\n⚠ {bad} 件商品抓取异常。常见原因：Cookie 失效、链接失效、接口变更、网络限制。")
        print("可用 `python main.py check` 查看详细错误，或 `python main.py test-notify` 检查通知。")
    return 0


def cmd_fit(args, store: Store, cfg: dict) -> int:
    """尺码换算助手：字母码 ↔ 中国号型 ↔ 美/欧/日码，以及鞋码互换。"""
    gender = args.gender
    if args.kind == "shoes":
        if args.table:
            print(f"{'男鞋' if gender == 'men' else '女鞋'}尺码对照：")
            for row in sizing.shoes_table(gender):
                print("  " + sizing.format_shoe_row(row))
            return 0
        if any(v is not None for v in (args.cn, args.eu, args.us, args.uk)):
            match = sizing.convert_shoe(gender, cn=args.cn, eu=args.eu,
                                        us=args.us, uk=args.uk)
            row = match.row
            print(f"鞋码换算（{'男' if gender == 'men' else '女'}）：")
            print(f"  中国 {row['cn']}mm · 欧码 {row['eu']} · 美码 {row['us']:g} · 英码 {row['uk']:g}")
            if match.note:
                print(f"  提示：{match.note}")
            return 0
        print("请提供 --cn/--eu/--us/--uk 之一，或加 --table 查看完整对照表。")
        return 1

    if args.table:
        print(f"{'男装' if gender == 'men' else '女装'}上衣尺码对照：")
        for row in sizing.tops_table(gender):
            print("  " + sizing.format_top_row(row))
        return 0
    if args.alpha:
        target = args.alpha.strip().upper()
        row = next((r for r in sizing.tops_table(gender) if r["alpha"] == target), None)
        if not row:
            print(f"未知字母码 {args.alpha}，可用：S/M/L/XL/XXL")
            return 1
        print(f"{target} 对照：" + sizing.format_top_row(row))
        return 0
    if args.height is not None or args.chest is not None:
        match = sizing.recommend_top(gender, height=args.height, chest=args.chest)
        print(f"推荐尺码：{match.row['alpha']}")
        print("  " + sizing.format_top_row(match.row))
        if match.note:
            print(f"提示：{match.note}")
        return 0
    print("请提供 --alpha 或 --height/--chest，或加 --table 查看完整对照表。")
    return 1


def cmd_export(args, store: Store, cfg: dict) -> int:
    """导出购物清单（可分享给家人，也可作为备份）。"""
    prods = store.list_products()
    if not prods:
        print("暂无跟踪商品可导出。")
        return 1
    data = {
        "version": 1,
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "products": [{
            "platform": p.platform, "pid": p.pid, "url": p.url, "name": p.name,
            "category": p.category, "launch_date": p.launch_date,
            "target_price": p.target_price, "watched_variant": p.watched_variant,
        } for p in prods],
    }
    if args.with_prices:
        data["prices"] = {
            p.display_id(): [[pt.ts, pt.price, pt.original_price]
                             for pt in store.get_prices(p.display_id())]
            for p in prods
        }
    out = Path(args.out or "wishlist.json")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已导出 {len(prods)} 件商品到 {out}"
          + ("（含价格历史）" if args.with_prices else "（不含价格历史，可加 --with-prices）"))
    return 0


def cmd_import(args, store: Store, cfg: dict) -> int:
    """导入购物清单（与本地合并）。"""
    path = Path(args.file)
    if not path.exists():
        print(f"文件不存在：{path}")
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"JSON 解析失败：{e}")
        return 1
    items = data.get("products") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        print("文件里没有商品列表（products）。")
        return 1
    added, updated, skipped = 0, 0, 0
    for it in items:
        try:
            p = Product(
                platform=it["platform"], pid=it["pid"], url=it["url"],
                name=it.get("name") or "", 
                category=season.normalize_category(it.get("category") or "general"),
                launch_date=it.get("launch_date"), target_price=it.get("target_price"),
                watched_variant=it.get("watched_variant") or "",
            )
        except KeyError:
            skipped += 1
            continue
        if store.add_product(p):
            added += 1
        elif args.overwrite:
            store.update_product(p)
            updated += 1
        else:
            skipped += 1
    # 价格历史合并
    price_rows = data.get("prices") if isinstance(data, dict) else None
    restored = 0
    if isinstance(price_rows, dict):
        for did, rows in price_rows.items():
            for row in rows:
                try:
                    ts, price = float(row[0]), float(row[1])
                except (TypeError, ValueError, IndexError):
                    continue
                orig = row[2] if len(row) > 2 and row[2] is not None else None
                store.add_price(did, PricePoint(ts=ts, price=price, original_price=orig))
                restored += 1
    print(f"导入完成：新增 {added}，覆盖 {updated}，跳过 {skipped}"
          + (f"，恢复价格记录 {restored} 条" if restored else ""))
    if skipped and not args.overwrite:
        print("提示：已存在的商品默认跳过；需要覆盖字段请加 --overwrite。")
    return 0


def cmd_serve(args, store: Store, cfg: dict) -> int:
    serve_dashboard(store, host=args.host, port=args.port, cfg=cfg)
    return 0


# ------------------------------------------------------------------ 入口
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="smart-shopper", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="配置文件路径（默认 ./config.json）")
    ap.add_argument("--db", help="SQLite 数据库路径")
    ap.add_argument("--currency", help="展示币种：USD/EUR/JPY/GBP/HKD…（价格仍以 CNY 入库）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="添加跟踪商品")
    p.add_argument("url")
    p.add_argument("--name", help="商品备注名")
    p.add_argument("--category", help="类目：羽绒服/大衣/夏装/鞋 或 down_jacket 等")
    p.add_argument("--launch", help="发售日，如 2026-09-15")
    p.add_argument("--target", type=float, help="目标价：降到该价即提醒")
    p.add_argument("--size", help="关注尺码/规格，如 M / 42（补货时单独提醒）")
    p.add_argument("--platform", choices=list(PLATFORMS) + [PLATFORM_GENERIC],
                   help="强制指定平台；generic 表示用 config.generic.sites 的正则解析")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("edit", help="修改已跟踪商品的名称/类目/目标价/尺码")
    p.add_argument("key", help="序号 / platform:pid / URL")
    p.add_argument("--name")
    p.add_argument("--category")
    p.add_argument("--launch", help="发售日，传空字符串则清空")
    p.add_argument("--target", type=float, help="目标价，传 0 则清空")
    p.add_argument("--size", help="关注尺码，传空字符串则清空")
    p.set_defaults(fn=cmd_edit)

    p = sub.add_parser("list", help="列出跟踪商品")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("rm", help="删除跟踪商品")
    p.add_argument("key", help="序号 / platform:pid / URL")
    p.set_defaults(fn=cmd_rm)

    p = sub.add_parser("check", help="抓取一轮价格与库存，降价/补货即提醒")
    p.add_argument("--watch", action="store_true", help="持续监控")
    p.add_argument("--interval", type=int, help="轮询间隔秒（默认取配置）")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("analyze", help="历史分析 + 降价预测 + 购买建议")
    p.add_argument("key", help="序号 / platform:pid / URL")
    p.set_defaults(fn=cmd_analyze)

    p = sub.add_parser("advise", help="一条命令给出购买建议：买/等/关注 + 可购价 + 叠券")
    p.add_argument("key", help="序号 / platform:pid / URL / 裸货号（如 u0000000066172）")
    p.add_argument("--coupons", help="券配置 JSON（缺省用内置演示券）")
    p.set_defaults(fn=cmd_advise)

    p = sub.add_parser("deal", help="优惠叠加最优解计算")
    p.add_argument("--price", type=float, required=True, help="商品现价")
    p.add_argument("--platform", choices=list(PLATFORMS), default="taobao")
    p.add_argument("--coupons", help="券配置 JSON 文件（缺省用内置演示券）")
    p.set_defaults(fn=cmd_deal)

    p = sub.add_parser("cart", help="购物车跨店凑单优化（JSON 文件）")
    p.add_argument("file", help='格式：{"items":[...],"coupons":[...]}')
    p.set_defaults(fn=cmd_cart)

    p = sub.add_parser("demo", help="离线演示全部分析链路")
    p.set_defaults(fn=cmd_demo)

    p = sub.add_parser("history", help="查看价格历史（可导出 CSV）")
    p.add_argument("key", help="序号 / platform:pid / URL")
    p.add_argument("--days", type=int, help="只看最近 N 天")
    p.add_argument("--limit", type=int, help="明细最多显示条数（默认全部）")
    p.add_argument("--csv", help="导出 CSV 文件路径")
    p.set_defaults(fn=cmd_history)

    p = sub.add_parser("init", help="生成 config.json 配置文件")
    p.add_argument("--force", action="store_true", help="覆盖已有配置")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("seed", help="补历史价格（慢慢买 / CSV），让预测立刻可用")
    p.add_argument("key", help="序号 / platform:pid / URL")
    p.add_argument("--csv", help="从 CSV 导入（表头 datetime,price）")
    p.add_argument("--url", help="覆盖用于查历史的商品链接")
    p.set_defaults(fn=cmd_seed)

    p = sub.add_parser("chart", help="导出价格走势图（svg/html/png）")
    p.add_argument("key", help="序号 / platform:pid / URL")
    p.add_argument("--format", choices=["svg", "html", "png"], default="svg")
    p.add_argument("--out", help="输出文件路径")
    p.set_defaults(fn=cmd_chart)

    p = sub.add_parser("report", help="生成自包含 HTML 分析报告")
    p.add_argument("key", nargs="?", help="只生成指定商品（缺省为全部）")
    p.add_argument("--out", default="report.html", help="输出路径（默认 report.html）")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("bundle", help="多商品组合购买：怎么凑单最划算")
    p.add_argument("--file", help='商品与券清单 JSON：{"items":[],"coupons":[]}')
    p.add_argument("--coupons", help="券配置 JSON（配合已跟踪商品）")
    p.add_argument("--platform", choices=list(PLATFORMS), default="taobao",
                   help="无券配置时使用的内置演示券所属平台")
    p.add_argument("--filler-max", type=float, default=150.0,
                   help="可接受的凑单金额上限（默认 150）")
    p.set_defaults(fn=cmd_bundle)

    p = sub.add_parser("sizes", help="查看/刷新尺码库存")
    p.add_argument("key", help="序号 / platform:pid / URL")
    p.add_argument("--refresh", action="store_true", help="先联网刷新一次")
    p.set_defaults(fn=cmd_sizes)

    p = sub.add_parser("serve", help="启动本地只读 Web 面板")
    p.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    p.add_argument("--port", type=int, default=8777, help="端口（默认 8777）")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("export", help="导出购物清单（备份 / 分享给家人）")
    p.add_argument("--out", default="wishlist.json", help="输出文件（默认 wishlist.json）")
    p.add_argument("--with-prices", action="store_true", help="同时导出价格历史")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("import-list", help="导入购物清单并与本地合并")
    p.add_argument("file", help="export 生成的 JSON 文件")
    p.add_argument("--overwrite", action="store_true", help="覆盖本地已有商品的字段")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("test-notify", help="发送一条测试通知，检查渠道配置")
    p.set_defaults(fn=cmd_test_notify)

    p = sub.add_parser("status", help="查看监控健康度（抓取失败情况）")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("fit", help="尺码换算助手（上衣字母码 / 鞋码对照）")
    p.add_argument("--kind", choices=["top", "shoes"], default="top", help="top=上衣，shoes=鞋")
    p.add_argument("--gender", choices=["men", "women"], default="men")
    p.add_argument("--alpha", help="字母码：S/M/L/XL/XXL")
    p.add_argument("--height", type=float, help="身高 cm（推荐上衣尺码）")
    p.add_argument("--chest", type=float, help="胸围 cm（推荐上衣尺码）")
    p.add_argument("--cn", type=float, help="鞋：中国码 mm")
    p.add_argument("--eu", type=float, help="鞋：欧码")
    p.add_argument("--us", type=float, help="鞋：美码")
    p.add_argument("--uk", type=float, help="鞋：英码")
    p.add_argument("--table", action="store_true", help="打印完整对照表")
    p.set_defaults(fn=cmd_fit)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    if args.db:
        cfg["db"] = args.db
    _configure_currency(cfg, args.currency)
    store = Store(cfg.get("db", "shopper.db"))
    return args.fn(args, store, cfg) or 0


if __name__ == "__main__":
    sys.exit(main())
