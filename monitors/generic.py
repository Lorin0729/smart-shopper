"""通用站点监控：用配置里的正则解析任意电商页面。

适用于没有专门适配的平台（拼多多、唯品会、天猫超市、品牌官网、海外站点…）。

config.json 配置示例：
{
  "generic": {
    "sites": {
      "mobile.yangkeduo.com": {
        "price_regex": "\\"minPrice\\":(\\d+)",
        "stock_regex": "有货|立即购买",
        "stockout_regex": "售罄|已抢光|补货中",
        "name_regex": "<title>(.+?)</title>",
        "cookie": "",
        "encoding": "utf-8"
      }
    }
  }
}

- 站点按域名后缀匹配，也可用 "*" 作为兜底规则
- price_regex 取第 1 个捕获组；没有捕获组则取整个匹配
- stock_regex 命中视为有货，stockout_regex 命中视为无货；都没命中则记为未知（返回 None）
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from core.models import InventorySnapshot, PricePoint
from .base import BaseMonitor, MonitorError

_CURRENCY = re.compile(r"[¥￥$,，\s]")


class GenericMonitor(BaseMonitor):
    platform = "generic"
    render_fallback = False  # 通用站点默认不渲染，按站点配置显式开启

    def fetch(self, pid: str) -> tuple[PricePoint | None, InventorySnapshot | None]:
        url = pid if pid.startswith("http") else f"https://{pid}"
        site = self._site_config(url)
        if not site.get("price_regex"):
            raise MonitorError(
                f"未为 {urlparse(url).netloc} 配置解析规则。请在 config 的 "
                "generic.sites 中添加该域名的 price_regex。"
            )
        html = self._load(url, site)

        price = _extract_price(html, site["price_regex"])
        if price is None:
            raise MonitorError(
                f"未能按 price_regex 从 {urlparse(url).netloc} 解析出价格（页面可能改版）"
            )

        state = ""
        in_stock = None
        if site.get("stockout_regex") and re.search(site["stockout_regex"], html, re.I):
            in_stock, state = False, site.get("stockout_state", "无货")
        elif site.get("stock_regex") and re.search(site["stock_regex"], html, re.I):
            in_stock, state = True, site.get("stock_state", "有货")

        self.last_name = _extract_name(html, site.get("name_regex", ""))
        stock = None
        if in_stock is not None:
            stock = InventorySnapshot(ts=self.now(), in_stock=in_stock, state=state)
        return PricePoint(ts=self.now(), price=price), stock

    def variants(self, pid: str) -> list:
        return []  # 通用站点不做尺码拆分，避免误解析

    # ---------- 内部 ----------
    def _site_config(self, url: str) -> dict:
        sites = (self.config.get("sites") or {})
        host = urlparse(url).netloc.lower()
        for domain, cfg in sites.items():
            if domain != "*" and (host == domain or host.endswith("." + domain)):
                return cfg or {}
        return sites.get("*") or {}

    def _load(self, url: str, site: dict) -> str:
        headers = dict(site.get("headers") or {})
        if site.get("cookie"):
            headers["Cookie"] = site["cookie"]
        try:
            resp = self.http_get(url, headers=headers)
            if site.get("encoding"):
                resp.encoding = site["encoding"]
            html = resp.text
        except Exception as e:  # noqa: BLE001
            raise MonitorError(f"页面请求失败：{e}") from e
        if site.get("playwright"):
            rendered = self._maybe_render(url, cookie=site.get("cookie", ""))
            if rendered:
                return rendered
        return html


def _extract_price(html: str, pattern: str) -> float | None:
    m = re.search(pattern, html, re.I | re.S)
    if not m:
        return None
    raw = m.group(1) if m.groups() else m.group(0)
    cleaned = _CURRENCY.sub("", raw)
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if 0 < value < 10_000_000 else None


def _extract_name(html: str, pattern: str) -> str:
    if not pattern:
        return ""
    m = re.search(pattern, html, re.I | re.S)
    return (m.group(1) if m.groups() else m.group(0)).strip()[:120] if m else ""
