"""淘宝/天猫监控（实验性）。

淘宝详情数据需要登录态并经过 mtop 签名，纯脚本难以长期稳定。
本模块采用“用户自备 Cookie + 页面/接口关键字段提取”的保守方案：

1. config -> taobao.cookie 填入浏览器复制的 Cookie 字符串；
2. 请求 item.taobao.com/item.htm?id=<id>，从页面 JSON 中提取价格；
3. 提取失败时抛出 MonitorError，提示更新 Cookie。

库存通常无法从 H5 页面稳定取得，返回 None。
"""
from __future__ import annotations

import re

from core.models import PricePoint
from .base import BaseMonitor, MonitorError

ITEM_PAGE = "https://item.taobao.com/item.htm?id={id}"

_PATTERNS = [
    r'"viewPrice"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)"?',
    r'"price"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)"?',
    r'"promPrice".{0,40}?([0-9]+\.[0-9]+)',
    r'J_PromoPrice.{0,120}?([0-9]+\.[0-9]+)',
    r'class="tb-rmb-num">\s*([0-9]+(?:\.[0-9]+)?)',
]
_NAME_PATTERNS = [
    r'<title>([^<]+)</title>',
    r'"title"\s*:\s*"([^"]{4,120})"',
]


class TaobaoMonitor(BaseMonitor):
    platform = "taobao"

    def fetch(self, pid: str) -> tuple[PricePoint | None, None]:
        cookie = self.config.get("cookie", "")
        if not cookie:
            raise MonitorError(
                "淘宝监控需要登录 Cookie：请把浏览器中的 Cookie 填到 config 的 taobao.cookie"
            )
        url = ITEM_PAGE.format(id=pid)
        try:
            resp = self.http_get(url, headers={"Cookie": cookie, "Referer": "https://www.taobao.com/"})
            html = resp.text
        except Exception as e:  # noqa: BLE001
            raise MonitorError(f"淘宝页面请求失败：{e}") from e
        if "login.taobao.com" in resp.url or "登录" in html[:3000]:
            raise MonitorError("Cookie 已失效，请重新复制")

        for pat in _PATTERNS:
            m = re.search(pat, html)
            if m:
                p = float(m.group(1))
                if p > 0:
                    return PricePoint(ts=self.now(), price=p), None

        # 页面结构变化时尝试浏览器渲染兜底
        rendered = self._maybe_render(url, cookie=cookie)
        if rendered:
            for pat in _PATTERNS:
                m = re.search(pat, rendered)
                if m:
                    p = float(m.group(1))
                    if p > 0:
                        return PricePoint(ts=self.now(), price=p), None
        raise MonitorError(
            "未能从淘宝页面解析价格（页面结构变更或需要滑块验证）。"
            "可更新 Cookie，或安装 playwright 启用渲染兜底。"
        )

    @staticmethod
    def extract_name(html: str) -> str:
        for pat in _NAME_PATTERNS:
            m = re.search(pat, html)
            if m:
                return m.group(1).strip()
        return ""
