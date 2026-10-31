"""历史价格数据源：第三方平台（慢慢买）+ 本地 CSV 导入。

用于给新添加的商品“冷启动”补历史价格，让预测立刻有数据可用。

慢慢买接口为社区长期使用的非官方端点，返回结构可能调整；
解析失败时抛 HistorySourceError，调用方可回退到 CSV 导入。
"""
from __future__ import annotations

import csv
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests

from core.models import PricePoint

TIMEOUT = 20
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
MMB_ENDPOINT = (
    "https://apapia-history.manmanbuy.com/ChartJson.ashx"
    "?action=GetHistoryData&url={url}"
)


class HistorySourceError(RuntimeError):
    """历史价抓取/解析失败。"""


# ------------------------------------------------------------------ 慢慢买
def fetch_manmanbuy(url: str) -> list[PricePoint]:
    """抓取慢慢买价格历史。

    返回点可能包含“最低价”“均价”等多个序列，这里只取逐日最低价，
    并按 (日期) 去重，保留每天最低值。
    """
    try:
        resp = requests.get(
            MMB_ENDPOINT.format(url=quote(url, safe="")),
            headers={"User-Agent": UA, "Referer": "https://www.manmanbuy.com/"},
            timeout=TIMEOUT,
        )
        payload = resp.json()
    except (requests.RequestException, ValueError) as e:
        raise HistorySourceError(f"慢慢买接口请求失败：{e}") from e

    raw = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as e:
            raise HistorySourceError("慢慢买返回数据无法解析") from e
    if not isinstance(raw, list) or not raw:
        raise HistorySourceError("慢慢买未返回历史数据（商品可能未被收录）")

    points: dict[int, PricePoint] = {}
    for item in raw:
        ts, price = _parse_item(item)
        if ts is None or price is None or price <= 0:
            continue
        key = int(ts // 86400)  # 按天聚合，保留当天最低
        prev = points.get(key)
        if prev is None or price < prev.price:
            points[key] = PricePoint(ts=ts, price=price)
    if not points:
        raise HistorySourceError("慢慢买返回的数据中没有可用价格")
    return sorted(points.values(), key=lambda p: p.ts)


def _parse_item(item) -> tuple[float | None, float | None]:
    """兼容多种结构：[ts, price] / {"date":..,"price":..} / ["2026-01-01", 899]。"""
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return _to_ts(item[0]), _to_float(item[1])
    if isinstance(item, dict):
        ts = _to_ts(item.get("date") or item.get("time") or item.get("x"))
        price = _to_float(item.get("price") or item.get("y") or item.get("minPrice"))
        return ts, price
    return None, None


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_ts(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) / 1000 if v > 1e11 else float(v)  # 毫秒/秒自动识别
    s = str(v).strip()
    if not s:
        return None
    if s.isdigit():
        return _to_ts(int(s))
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------ CSV 导入
def load_csv(path: str | Path) -> list[PricePoint]:
    """从 CSV 导入历史价。

    表头需至少包含价格列；日期列支持 `datetime` / `date` / `time` / `时间`。
    无表头时按 `日期,价格` 两列解析。
    """
    p = Path(path)
    if not p.exists():
        raise HistorySourceError(f"文件不存在：{p}")
    rows = list(csv.DictReader(p.read_text(encoding="utf-8-sig").splitlines()))
    points: list[PricePoint] = []
    if rows and rows[0].keys():
        for r in rows:
            ts = _to_ts(r.get("datetime") or r.get("date") or r.get("time") or r.get("时间"))
            price = _to_float(r.get("price") or r.get("价格") or r.get("现价"))
            if ts is not None and price and price > 0:
                points.append(PricePoint(ts=ts, price=price))
    if not points:  # 无表头模式
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            cells = [c.strip() for c in line.replace("\t", ",").split(",")]
            if len(cells) < 2:
                continue
            ts, price = _to_ts(cells[0]), _to_float(cells[1])
            if ts is not None and price and price > 0:
                points.append(PricePoint(ts=ts, price=price))
    if not points:
        raise HistorySourceError(f"{p} 中没有解析到有效的日期/价格")
    return sorted(points, key=lambda x: x.ts)


def dedupe(points: list[PricePoint]) -> list[PricePoint]:
    """同一时间戳只保留一条。"""
    seen: dict[float, PricePoint] = {}
    for pt in points:
        seen[pt.ts] = pt
    return sorted(seen.values(), key=lambda x: x.ts)


def newest_ts(points: list[PricePoint]) -> float:
    return max((p.ts for p in points), default=time.time())
