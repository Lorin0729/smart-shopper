"""核心数据模型。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date

PLATFORM_UNIQLO = "uniqlo"
PLATFORM_JD = "jd"
PLATFORM_TAOBAO = "taobao"
PLATFORMS = (PLATFORM_UNIQLO, PLATFORM_JD, PLATFORM_TAOBAO)


@dataclass
class Product:
    """被跟踪的商品。"""

    platform: str
    pid: str
    url: str
    name: str = ""
    category: str = "general"  # 见 predictor/season.py 的类目档案
    launch_date: str | None = None  # 发售/上架日 ISO，如 2026-09-15
    added_at: float = field(default_factory=time.time)

    def display_id(self) -> str:
        return f"{self.platform}:{self.pid}"

    def launch(self) -> date | None:
        if not self.launch_date:
            return None
        try:
            return date.fromisoformat(self.launch_date)
        except ValueError:
            return None


@dataclass
class PricePoint:
    """一次价格采样。"""

    ts: float
    price: float
    original_price: float | None = None  # 划线价/吊牌价


@dataclass
class InventorySnapshot:
    """一次库存采样。"""

    ts: float
    in_stock: bool
    state: str = ""  # 现货 / 无货 / 补货中 / 紧张 ...
    quantity: int | None = None  # 若平台能给出具体数量
