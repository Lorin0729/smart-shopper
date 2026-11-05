"""监控器基类与公共工具。"""
from __future__ import annotations

import ssl
import time

import requests
from requests.adapters import HTTPAdapter

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = 15


class _SystemTrustAdapter(HTTPAdapter):
    """用操作系统信任库做 TLS 校验（Windows 公司代理装了根证书时，certifi 会误判）。"""

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = ssl.create_default_context()
        return super().init_poolmanager(*args, **kwargs)


class MonitorError(RuntimeError):
    """抓取失败。"""


class BaseMonitor:
    """子类实现 fetch()：返回 (价格点, 库存快照)，任一可能为 None。

    可选实现 variants()：返回尺码/SKU 级库存（UniqloMonitor / JdMonitor 已支持）。
    """

    platform = "base"
    render_fallback = True  # 解析失败时是否尝试 Playwright 渲染（需已安装）

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def fetch(self, pid: str) -> tuple:
        raise NotImplementedError

    def variants(self, pid: str) -> list:
        """尺码/SKU 级库存，默认不支持则返回空列表。"""
        return []

    def _maybe_render(self, url: str, cookie: str = "",
                      wait_selector: str | None = None) -> str | None:
        """尝试用 Playwright 渲染页面；未安装或失败返回 None。"""
        if not self.config.get("playwright_fallback", self.render_fallback):
            return None
        try:
            from . import renderer
            if not renderer.available():
                return None
            return renderer.render_html(url, cookie=cookie, wait_selector=wait_selector)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def http_get(url: str, **kw) -> requests.Response:
        headers = kw.pop("headers", {})
        headers.setdefault("User-Agent", DEFAULT_UA)
        try:
            return requests.get(url, headers=headers, timeout=TIMEOUT, **kw)
        except requests.exceptions.SSLError:
            # 公司代理/杀毒软件把根证书装在系统信任库，但 requests 默认只信 certifi
            session = requests.Session()
            session.mount("https://", _SystemTrustAdapter())
            return session.get(url, headers=headers, timeout=TIMEOUT, **kw)

    @staticmethod
    def now() -> float:
        return time.time()
