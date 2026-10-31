"""优衣库(中国官网 uniqlo.cn)监控。

策略（按顺序尝试）：
1. 配置的自定义接口模板 uniqlo.api_template，{} 会被替换为 productCode；
   期望返回 JSON，字段中包含 price/xxxPrice 与名称。
2. 抓取商品页 HTML，从 JSON-LD(application/ld+json) 或内嵌 JSON 中提取价格。

优衣库前端接口时常变动，若默认抓取失败，可在 config 中配置 api_template，
或参考项目 README 的“接口适配”章节自行补充解析函数。
"""
from __future__ import annotations

import json
import re

from core.models import InventorySnapshot, PricePoint
from .base import BaseMonitor, MonitorError

PRODUCT_PAGE = "https://www.uniqlo.cn/product-detail.html?productCode={code}"

_SOLD_OUT_PAT = re.compile(r"(售罄|已售完|缺货|soldOut[\"']?\s*[:=]\s*true)", re.I)


class UniqloMonitor(BaseMonitor):
    platform = "uniqlo"

    def fetch(self, pid: str) -> tuple[PricePoint | None, InventorySnapshot | None]:
        if self.config.get("api_template"):
            point, stock, name = self._fetch_via_api(pid)
            self.last_name = name
            if point:
                return point, stock
        point, stock, name = self._fetch_via_page(pid)
        self.last_name = name
        return point, stock

    # ---------- 策略 1：自定义 JSON 接口 ----------
    def _fetch_via_api(self, pid: str) -> tuple[PricePoint | None, InventorySnapshot | None, str]:
        url = self.config["api_template"].format(code=pid)
        try:
            resp = self.http_get(url)
            data = resp.json()
        except Exception:  # noqa: BLE001
            return None, None, ""
        node = data if isinstance(data, dict) else {}
        for key in ("data", "result", "product", "response"):
            if isinstance(node.get(key), dict):
                node = node[key]
                break
        price = _first_price(node)
        name = node.get("name") or node.get("productName") or ""
        if not price:
            return None, None, name
        dumped = json.dumps(data, ensure_ascii=False)
        sold_out = bool(_SOLD_OUT_PAT.search(dumped))
        stock = InventorySnapshot(
            ts=self.now(), in_stock=not sold_out, state="无货" if sold_out else "有货"
        )
        return PricePoint(ts=self.now(), price=price), stock, name

    # ---------- 策略 2：商品页解析 ----------
    def _fetch_via_page(self, pid: str) -> tuple[PricePoint | None, InventorySnapshot | None, str]:
        url = PRODUCT_PAGE.format(code=pid)
        try:
            resp = self.http_get(url)
            html = resp.text
        except Exception as e:  # noqa: BLE001
            raise MonitorError(f"优衣库页面请求失败：{e}") from e

        price = _price_from_jsonld(html) or _price_from_embedded(html)
        if not price:
            raise MonitorError(
                "未能从优衣库页面解析出价格。官网接口可能已变更，"
                "请在 config 中为 uniqlo.api_template 配置可用的 JSON 接口模板。"
            )
        name_m = re.search(r"<title>([^<]+)</title>", html)
        name = name_m.group(1).split("-")[0].strip() if name_m else ""
        sold_out = bool(_SOLD_OUT_PAT.search(html))
        stock = InventorySnapshot(
            ts=self.now(), in_stock=not sold_out, state="无货" if sold_out else "有货"
        )
        return PricePoint(ts=self.now(), price=price), stock, name


# ---------------------------------------------------------------- 解析工具
def _first_price(node: dict) -> float | None:
    """在任意层级的 dict/list 中找第一个像价格的字段。"""
    if isinstance(node, dict):
        for key in ("price", "minPrice", "salePrice", "activityPrice", "sellPrice"):
            v = node.get(key)
            p = _to_price(v)
            if p:
                return p
        for v in node.values():
            p = _first_price(v) if isinstance(v, (dict, list)) else None
            if p:
                return p
    elif isinstance(node, list):
        for v in node:
            p = _first_price(v) if isinstance(v, (dict, list)) else None
            if p:
                return p
    return None


def _to_price(v) -> float | None:
    if isinstance(v, dict):
        v = v.get("value") or v.get("amount")
    try:
        p = float(v)
        return p if p > 0 else None
    except (TypeError, ValueError):
        return None


def _price_from_jsonld(html: str) -> float | None:
    for m in re.finditer(
        r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S
    ):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            p = _to_price(offers.get("price"))
            if p:
                return p
    return None


def _price_from_embedded(html: str) -> float | None:
    """从内嵌 JSON(window.__INITIAL_STATE__ 等)中提取价格。"""
    for key in ("minPrice", "salePrice", "activityPrice", '"price"'):
        m = re.search(key + r"['\"]?\s*[:=]\s*['\"]?([0-9]+(?:\.[0-9]+)?)", html)
        if m:
            p = float(m.group(1))
            if 1 <= p <= 1_000_000:
                return p
    return None
