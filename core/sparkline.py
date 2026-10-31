"""终端价格走势图（Unicode sparkline）。"""
from __future__ import annotations

BARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float], width: int = 60) -> str:
    """把价格序列压成一行块字符走势；高=贵，低=便宜。"""
    if not values:
        return ""
    if len(values) > width:  # 等距抽样压缩到 width
        step = len(values) / width
        values = [values[int(i * step)] for i in range(width)]
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return BARS[0] * len(values)
    return "".join(BARS[min(7, int((v - lo) / (hi - lo) * 7.999))] for v in values)


def trend_line(values: list[float], width: int = 60) -> str:
    """带首尾标注的走势行：`▁▂▄█▅ （¥899 → ¥819）`。"""
    s = sparkline(values, width)
    if not s:
        return ""
    return f"{s}  （¥{values[0]:g} → ¥{values[-1]:g}）"
