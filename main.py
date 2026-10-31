#!/usr/bin/env python3
"""Smart Shopper —— 降价监控 · 优惠叠加优化 · 购买时机预测。

用法示例：
  python main.py add "https://item.jd.com/100012043978.html" --category 羽绒服 --launch 2026-09-15
  python main.py check
  python main.py analyze 1
  python main.py deal --price 899 --platform taobao --demo
  python main.py demo
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

from core.models import InventorySnapshot, PLATFORMS, Product
from core.storage import Store
from monitors import MonitorError, get_monitor, parse_product_url
from notifier import Notifier
from optimizer.deals import CartItem, DiscountRule, best_deal, demo_rules, optimize_cart
from predictor import season
from predictor.forecast import (PriceHistory, predict_drop, recommend,
                                upcoming_promos)

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
    p = Path(path or ROOT / "config.json")
    cfg = dict(DEFAULT_CONFIG)
    if p.exists():
        cfg.update(json.loads(p.read_text(encoding="utf-8")))
    return cfg


def resolve_product(store: Store, key: str) -> Product | None:
    if key.isdigit():
        prods = store.list_products()
        idx = int(key) - 1
        if 0 <= idx < len(prods):
            return prods[idx]
    return store.get_product(key)


def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------------ 子命令
def cmd_add(args, store: Store, cfg: dict) -> int:
    platform, pid = parse_product_url(args.url)
    p = Product(platform=platform, pid=pid, url=args.url,
                name=args.name or "", category=season.normalize_category(args.category or "general"),
                launch_date=args.launch)
    if not store.add_product(p):
        print(f"已存在：{p.display_id()}（如需修改名称/类目请先 rm 再 add）")
        return 1
    print(f"已添加跟踪：{p.display_id()}  {p.url}")
    print("提示：执行 `python main.py check` 立即抓取一次价格与库存。")
    return 0


def cmd_list(args, store: Store, cfg: dict) -> int:
    prods = store.list_products()
    if not prods:
        print("暂无跟踪商品，用 add 命令添加。")
        return 0
    print(f"{'序号':<4} {'商品':<6} {'类目':<10} {'当前价':>9} {'历史最低':>9} {'库存':<6} 名称")
    for i, p in enumerate(prods, 1):
        last = store.last_price(p.display_id())
        low = store.lowest_price(p.display_id())
        inv = store.last_inventory(p.display_id())
        cur = f"¥{last.price:g}" if last else "-"
        lo = f"¥{low.price:g}" if low else "-"
        stock = (inv.state or ("有货" if inv.in_stock else "无货")) if inv else "-"
        print(f"{i:<4} {p.display_id():<14} {p.category:<10} {cur:>9} {lo:>9} {stock:<6} {p.name}")
    return 0


def cmd_rm(args, store: Store, cfg: dict) -> int:
    ok = store.remove_product(args.key)
    print("已删除" if ok else "未找到该商品")
    return 0 if ok else 1


def check_cycle(store: Store, cfg: dict, notifier: Notifier) -> None:
    """抓取所有跟踪商品一轮，记录价格/库存并发出降价、补货提醒。"""
    prods = store.list_products()
    if not prods:
        print("暂无跟踪商品。")
        return
    alerts: list[str] = []
    for p in prods:
        try:
            monitor = get_monitor(p.platform, cfg)
            point, inv = monitor.fetch(p.pid)
        except MonitorError as e:
            print(f"[失败] {p.display_id()}: {e}")
            continue
        if not point and not inv:
            print(f"[失败] {p.display_id()}: 未取到任何数据")
            continue
        did = p.display_id()
        prev = store.last_price(did)
        prev_inv = store.last_inventory(did)
        if point:
            store.add_price(did, point)
            line = f"[记录] {did} ¥{prev.price:g} → ¥{point.price:g}" if prev else f"[记录] {did} ¥{point.price:g}"
            print(line)
            if prev and point.price < prev.price - 0.01:
                pct = (prev.price - point.price) / prev.price * 100
                alerts.append(f"[降价] {p.name or did}: ¥{prev.price:g} → ¥{point.price:g}（-{pct:.1f}%）")
                low = store.lowest_price(did)
                if low and point.price <= low.price + 0.01:
                    alerts.append(f"[新低] {p.name or did} 刷新历史最低 ¥{point.price:g}")
            if monitor.last_name and not p.name:
                p.name = monitor.last_name
                store.update_product(p)
        if inv:
            store.add_inventory(did, inv)
            if prev_inv and not prev_inv.in_stock and inv.in_stock:
                alerts.append(f"[补货] {p.name or did} 已补货（{inv.state}）")
            if prev_inv and prev_inv.in_stock and not inv.in_stock:
                print(f"[提示] {did} 变为无货")
    if alerts:
        notifier.send("Smart Shopper 价格提醒", alerts)


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
    inv = store.last_inventory(p.display_id())
    rec = recommend(hist, inv, prediction, windows, today)

    print("=" * 62)
    print(f"商品分析：{p.name or p.display_id()}   [{p.platform}]")
    print("=" * 62)
    if stats:
        print(f"当前价 ¥{stats.current:g} | 最低 ¥{stats.low:g} | 最高 ¥{stats.high:g} "
              f"| 均价 ¥{stats.avg:g} | 采样 {stats.count} 次")
        print(f"价格分位 {stats.percentile:.0%} | 有效降价 {stats.drop_count} 次"
              + (f" | 平均间隔 {stats.avg_drop_interval_days:g} 天 | 平均降幅 {stats.avg_drop_depth:.0%}"
                 if stats.avg_drop_interval_days and stats.avg_drop_depth else ""))
    else:
        print("价格历史不足 2 个采样点，先执行 check 积累数据。以下基于类目模型给出建议。")
    if inv:
        print(f"库存：{'有货' if inv.in_stock else '无货'} {inv.state}"
              + (f"（剩 {inv.quantity} 件）" if inv.quantity is not None else ""))

    print("-" * 62)
    print(f"◆ 降价预测（未来 30 天）：概率 {prediction.prob_30d:.0%}")
    if prediction.likely_start:
        print(f"  预计窗口：{prediction.likely_start.strftime('%m-%d')} ~ {prediction.likely_end.strftime('%m-%d')}")
    print(f"  依据：{prediction.reason}")

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
    return [DiscountRule.from_dict(d) for d in data]


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
    return 0


# ------------------------------------------------------------------ 入口
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="smart-shopper", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="配置文件路径（默认 ./config.json）")
    ap.add_argument("--db", help="SQLite 数据库路径")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="添加跟踪商品")
    p.add_argument("url")
    p.add_argument("--name", help="商品备注名")
    p.add_argument("--category", help="类目：羽绒服/大衣/夏装/鞋 或 down_jacket 等")
    p.add_argument("--launch", help="发售日，如 2026-09-15")
    p.set_defaults(fn=cmd_add)

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
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    if args.db:
        cfg["db"] = args.db
    store = Store(cfg.get("db", "shopper.db"))
    return args.fn(args, store, cfg) or 0


if __name__ == "__main__":
    sys.exit(main())
