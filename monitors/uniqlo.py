"""优衣库中国站监控器。

数据链路（按优先级逐级降级，任何一层失败都会记录原因并继续下一层）：
1. 用户配置的 api_template / api_templates
2. 官方静态商品 JSON（含完整颜色 × 尺码矩阵与价格）
3. 官方商品详情接口 JSON（价格兜底）
4. 官方商品摘要 JSON
5. 商品页 HTML（JSON-LD / 内嵌 JSON / 可见价格文本）
6. 移动端页面
7. Playwright 渲染兜底

库存单独走 `POST /stock/stock/query/{locale}`（type=DETAIL），一次请求拿到
所有 SKU 的库存映射。网络访问与解析分离：解析逻辑在 uniqlo_parse.py。
"""
from __future__ import annotations

import json
import time
from typing import Callable

import requests

from core.models import InventorySnapshot, PricePoint

from .base import DEFAULT_UA, BaseMonitor, MonitorError
from .uniqlo_parse import (CODE_RE, UniqloProduct, StockInfo, parse_generic_json,
                           parse_page_html, parse_product_code, parse_prodinfo_json,
                           parse_spu_json, parse_stock_json)

PRODUCT_PAGE = "https://www.uniqlo.cn/product-detail.html?productCode={code}"
MOBILE_PAGE = "https://h.uniqlo.cn/?type=pc#/product?pid={code}"
SPU_JSON = "https://www.uniqlo.cn/data/products/spu/zh_CN/{code}.json"
SPU_API = "https://d.uniqlo.cn/p/product/i/product/spu/pc/query/{code}/zh_CN"
PRODINFO_JSON = "https://www.uniqlo.cn/data/products/prodInfo/zh_CN/{code}.json"
STOCK_API = "https://d.uniqlo.cn/p/stock/stock/query/zh_CN"

# transport: (method, url, body) -> (status, text, headers)
Transport = Callable[[str, str, dict | None], tuple[int, str, dict]]


class UniqloHTTPError(MonitorError):
    """带分类的 HTTP/网络错误，用于决定是否重试以及给出什么建议。"""

    def __init__(self, message: str, kind: str = "http", status: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retry_after = retry_after


class UniqloMonitor(BaseMonitor):
    platform = "uniqlo"

    DEFAULTS = {
        "api_template": "",
        "api_templates": [],
        "use_builtin_apis": True,
        "stock_api": True,
        "mobile_page": True,
        "cache_ttl_seconds": 300,
        "request_timeout": 15,
        "retry_times": 1,
        "retry_backoff_seconds": 1.5,
        "cookie": "",
    }

    def __init__(self, config: dict | None = None, transport: Transport | None = None):
        super().__init__(config or {})
        self.timeout = float(self.config.get("request_timeout", self.DEFAULTS["request_timeout"]))
        self.retry_times = int(self.config.get("retry_times", self.DEFAULTS["retry_times"]))
        self.retry_backoff = float(
            self.config.get("retry_backoff_seconds", self.DEFAULTS["retry_backoff_seconds"])
        )
        self.cache_ttl = float(
            self.config.get("cache_ttl_seconds", self.DEFAULTS["cache_ttl_seconds"])
        )
        self._transport = transport
        self._cache: dict[str, tuple[float, UniqloProduct]] = {}
        self.last_name = ""
        self.last_source = ""
        self.last_attempts: list[tuple[str, str, str]] = []
        self.cache_hit = False

    # -------------------------------------------------- 对外接口
    def fetch(self, pid: str) -> tuple:
        product = self._resolve(pid)
        self.last_name = product.name
        if not product.price:
            raise MonitorError(f"优衣库商品 {pid} 未解析到价格（来源：{product.source}）")
        point = PricePoint(
            ts=self.now(),
            price=round(product.price, 2),
            original_price=round(product.original_price, 2) if product.original_price else None,
        )
        inventory = None
        if product.in_stock is not None:
            inventory = InventorySnapshot(
                ts=self.now(),
                in_stock=bool(product.in_stock),
                state=product.stock_state,
                quantity=product.total_quantity,
            )
        return point, inventory

    def variants(self, pid: str) -> list:
        product = self._resolve(pid)
        return product.snapshots(self.now())

    # -------------------------------------------------- 候选链路
    def _resolve(self, pid: str) -> UniqloProduct:
        cached = self._cache_get(pid)
        if cached is not None:
            self.cache_hit = True
            self.last_source = cached.source
            self.last_name = cached.name
            self.last_attempts = [("缓存", f"{self.cache_ttl:g}s 内命中，未重复请求", "cache")]
            return cached

        self.cache_hit = False
        attempts: list[tuple[str, str, str]] = []
        product: UniqloProduct | None = None

        for name, loader in self._candidates(pid):
            try:
                product = loader(pid)
            except UniqloHTTPError as exc:
                attempts.append((name, str(exc), exc.kind))
                continue
            except Exception as exc:  # noqa: BLE001 - 单个候选失败不应中断整条链路
                attempts.append((name, f"{type(exc).__name__}: {exc}", "parse"))
                continue

            if product and (product.price or product.skus):
                attempts.append(
                    (name, f"成功（价格 {product.price}，SKU {len(product.skus)} 个）", "ok")
                )
                product.source = name
                break
            attempts.append((name, "返回内容可读，但未找到价格/SKU", "empty"))
            product = None

        self.last_attempts = attempts
        if product is None:
            raise MonitorError(self._failure_message(pid, attempts))

        if product.skus and self.config.get("stock_api", True):
            try:
                info = self._fetch_stock(pid)
                if info:
                    info.apply(product)
                    attempts.append(("库存接口", f"成功（{len(info.mapping)} 个 SKU）", "ok"))
                else:
                    attempts.append(("库存接口", "未解析到 skuStocks", "empty"))
            except UniqloHTTPError as exc:
                attempts.append(("库存接口", str(exc), exc.kind))
            except Exception as exc:  # noqa: BLE001
                attempts.append(("库存接口", f"{type(exc).__name__}: {exc}", "parse"))

        product.finalize()
        self.last_attempts = attempts
        self.last_source = product.source
        self.last_name = product.name
        if product.price or product.skus:
            self._cache_put(pid, product)
        return product

    def _candidates(self, pid: str):
        """生成 (名称, loader) 序列。"""
        yield from self._custom_candidates(pid)
        if self.config.get("use_builtin_apis", True):
            yield "官方商品 JSON", self._load_spu_json
            yield "官方详情接口", self._load_spu_api
            yield "官方摘要 JSON", self._load_prodinfo
        yield "商品页 HTML", self._load_page
        if self.config.get("mobile_page", True):
            yield "移动端页面", self._load_mobile
        yield "Playwright 渲染", self._load_rendered

    def _custom_candidates(self, pid: str):
        templates = []
        single = str(self.config.get("api_template") or "").strip()
        if single:
            templates.append(single)
        extra = self.config.get("api_templates") or []
        if isinstance(extra, str):
            extra = [extra]
        for tpl in extra:
            if isinstance(tpl, str) and tpl.strip():
                templates.append(tpl.strip())
        for index, tpl in enumerate(dict.fromkeys(templates), 1):
            url = tpl.replace("{code}", pid).replace("{}", pid)
            yield f"自定义接口 #{index}", lambda _pid, u=url: self._load_generic(u)

    def _load_spu_json(self, pid: str) -> UniqloProduct | None:
        raw = self._get_text(SPU_JSON.format(code=pid))
        data = self._parse_json(raw, "官方商品 JSON")
        return parse_spu_json(data, code=pid)

    def _load_spu_api(self, pid: str) -> UniqloProduct | None:
        data = self._get_json(SPU_API.format(code=pid))
        return parse_spu_json(data, code=pid)

    def _load_prodinfo(self, pid: str) -> UniqloProduct | None:
        data = self._get_json(PRODINFO_JSON.format(code=pid))
        return parse_prodinfo_json(data, code=pid)

    def _load_page(self, pid: str) -> UniqloProduct | None:
        return parse_page_html(self._get_text(PRODUCT_PAGE.format(code=pid)), code=pid)

    def _load_mobile(self, pid: str) -> UniqloProduct | None:
        return parse_page_html(self._get_text(MOBILE_PAGE.format(code=pid)), code=pid)

    def _load_rendered(self, pid: str) -> UniqloProduct | None:
        html = self._maybe_render(
            PRODUCT_PAGE.format(code=pid), cookie=self.config.get("cookie", "")
        )
        if not html:
            raise UniqloHTTPError("Playwright 未安装或渲染失败", kind="render")
        return parse_page_html(html, code=pid)

    def _load_generic(self, url: str) -> UniqloProduct | None:
        raw = self._get_text(url)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return parse_page_html(raw)
        return parse_spu_json(data) or parse_generic_json(data)

    # -------------------------------------------------- 库存
    def _fetch_stock(self, pid: str) -> StockInfo | None:
        body = {"distribution": "EXPRESS", "productCode": pid, "type": "DETAIL"}
        data = self._post_json(STOCK_API, body)
        return parse_stock_json(data)

    # -------------------------------------------------- 网络层
    def _get_text(self, url: str) -> str:
        text, _ = self._request("GET", url)
        return text

    def _get_json(self, url: str) -> dict:
        return self._parse_json(self._get_text(url), url)

    def _post_json(self, url: str, body: dict) -> dict:
        text, _ = self._request("POST", url, body)
        return self._parse_json(text, url)

    @staticmethod
    def _parse_json(text: str, source: str) -> dict:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            preview = " ".join((text or "").strip().split())[:120]
            raise UniqloHTTPError(
                f"返回不是 JSON（Content-Type/结构可能已变更）：{preview or '空响应'}",
                kind="invalid_json",
            )
        if not isinstance(data, (dict, list)):
            raise UniqloHTTPError("JSON 顶层结构不是对象/数组", kind="invalid_json")
        return data

    def _request(self, method: str, url: str, body: dict | None = None) -> tuple[str, dict]:
        last_error: UniqloHTTPError | None = None
        for attempt in range(self.retry_times + 1):
            try:
                status, text, headers = self._send(method, url, body)
            except UniqloHTTPError:
                raise
            except requests.Timeout:
                last_error = UniqloHTTPError("请求超时", kind="timeout")
                status = None
            except requests.RequestException as exc:
                last_error = UniqloHTTPError(f"网络错误：{exc}", kind="network")
                status = None
            else:
                if 200 <= status < 300:
                    return text, headers
                last_error = self._http_error(status, headers)

            retryable = last_error.kind in ("timeout", "network", "rate_limited", "server")
            if not retryable or attempt >= self.retry_times:
                raise last_error
            wait = last_error.retry_after or self.retry_backoff * (attempt + 1)
            time.sleep(min(wait, 10))
        raise last_error or UniqloHTTPError("请求失败")

    def _send(self, method: str, url: str, body: dict | None) -> tuple[int, str, dict]:
        if self._transport:
            return self._transport(method, url, body)
        response = requests.request(
            method, url, headers=self._headers(), json=body, timeout=self.timeout
        )
        return response.status_code, response.text, dict(response.headers)

    def _headers(self) -> dict:
        headers = {
            "User-Agent": DEFAULT_UA,
            "Accept": "application/json, text/html, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.uniqlo.cn/",
        }
        extra = self.config.get("headers") or {}
        if isinstance(extra, dict):
            headers.update({str(k): str(v) for k, v in extra.items()})
        cookie = str(self.config.get("cookie") or "").strip()
        if cookie:
            headers["Cookie"] = cookie
        return headers

    @staticmethod
    def _http_error(status: int, headers: dict) -> UniqloHTTPError:
        retry_after = None
        try:
            value = (headers or {}).get("Retry-After") or (headers or {}).get("retry-after")
            retry_after = float(value) if value else None
        except (TypeError, ValueError):
            retry_after = None
        if status == 429:
            return UniqloHTTPError("HTTP 429（请求被限流）", "rate_limited", status, retry_after)
        if status == 403:
            return UniqloHTTPError("HTTP 403（疑似风控拦截）", "blocked", status)
        if status == 404:
            return UniqloHTTPError("HTTP 404（商品不存在或接口路径变更）", "not_found", status)
        if status >= 500:
            return UniqloHTTPError(f"HTTP {status}（服务端错误）", "server", status, retry_after)
        return UniqloHTTPError(f"HTTP {status}", "http", status, retry_after)

    # -------------------------------------------------- 缓存
    def _cache_get(self, pid: str) -> UniqloProduct | None:
        if self.cache_ttl <= 0:
            return None
        item = self._cache.get(pid)
        if not item:
            return None
        saved, product = item
        if time.monotonic() - saved > self.cache_ttl:
            self._cache.pop(pid, None)
            return None
        return product

    def _cache_put(self, pid: str, product: UniqloProduct) -> None:
        if self.cache_ttl > 0:
            self._cache[pid] = (time.monotonic(), product)

    # -------------------------------------------------- 诊断
    def _failure_message(self, pid: str, attempts: list[tuple[str, str, str]]) -> str:
        lines = [f"优衣库商品 {pid} 抓取失败，已尝试 {len(attempts)} 种方式："]
        for name, detail, _kind in attempts:
            lines.append(f"  · {name}：{detail}")
        kinds = {kind for _name, _detail, kind in attempts}
        tips = ["先确认商品编号是否有效（形如 u0000000066172）"]
        if "blocked" in kinds or "rate_limited" in kinds:
            tips.append(
                "返回 403/429 说明被限流：把 check 间隔调到 1 小时以上，"
                "在 config.uniqlo.cookie 填入浏览器 Cookie，或稍后再试"
            )
        if "timeout" in kinds or "network" in kinds:
            tips.append("超时/网络错误：检查网络代理，或调大 uniqlo.request_timeout")
        if "invalid_json" in kinds:
            tips.append("官网结构可能改版：升级项目，或用 uniqlo.api_template 指定新接口")
        if "render" in kinds:
            tips.append(
                "安装 Playwright（pip install playwright && playwright install chromium）"
                "可启用浏览器渲染兜底"
            )
        lines.append("建议：")
        for index, tip in enumerate(tips, 1):
            lines.append(f"  {index}) {tip}")
        return "\n".join(lines)


def expand_short_link(url: str, timeout: float = 10) -> str:
    """展开优衣库短链（u.uniqlo.cn 等），返回 productCode。

    只应在 add 阶段对短链调用；普通链接不需要网络。
    """
    try:
        response = requests.get(
            url, headers={"User-Agent": DEFAULT_UA}, timeout=timeout, allow_redirects=True
        )
    except requests.RequestException as exc:
        raise MonitorError(f"优衣库短链展开失败：{exc}") from exc

    try:
        return parse_product_code(str(response.url))
    except MonitorError:
        match = CODE_RE.search(response.text or "")
        if match:
            return match.group(0).lower()
        raise MonitorError(
            "优衣库短链展开后仍未找到 productCode。"
            "请在浏览器打开短链，复制跳转后的完整商品地址后再 add。"
        )
