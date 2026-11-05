"""京东监控：价格走 p.3.cn 公开接口，库存走 c0.3.cn。

接口说明（社区长期使用的稳定端点）：
- 价格:  https://p.3.cn/prices/mgets?skuIds=J_<sku>
         返回 [{"p": 现价, "m": 移动价, "op": 原价, ...}]
- 库存:  https://c0.3.cn/stock?skuId=<sku>&area=<区域码>&cat=<分类>&venderId=<商家>
         返回 {"stock": {"StockState": 33, "StockStateName": "现货", ...}}
         StockState: 33=现货 34=配货中 36=预订 39=有货 40=无货
"""
from __future__ import annotations

import json
import re
import time

from core.models import InventorySnapshot, PricePoint, VariantSnapshot
from .base import BaseMonitor, MonitorError

STOCK_STATE = {
    "33": ("现货", True),
    "34": ("配货中", True),
    "36": ("预订", True),
    "39": ("有货", True),
    "40": ("无货", False),
}

ITEM_PAGE = "https://item.jd.com/{sku}.html"
MOBILE_PAGE = "https://item.m.jd.com/product/{sku}.html"
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
)
_MOBILE_PRICE_PATTERNS = (
    r'"jdprice_amount"\s*:\s*([0-9.]+)',      # 移动端内嵌 JSON 的真实价（最可靠）
    r'"jdPrice"\s*:\s*"([0-9.]+)"',           # 部分页面不脱敏
    r'"salePrice"\s*:\s*"([0-9.]+)"',
    r'"price"\s*:\s*"([0-9.]+)"',
    r'"p"\s*:\s*"([0-9.]+)"',
)
_COLOR_SIZE_PAT = re.compile(r"colorSize\s*:\s*(\[[^\]]{0,30000}\])", re.I)
_SKU_SIZE_PAT = re.compile(r'"skus"\s*:\s*(\[[^\]]{0,30000}\])', re.I)


class JdMonitor(BaseMonitor):
    platform = "jd"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self._cache_sku = ""
        self._cache_variants: list[VariantSnapshot] = []
        self._mobile_sku = ""
        self._mobile_cache = ""
        self.last_name = ""

    def fetch(self, pid: str) -> tuple[PricePoint | None, InventorySnapshot | None]:
        price = self._fetch_price(pid)
        stock = self._fetch_stock(pid)
        return price, stock

    # ---------- 价格 ----------
    def _fetch_price(self, pid: str) -> PricePoint | None:
        url = f"https://p.3.cn/prices/mgets?skuIds=J_{pid}"
        api_error: Exception | None = None
        try:
            resp = self.http_get(url)
            data = resp.json()
            if isinstance(data, list) and data:
                item = data[0]
                try:
                    p = float(item.get("p") or item.get("jdPrice") or 0)
                except (TypeError, ValueError):
                    p = 0.0
                if p > 0:
                    op = item.get("op") or item.get("m")
                    try:
                        op = float(op) if op else None
                    except (TypeError, ValueError):
                        op = None
                    return PricePoint(ts=self.now(), price=p, original_price=op)
        except Exception as e:  # noqa: BLE001
            api_error = e

        # p.3.cn 在部分网络已 404/被拦，改用移动端商品页内嵌 JSON
        mobile_price = _parse_mobile_price(self._mobile_page(pid))
        if mobile_price:
            return PricePoint(ts=self.now(), price=mobile_price)
        if api_error:
            raise MonitorError(f"京东价格接口请求失败：{api_error}") from api_error
        raise MonitorError("京东价格接口与移动端页面均未解析到价格（可能需要登录或商品已下架）")

    # ---------- 库存 ----------
    def _fetch_stock(self, pid: str) -> InventorySnapshot | None:
        area = self.config.get("area", "1_72_2799_0")  # 默认北京
        cat = self.config.get("cat", "")
        vender = self.config.get("venderId", "")
        url = (
            f"https://c0.3.cn/stock?skuId={pid}&area={area}"
            f"&cat={cat}&venderId={vender}"
            f'&extraParam={{"originid":"list"}}'
        )
        try:
            resp = self.http_get(url)
            data = json.loads(re.sub(r"^[^(]*\(", "", resp.text).rstrip(");"))
            stock = (data or {}).get("stock") or {}
            state_code = str(stock.get("StockState", ""))
            name, in_stock = STOCK_STATE.get(state_code, (stock.get("StockStateName", ""), None))
            if in_stock is not None:
                qty = stock.get("StockQty") or stock.get("stockQty")
                try:
                    qty = (int(qty) if qty not in (None, "", "0") and str(qty).isdigit()
                           else None)
                except Exception:  # noqa: BLE001
                    qty = None
                return InventorySnapshot(ts=self.now(), in_stock=in_stock, state=name, quantity=qty)
        except Exception:  # noqa: BLE001
            pass

        # 库存接口失败时，从移动端商品页内嵌 stock 对象取 StockState
        return _parse_mobile_stock(self._mobile_page(pid))

    # ---------- 尺码 / SKU 库存 ----------
    def variants(self, pid: str, max_skus: int = 12) -> list[VariantSnapshot]:
        """从商品页 colorSize 解析各尺码 sku，再逐个查库存。

        一次最多查 max_skus 个，避免请求过多。
        """
        if self._cache_sku == pid and self._cache_variants:
            return self._cache_variants
        html = self._item_html(pid)
        if not html:
            return []
        skus = _parse_skus(html)
        out: list[VariantSnapshot] = []
        for label, sku in skus[:max_skus]:
            snap = self._fetch_stock(sku)
            if snap is None:
                continue
            out.append(VariantSnapshot(
                ts=snap.ts, variant=label, in_stock=snap.in_stock,
                state=snap.state, quantity=snap.quantity,
            ))
        self._cache_sku, self._cache_variants = pid, out
        return out

    def _mobile_page(self, pid: str) -> str:
        """移动端商品页（桌面页是 SPA 骨架，移动端含价格/库存 JSON）。"""
        if self._mobile_sku == pid:
            return self._mobile_cache
        url = MOBILE_PAGE.format(sku=pid)
        try:
            resp = self.http_get(url, headers={
                "User-Agent": MOBILE_UA,
                "Referer": "https://item.m.jd.com/",
            })
        except Exception:  # noqa: BLE001
            return ""
        if resp.status_code >= 400:
            return ""
        self._mobile_sku, self._mobile_cache = pid, resp.text
        self.last_name = _parse_title(resp.text) or self.last_name
        return resp.text

    def _item_html(self, pid: str) -> str:
        url = ITEM_PAGE.format(sku=pid)
        try:
            resp = self.http_get(url)
            if "验证" in resp.text[:2000] or resp.status_code >= 400:
                return self._maybe_render(url) or ""
            return resp.text
        except Exception:  # noqa: BLE001
            return self._maybe_render(url) or ""


def _parse_skus(html: str) -> list[tuple[str, str]]:
    """解析商品页中的尺码-sku 映射：[(label, skuId)]。"""
    for pat in (_COLOR_SIZE_PAT, _SKU_SIZE_PAT):
        for m in pat.finditer(html):
            raw = m.group(1).replace("'", '"')
            try:
                items = json.loads(raw)
            except json.JSONDecodeError:
                continue
            out: list[tuple[str, str]] = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                sku = it.get("skuId") or it.get("sku_id") or it.get("id")
                label = (it.get("尺码") or it.get("size") or it.get("sizeName")
                         or it.get("skuName") or it.get("name"))
                if sku and label:
                    out.append((str(label), str(sku)))
            if out:
                return out
    return []


def _parse_mobile_price(html: str) -> float | None:
    """从移动端商品页内嵌 JSON 提取真实价；忽略 1??9 这种脱敏价。"""
    if not html:
        return None
    for pat in _MOBILE_PRICE_PATTERNS:
        for m in re.finditer(pat, html):
            try:
                price = float(m.group(1))
            except ValueError:
                continue
            if 0 < price < 1_000_000:
                return price
    return None


def _parse_mobile_stock(html: str) -> InventorySnapshot | None:
    """从移动端商品页的 stock 对象取 StockState（33=现货…40=无货）。"""
    if not html:
        return None
    m = re.search(r'"StockState"\s*:\s*(\d+)', html)
    if not m:
        return None
    name, in_stock = STOCK_STATE.get(m.group(1), ("", None))
    if in_stock is None:
        return None
    return InventorySnapshot(ts=time.time(), in_stock=in_stock, state=name)


def _parse_title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    if not m:
        return ""
    title = re.sub(r"【[^】]*】", "", m.group(1))
    return title.split("-京东")[0].strip()
