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

from core.models import InventorySnapshot, PricePoint
from .base import BaseMonitor, MonitorError

STOCK_STATE = {
    "33": ("现货", True),
    "34": ("配货中", True),
    "36": ("预订", True),
    "39": ("有货", True),
    "40": ("无货", False),
}


class JdMonitor(BaseMonitor):
    platform = "jd"

    def fetch(self, pid: str) -> tuple[PricePoint | None, InventorySnapshot | None]:
        price = self._fetch_price(pid)
        stock = self._fetch_stock(pid)
        return price, stock

    # ---------- 价格 ----------
    def _fetch_price(self, pid: str) -> PricePoint | None:
        url = f"https://p.3.cn/prices/mgets?skuIds=J_{pid}"
        try:
            resp = self.http_get(url)
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise MonitorError(f"京东价格接口请求失败：{e}") from e
        if isinstance(data, list) and data:
            item = data[0]
            try:
                p = float(item.get("p") or item.get("jdPrice") or 0)
            except (TypeError, ValueError):
                p = 0.0
            if p <= 0:
                return None
            op = item.get("op") or item.get("m")
            try:
                op = float(op) if op else None
            except (TypeError, ValueError):
                op = None
            return PricePoint(ts=self.now(), price=p, original_price=op)
        raise MonitorError(f"京东价格接口返回异常：{str(data)[:120]}")

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
        except Exception:  # noqa: BLE001
            return None  # 库存接口失败不阻断价格记录
        stock = (data or {}).get("stock") or {}
        state_code = str(stock.get("StockState", ""))
        name, in_stock = STOCK_STATE.get(state_code, (stock.get("StockStateName", ""), None))
        if in_stock is None:
            return None
        qty = stock.get("StockQty") or stock.get("stockQty")
        try:
            qty = int(qty) if qty not in (None, "", "0") and str(qty).isdigit() else None
        except Exception:  # noqa: BLE001
            qty = None
        return InventorySnapshot(ts=self.now(), in_stock=in_stock, state=name, quantity=qty)
