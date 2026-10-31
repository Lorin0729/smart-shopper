"""监控器基类与公共工具。"""
from __future__ import annotations

import time

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = 15


class MonitorError(RuntimeError):
    """抓取失败。"""


class BaseMonitor:
    """子类实现 fetch()：返回 (价格点, 库存快照)，任一可能为 None。"""

    platform = "base"

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def fetch(self, pid: str) -> tuple:
        raise NotImplementedError

    @staticmethod
    def http_get(url: str, **kw) -> requests.Response:
        headers = kw.pop("headers", {})
        headers.setdefault("User-Agent", DEFAULT_UA)
        return requests.get(url, headers=headers, timeout=TIMEOUT, **kw)

    @staticmethod
    def now() -> float:
        return time.time()
