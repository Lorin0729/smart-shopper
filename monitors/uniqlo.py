"""优衣库(中国官网 uniqlo.cn)监控。

策略（按顺序尝试）：
1. 配置的自定义接口模板 uniqlo.api_template，{} 会被替换为 productCode；
   期望返回 JSON，字段中包含 price/xxxPrice 与名称。
2. 抓取商品页 HTML，从 JSON-LD(application/ld+json) 或内嵌 JSON 中提取价格。
3. 都失败且已安装 Playwright 时，用真实浏览器渲染后再解析（playwright_fallback）。

尺码库存 variants()：从页面内嵌的 sku 数组中解析各尺码的有货/无货，
用于「我只要 M 码，M 码补货叫我」这类需求。

优衣库前端接口时常变动，若默认抓取失败，可在 config 中配置 api_template，
或参考项目 README 的“接口适配”章节自行补充解析函数。
"""
from __future__ import annotations

import json
import re

from core.models import InventorySnapshot, PricePoint, VariantSnapshot
from .base import BaseMonitor, MonitorError

PRODUCT_PAGE = "https://www.uniqlo.cn/product-detail.html?productCode={code}"

_SOLD_OUT_PAT = re.compile(r"(售罄|已售完|缺货|soldOut[\"']?\s*[:=]\s*true)", re.I)


class UniqloMonitor(BaseMonitor):
    platform = "uniqlo"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.last_name = ""          # 最近一次抓取解析到的商品名
        self._cache_pid = ""
        self._cache_html = ""

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
        html = self._load_page(pid)

        price = _price_from_jsonld(html) or _price_from_embedded(html)
        if not price:
            rendered = self._maybe_render(PRODUCT_PAGE.format(code=pid),
                                          wait_selector=".product-price, .price")
            if rendered:
                html = rendered
                self._cache_pid, self._cache_html = pid, html
                price = _price_from_jsonld(html) or _price_from_embedded(html)
        if not price:
            raise MonitorError(
                "未能从优衣库页面解析出价格。官网接口可能已变更，"
                "请在 config 中为 uniqlo.api_template 配置可用的 JSON 接口模板，"
                "或安装 playwright 启用渲染兜底。"
            )
        name_m = re.search(r"<title>([^<]+)</title>", html)
        name = name_m.group(1).split("-")[0].strip() if name_m else ""
        sold_out = bool(_SOLD_OUT_PAT.search(html))
        stock = InventorySnapshot(
            ts=self.now(), in_stock=not sold_out, state="无货" if sold_out else "有货"
        )
        return PricePoint(ts=self.now(), price=price), stock, name

    def _load_page(self, pid: str) -> str:
        if self._cache_pid == pid and self._cache_html:
            return self._cache_html
        url = PRODUCT_PAGE.format(code=pid)
        try:
            html = self.http_get(url).text
        except Exception as e:  # noqa: BLE001
            raise MonitorError(f"优衣库页面请求失败：{e}") from e
        self._cache_pid, self._cache_html = pid, html
        return html

    # ---------- 尺码库存 ----------
    def variants(self, pid: str) -> list[VariantSnapshot]:
        html = self._load_page(pid)
        out = _variants_from_html(html, self.now())
        if not out:
            rendered = self._maybe_render(PRODUCT_PAGE.format(code=pid))
            if rendered:
                self._cache_pid, self._cache_html = pid, rendered
                out = _variants_from_html(rendered, self.now())
        return out


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


def _variants_from_html(html: str, ts: float) -> list[VariantSnapshot]:
    """从 sku 数组解析尺码库存：{"size":"M","stock":1} 之类。"""
    out: dict[str, VariantSnapshot] = {}
    for m in re.finditer(
        r'"sku(?:List|s|InfoList|StockList)"\s*:\s*(\[[^\]]{0,20000}\])', html, re.I
    ):
        try:
            items = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            name = (it.get("size") or it.get("sizeName") or it.get("skuName")
                    or it.get("sizeCode") or it.get("name"))
            if not name or not isinstance(name, (str, int)):
                continue
            field = it.get("stock", it.get("inventory", it.get("quantity")))
            if field is None:
                continue
            qty = field if isinstance(field, int) else None
            in_stock = (field > 0) if isinstance(field, (int, float)) else \
                str(field).lower() not in ("0", "false", "none", "")
            out[str(name)] = VariantSnapshot(
                ts=ts, variant=str(name), in_stock=in_stock,
                state="有货" if in_stock else "无货", quantity=qty,
            )
    return sorted(out.values(), key=lambda v: v.variant)
