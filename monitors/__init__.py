"""监控器：把商品 URL 解析成 (platform, pid)，并提供各平台实现。"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from core.models import PLATFORM_JD, PLATFORM_TAOBAO, PLATFORM_UNIQLO

from .base import BaseMonitor, MonitorError
from .jd import JdMonitor
from .taobao import TaobaoMonitor
from .uniqlo import UniqloMonitor

__all__ = [
    "BaseMonitor", "MonitorError", "JdMonitor", "TaobaoMonitor",
    "UniqloMonitor", "parse_product_url", "get_monitor",
]


def parse_product_url(url: str) -> tuple[str, str]:
    """从商品链接解析 (platform, pid)。"""
    u = url.strip()
    low = u.lower()

    if "uniqlo.cn" in low:
        # https://www.uniqlo.cn/product-detail.html?productCode=u0000000064781
        m = re.search(r"productCode=([A-Za-z0-9_-]+)", u)
        if not m:
            raise MonitorError("优衣库链接中未找到 productCode 参数")
        return PLATFORM_UNIQLO, m.group(1)

    if re.search(r"(item\.jd\.com|jd\.hk|3cn\.jd)", low) or "jd.com" in low:
        # https://item.jd.com/100012043978.html
        m = re.search(r"(\d{6,})\.html", low) or re.search(r"sku(?:Id)?[=:](\d{6,})", low, re.I)
        if m:
            return PLATFORM_JD, m.group(1)
        # 短链 pcitem/3.cn 交给用户直接填 sku 数字
        m = re.search(r"(\d{6,})", low)
        if m:
            return PLATFORM_JD, m.group(1)
        raise MonitorError("无法从京东链接解析商品编号")

    if re.search(r"(taobao\.com|tmall\.com|tmall\.hk)", low):
        # https://item.taobao.com/item.htm?id=679876543210
        q = parse_qs(urlparse(u).query)
        iid = (q.get("id") or q.get("itemId") or [None])[0]
        if not iid:
            m = re.search(r"[?&]id=(\d+)", u)
            iid = m.group(1) if m else None
        if not iid:
            raise MonitorError("无法从淘宝/天猫链接解析商品 id")
        return PLATFORM_TAOBAO, iid

    raise MonitorError(f"暂不支持的平台链接：{url}")


def get_monitor(platform: str, config: dict | None = None) -> BaseMonitor:
    config = config or {}
    if platform == PLATFORM_UNIQLO:
        return UniqloMonitor(config.get("uniqlo") or {})
    if platform == PLATFORM_JD:
        return JdMonitor(config.get("jd") or {})
    if platform == PLATFORM_TAOBAO:
        return TaobaoMonitor(config.get("taobao") or {})
    raise MonitorError(f"未知平台：{platform}")
