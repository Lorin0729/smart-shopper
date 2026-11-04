"""优衣库解析层：链接识别与数据解析，不发起任何网络请求。

分成独立模块的目的：
- 网络访问集中在 monitors/uniqlo.py，解析逻辑可以用本地 fixture 单测；
- 官网结构经常调整，新增一种 JSON 结构时只需要在这里加分支。
"""
from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterator
from urllib.parse import unquote, urlparse

from core.models import VariantSnapshot

from .base import MonitorError

# 优衣库中国站商品编号：u + 13 位数字（如 u0000000066172）
CODE_RE = re.compile(r"[uU]\d{13}")
UNIQLO_HOST_RE = re.compile(
    r"(?:^|\.)(?:uniqlo\.cn|uniqlo\.com|u\.uniqlo\.cn|h\.uniqlo\.cn|d\.uniqlo\.cn)", re.I
)
# productCode / product_code / pid / productId 等参数（query、hash、JSON 片段都能命中）
_CODE_PARAM_RE = re.compile(
    r"(?:productCode|product_code|productcode|pid|productId)\s*[=:]\s*['\"]?([A-Za-z0-9_-]{6,})",
    re.I,
)

_SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "3XL", "4XL", "5XL"]
_SIZE_ALIASES = {"XXXL": "3XL", "XXXXL": "4XL", "XXXXXL": "5XL"}
_SOLD_OUT_PAT = re.compile(r"(售罄|已售完|缺货|soldOut[\"']?\s*[:=]\s*true)", re.I)

# 解析后 SKU 的库存来源，未知时不要伪造「有货」
_STATE_IN = "有货"
_STATE_OUT = "无货"


# ---------------------------------------------------------------- 链接识别
def is_uniqlo_link(text: str) -> bool:
    low = (text or "").lower()
    return "uniqlo" in low and UNIQLO_HOST_RE.search(urlparse(low).netloc or low) is not None


def parse_product_code(text: str) -> str:
    """从商品编号 / 各种商品链接中提取 productCode。

    支持：
    - u0000000066172（纯编号）
    - https://www.uniqlo.cn/product-detail.html?productCode=u0000000066172
    - https://www.uniqlo.cn/product-detail.html?utm_source=x&productCode=u0000000066172#detail
    - https://h.uniqlo.cn/?type=pc#/product?pid=u0000000066172
    - https://www.uniqlo.cn/xxx/u0000000066172.html（路径里带编号）
    - 百分号编码后的链接
    """
    raw = html_lib.unescape(unquote((text or "").strip()))
    if not raw:
        raise MonitorError("优衣库链接为空：请传入商品链接或 productCode（形如 u0000000066172）")

    m = CODE_RE.search(raw)
    if m:
        return m.group(0).lower()

    m = _CODE_PARAM_RE.search(raw)
    if m:
        value = m.group(1).strip()
        if value:
            return value.lower()

    raise MonitorError(
        "无法从链接识别优衣库商品编号。"
        "支持的形式：商品页链接（?productCode=u0000000066172）、移动端/分享链接（#/product?pid=...）、"
        "路径中带 u0000000066172 的链接，或直接填 u0000000066172。"
        "若使用的是 u.uniqlo.cn 短链，请先在浏览器打开并复制跳转后的完整地址。"
    )


# ---------------------------------------------------------------- 数据结构
@dataclass
class UniqloSku:
    """一个颜色 × 尺码的 SKU。"""

    sku_id: str = ""
    color: str = ""
    color_no: str = ""
    size: str = ""
    price: float | None = None
    original_price: float | None = None
    member_price: float | None = None
    in_stock: bool | None = None
    quantity: int | None = None
    limit: int | None = None
    enabled: bool = True

    @property
    def label(self) -> str:
        if self.color and self.size:
            return f"{self.color} / {self.size}"
        return self.size or self.color or self.sku_id

    @property
    def discount(self) -> float | None:
        if self.price and self.original_price and self.original_price > self.price:
            return 1 - self.price / self.original_price
        return None

    def snapshot(self, ts: float) -> VariantSnapshot:
        return VariantSnapshot(
            ts=ts,
            variant=self.label,
            in_stock=bool(self.in_stock),
            state=_STATE_IN if self.in_stock else _STATE_OUT,
            quantity=self.quantity,
            color=self.color,
            size=self.size,
            sku_id=self.sku_id,
            price=self.price,
            original_price=self.original_price,
            member_price=self.member_price,
            limit=self.limit,
        )


@dataclass
class UniqloProduct:
    """一次解析结果（价格 + 尺码矩阵）。"""

    code: str = ""
    name: str = ""
    price: float | None = None
    original_price: float | None = None
    in_stock: bool | None = None
    skus: list[UniqloSku] = field(default_factory=list)
    source: str = ""
    limited: bool = False
    note: str = ""

    def finalize(self) -> "UniqloProduct":
        """按 SKU 库存与价格修正商品级字段。"""
        prices = [s.price for s in self.skus if s.price]
        if prices:
            # 商品级价格取「所有 SKU 的最低现价」，与商品页展示口径一致；
            # 某个便宜尺码售罄时不会把整件商品的价格历史推高。
            self.price = min(prices)
        originals = [s.original_price for s in self.skus if s.original_price]
        if originals:
            self.original_price = max(originals)
        if self.original_price and self.price and self.original_price <= self.price:
            self.original_price = None
        if self.skus:
            states = [s.in_stock for s in self.skus if s.in_stock is not None]
            if states:
                self.in_stock = any(states)
        self.skus.sort(key=lambda s: (_color_key(s.color_no, s.color), _size_key(s.size)))
        return self

    @property
    def stock_state(self) -> str:
        if self.in_stock is None:
            return "库存未知"
        return _STATE_IN if self.in_stock else _STATE_OUT

    @property
    def total_quantity(self) -> int | None:
        qtys = [s.quantity for s in self.skus if s.quantity is not None]
        return sum(qtys) if qtys else None

    def snapshots(self, ts: float) -> list[VariantSnapshot]:
        """只返回库存状态已知的 SKU，避免把「未知」写成「有货」。"""
        return [s.snapshot(ts) for s in self.skus if s.in_stock is not None]


@dataclass
class StockInfo:
    mapping: dict[str, int] = field(default_factory=dict)
    has_stock: bool | None = None

    def apply(self, product: UniqloProduct) -> None:
        if self.mapping:
            for sku in product.skus:
                if not sku.sku_id:
                    continue
                qty = self.mapping.get(sku.sku_id)
                if qty is None:
                    sku.in_stock = False
                    sku.quantity = None
                else:
                    sku.in_stock = qty > 0
                    sku.quantity = qty
            if product.skus:
                product.in_stock = any(s.in_stock for s in product.skus)
        elif self.has_stock is not None:
            product.in_stock = self.has_stock
        product.finalize()


# ---------------------------------------------------------------- 解析入口
def parse_spu_json(data: Any, code: str = "", source: str = "") -> UniqloProduct | None:
    """解析静态商品 JSON / 详情接口 JSON（summary + rows/skus）。"""
    node = _unwrap(data)
    if not isinstance(node, dict):
        return None
    summary = node.get("summary") if isinstance(node.get("summary"), dict) else {}
    if not summary and isinstance(node.get("skuInfo"), dict):
        summary = node["skuInfo"]
    rows = node.get("rows") or node.get("skus") or node.get("skuList") or node.get("skuInfoList") or []
    if isinstance(rows, dict):
        rows = [dict(v, **{"__key": k}) if isinstance(v, dict) else v for k, v in rows.items()]

    name = str(
        summary.get("name") or summary.get("productName") or node.get("name")
        or node.get("productName") or ""
    ).strip()
    product_code = str(
        summary.get("productCode") or node.get("productCode") or code or ""
    ).strip().lower()
    original = _to_float(
        summary.get("originPrice") or summary.get("originalPrice")
        or node.get("originPrice") or node.get("originalPrice")
    )
    limited = _to_bool(summary.get("epLimitFlag")) or _to_bool(summary.get("limitFlag")) or False

    product = UniqloProduct(
        code=product_code, name=name, original_price=original,
        source=source, limited=limited,
    )

    row_prices: list[float] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        sku = _sku_from_row(row, original)
        if not sku:
            continue
        if sku.price:
            row_prices.append(sku.price)
        product.skus.append(sku)

    coarse_stock = summary.get("hasStock", summary.get("stock"))
    if coarse_stock is not None and not isinstance(coarse_stock, (int, float)):
        product.in_stock = _to_bool(coarse_stock)
    elif isinstance(coarse_stock, (int, float)):
        product.in_stock = coarse_stock > 0

    summary_price = _to_float(summary.get("minPrice") or summary.get("minVaryPrice"))
    prices = [s.price for s in product.skus if s.price]
    product.price = min(prices) if prices else (min(row_prices) if row_prices else summary_price)
    product.finalize()

    if not product.name:
        product.note = "未解析到商品名称"
    if product.price is None and not product.skus:
        return None
    return product


def parse_prodinfo_json(data: Any, code: str = "") -> UniqloProduct | None:
    """解析 /data/products/prodInfo/{locale}/{code}.json（只有商品级信息）。"""
    if not isinstance(data, dict):
        return None
    price = _to_float(data.get("minPrice") or data.get("minVaryPrice") or data.get("originPrice"))
    if price is None:
        return None
    original = _to_float(data.get("originPrice"))
    return UniqloProduct(
        code=str(data.get("productCode") or data.get("code") or code or "").strip().lower(),
        name=str(data.get("name") or "").strip(),
        price=price,
        original_price=original if original and original > price else None,
        in_stock=_to_bool(data.get("hasStock", data.get("stock"))),
        source="商品摘要 JSON",
    ).finalize()


def parse_stock_json(data: Any) -> StockInfo | None:
    """解析 POST /stock/stock/query/{locale}（type=DETAIL）的库存映射。"""
    node = _unwrap(data)
    if not isinstance(node, dict):
        return None
    mapping: dict[str, int] = {}
    for key in ("skuStocks", "expressSkuStocks", "pickupSkuStocks", "bplStocks", "dcSkuStocks"):
        value = node.get(key)
        if isinstance(value, dict) and value:
            mapping = {str(k): int(_to_float(v) or 0) for k, v in value.items()}
            break
    has_stock = _to_bool(node.get("hasStock"))
    if not mapping and has_stock is None:
        return None
    return StockInfo(mapping=mapping, has_stock=has_stock)


def parse_page_html(html: str, code: str = "") -> UniqloProduct | None:
    """解析商品页 HTML：JSON-LD → 内嵌 JSON → 可见价格文本。"""
    if not html:
        return None
    product = parse_jsonld(html, code)
    if product and product.price:
        return product
    product = parse_embedded_json(html, code)
    if product and product.price:
        return product
    return parse_visible_price(html, code)


def parse_jsonld(html: str, code: str = "") -> UniqloProduct | None:
    for m in re.finditer(
        r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I
    ):
        try:
            data = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        node = _find_offer_node(data)
        if not node:
            continue
        price = _to_float(node.get("price") or node.get("lowPrice"))
        if not price:
            continue
        availability = str(node.get("availability") or (node.get("offers") or {}).get("availability") or "")
        in_stock = None
        if availability:
            in_stock = "OutOfStock" not in availability and "SoldOut" not in availability
        name = _clean_name(str(node.get("name") or ""))
        return UniqloProduct(
            code=code, name=name, price=price, in_stock=in_stock, source="页面 JSON-LD"
        ).finalize()
    return None


def parse_embedded_json(html: str, code: str = "") -> UniqloProduct | None:
    for obj in _iter_embedded_objects(html):
        if not isinstance(obj, (dict, list)):
            continue
        found = _find_product_object(obj)
        if not found:
            continue
        product = parse_spu_json(found, code=code, source="页面内嵌 JSON")
        if product and (product.price or product.skus):
            return product
    return None


def parse_visible_price(html: str, code: str = "") -> UniqloProduct | None:
    patterns = [
        r'itemprop\s*=\s*["\']price["\'][^>]*content\s*=\s*["\']([0-9]+(?:\.[0-9]+)?)',
        r'data-price\s*=\s*["\']([0-9]+(?:\.[0-9]+)?)',
        r"(?:¥|￥|CNY|RMB)\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
    ]
    price = None
    for pat in patterns:
        m = re.search(pat, html, re.I)
        if m:
            price = _to_float(m.group(1))
            if price:
                break
    if not price:
        return None
    name = _title_name(html)
    sold_out = bool(_SOLD_OUT_PAT.search(html))
    return UniqloProduct(
        code=code, name=name, price=price, in_stock=not sold_out, source="页面可见文本"
    ).finalize()


def parse_generic_json(data: Any, code: str = "") -> UniqloProduct | None:
    """用于用户自定义 api_template：尽量从任意 JSON 中找价格与名称。"""
    price = _first_price(data)
    if not price:
        return None
    return UniqloProduct(
        code=code,
        name=_first_str(data, ("name", "productName", "title")),
        price=price,
        source="自定义接口",
    ).finalize()


# ---------------------------------------------------------------- 内部工具
def _sku_from_row(row: dict, summary_original: float | None) -> UniqloSku | None:
    size = str(row.get("sizeText") or row.get("sizeName") or row.get("size") or "").strip()
    color = str(row.get("style") or row.get("colorName") or row.get("color") or "").strip()
    if not color:
        style_text = str(row.get("styleText") or "")
        color = style_text.split("/")[-1].strip() if "/" in style_text else ""
    color_no = str(row.get("colorNo") or row.get("colorCode") or "").strip()
    sku_id = str(
        row.get("productId") or row.get("skuId") or row.get("skuCode")
        or row.get("omsSkuCode") or row.get("__key") or row.get("id") or ""
    ).strip()
    if not (size or color or sku_id):
        return None

    price = _to_float(
        row.get("varyPrice") or row.get("price") or row.get("salePrice")
        or row.get("activityPrice") or row.get("currentPrice") or row.get("priceValue")
    )
    original = _to_float(
        row.get("originPrice") or row.get("originalPrice") or row.get("listPrice")
    ) or summary_original
    member = _to_float(row.get("memberPrice") or row.get("vipPrice") or row.get("concessionalPrice"))
    limit = _to_int(row.get("purchaseLimit") or row.get("limitNum") or row.get("maxQuantity") or row.get("limit"))
    enabled = _to_bool(row.get("enabledFlag")) is not False

    stock_raw = row.get("stock")
    if stock_raw is None:
        stock_raw = row.get("stockNum", row.get("quantity", row.get("inventory")))
    in_stock: bool | None = None
    quantity: int | None = None
    if isinstance(stock_raw, bool):
        in_stock = stock_raw
    elif isinstance(stock_raw, (int, float)):
        quantity = int(stock_raw)
        in_stock = quantity > 0
    elif isinstance(stock_raw, str) and stock_raw.strip():
        if stock_raw.strip().upper() in ("Y", "N"):
            in_stock = stock_raw.strip().upper() == "Y"
        else:
            q = _to_int(stock_raw)
            if q is not None:
                quantity, in_stock = q, q > 0

    return UniqloSku(
        sku_id=sku_id, color=color, color_no=color_no, size=size,
        price=price, original_price=original if original and price and original > price else None,
        member_price=member, in_stock=in_stock, quantity=quantity,
        limit=limit, enabled=enabled,
    )


def _unwrap(data: Any) -> Any:
    node = data
    for _ in range(5):
        if isinstance(node, list):
            if not node:
                return node
            node = node[0]
            continue
        if isinstance(node, dict):
            if any(k in node for k in ("summary", "rows", "skus", "skuList")):
                return node
            for key in ("resp", "result", "data", "product", "productInfo"):
                value = node.get(key)
                if isinstance(value, (dict, list)) and value:
                    node = value
                    break
            else:
                return node
            continue
        return node
    return node


def _iter_embedded_objects(html: str) -> Iterator[Any]:
    for m in re.finditer(
        r'<script[^>]*type\s*=\s*["\']application/(?:ld\+)?json["\'][^>]*>(.*?)</script>',
        html, re.S | re.I,
    ):
        text = m.group(1).strip()
        try:
            yield json.loads(text)
        except json.JSONDecodeError:
            continue

    assign_re = re.compile(
        r"(?:window\.)?[\w$.]*(?:INITIAL_STATE|NEXT_DATA|NUXT|PRELOADED_STATE|APP_STATE)[\w$.]*\s*=\s*",
        re.I,
    )
    decoder = json.JSONDecoder()
    for m in assign_re.finditer(html):
        chunk = html[m.end():m.end() + 500_000].lstrip()
        try:
            obj, _ = decoder.raw_decode(chunk)
        except json.JSONDecodeError:
            continue
        yield obj


def _find_product_object(obj: Any, depth: int = 0) -> dict | None:
    if depth > 8:
        return None
    if isinstance(obj, dict):
        has_summary = isinstance(obj.get("summary"), dict)
        has_rows = any(k in obj for k in ("rows", "skus", "skuList", "skuInfoList"))
        if has_summary and has_rows:
            return obj
        if isinstance(obj.get("skuInfo"), dict) and ("rows" in obj or "skus" in obj):
            return obj
        for key in ("resp", "result", "data", "product", "props", "pageProps", "state"):
            if key in obj:
                found = _find_product_object(obj[key], depth + 1)
                if found:
                    return found
        for value in obj.values():
            if isinstance(value, (dict, list)):
                found = _find_product_object(value, depth + 1)
                if found:
                    return found
    elif isinstance(obj, list):
        for value in obj[:50]:
            if isinstance(value, (dict, list)):
                found = _find_product_object(value, depth + 1)
                if found:
                    return found
    return None


def _find_offer_node(data: Any) -> dict | None:
    if isinstance(data, dict):
        offers = data.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if isinstance(offers, dict) and offers.get("price") is not None:
            merged = dict(offers)
            merged.setdefault("name", data.get("name"))
            return merged
        for value in data.values():
            found = _find_offer_node(value)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_offer_node(value)
            if found:
                return found
    return None


def _first_price(node: Any) -> float | None:
    if isinstance(node, dict):
        for key in ("salePrice", "activityPrice", "price", "minPrice", "sellPrice"):
            price = _to_float(node.get(key))
            if price:
                return price
        for value in node.values():
            if isinstance(value, (dict, list)):
                price = _first_price(value)
                if price:
                    return price
    elif isinstance(node, list):
        for value in node:
            if isinstance(value, (dict, list)):
                price = _first_price(value)
                if price:
                    return price
    return None


def _first_str(node: Any, keys: tuple[str, ...]) -> str:
    if isinstance(node, dict):
        for key in keys:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in node.values():
            if isinstance(value, (dict, list)):
                found = _first_str(value, keys)
                if found:
                    return found
    elif isinstance(node, list):
        for value in node:
            if isinstance(value, (dict, list)):
                found = _first_str(value, keys)
                if found:
                    return found
    return ""


def _to_float(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value") or value.get("amount") or value.get("price")
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        price = float(value)
        return price if 0 < price <= 1_000_000 else None
    text = str(value).strip().replace(",", "").replace("¥", "").replace("￥", "").replace("$", "")
    try:
        price = float(text)
        return price if 0 < price <= 1_000_000 else None
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    text = str(value).strip().upper()
    if text in ("Y", "YES", "TRUE", "1", "有", "有货", "IN_STOCK"):
        return True
    if text in ("N", "NO", "FALSE", "0", "无", "无货", "OUT_OF_STOCK"):
        return False
    return None


def size_sort_key(size: str) -> tuple:
    """尺码排序键：字母码按 XS→5XL，数字码按数值，其余按字符串。"""
    return _size_key(size)


def _size_key(size: str) -> tuple:
    value = (size or "").strip().upper()
    value = _SIZE_ALIASES.get(value, value)
    if value in _SIZE_ORDER:
        return (0, _SIZE_ORDER.index(value))
    if value.isdigit():
        return (1, int(value))
    return (2, value)


def _color_key(color_no: str, color: str) -> str:
    return (color_no or color or "").lower()


def _clean_name(name: str) -> str:
    text = re.sub(r"\s+", " ", name or "").strip()
    for sep in (" - ", " | ", "｜"):
        if sep in text:
            text = text.split(sep)[0].strip()
    if text in ("产品详情", "商品详情", "优衣库"):
        return ""
    return text


def _title_name(html: str) -> str:
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
    return _clean_name(m.group(1)) if m else ""
