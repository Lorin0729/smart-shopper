"""多币种显示：价格仍以人民币存库，仅展示时换算。

汇率来自 config.currency.rates（相对 1 CNY），未配置的币种用内置近似值。
离线、无网络请求，适合海外站点/跨境购物时快速换算出心理价位。
"""
from __future__ import annotations

from dataclasses import dataclass

# 内置近似汇率（1 CNY = ?），仅用于展示，不用于交易
DEFAULT_RATES = {
    "CNY": 1.0,
    "USD": 0.14,
    "EUR": 0.128,
    "GBP": 0.11,
    "JPY": 20.6,
    "HKD": 1.09,
    "KRW": 194.0,
    "AUD": 0.215,
    "CAD": 0.195,
}

SYMBOLS = {
    "CNY": "¥", "JPY": "¥", "USD": "$", "EUR": "€", "GBP": "£",
    "HKD": "HK$", "KRW": "₩", "AUD": "A$", "CAD": "C$",
}


@dataclass
class Currency:
    code: str = "CNY"
    rates: dict | None = None

    def __post_init__(self):
        self.code = (self.code or "CNY").upper()
        merged = dict(DEFAULT_RATES)
        if self.rates:
            merged.update({str(k).upper(): float(v) for k, v in self.rates.items()
                           if isinstance(v, (int, float))})
        self.rate = float(merged.get(self.code, 1.0))
        self.symbol = SYMBOLS.get(self.code, self.code + " ")

    def convert(self, amount_cny: float) -> float:
        return amount_cny * self.rate

    def format(self, amount_cny: float | None, decimals: int | None = None) -> str:
        if amount_cny is None:
            return "-"
        value = self.convert(amount_cny)
        if decimals is None:
            decimals = 0 if (self.code in ("CNY", "JPY", "KRW") or abs(value) >= 100) else 2
        return f"{self.symbol}{value:,.{decimals}f}"

    def describe(self) -> str:
        if self.code == "CNY":
            return "人民币（¥）"
        return f"{self.code}（{self.symbol.strip()}，1 CNY ≈ {self.rate:g}）"


def from_config(cfg: dict | None) -> Currency:
    """支持 config.currency = "USD" 或 {"code": "USD", "rates": {...}}。"""
    conf = (cfg or {}).get("currency") or {}
    if isinstance(conf, str):
        return Currency(conf)
    if isinstance(conf, dict):
        return Currency(conf.get("code") or "CNY", conf.get("rates"))
    return Currency("CNY")
