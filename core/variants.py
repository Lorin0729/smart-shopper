"""关注尺码的匹配规则。

商品里保存的 watched_variant 可能是 "M"、"黑色 M"、"黑色 / M"、"175/92A" 等写法，
而监控抓到的 SKU 是 "颜色 / 尺码"。这里统一做宽松匹配：
- 只写尺码 → 匹配所有颜色的该尺码；
- 只写颜色 → 匹配该颜色所有尺码；
- 颜色 + 尺码 → 精确匹配。
"""
from __future__ import annotations

import re

from .models import VariantSnapshot

_SEP_RE = re.compile(r"[\s/|,，、+]+")
_SIZE_RE = re.compile(r"(?:XXS|XS|S|M|L|XL|XXL|XXXL|3XL|4XL|5XL|\d{2,3}(?:\.5)?)")
_SIZE_FULL_RE = re.compile(r"\d{2,3}/\d{2,3}[A-Za-z]?")
_ALIASES = {"XXXL": "3XL", "XXXXL": "4XL", "XXXXXL": "5XL"}


def split_watched(text: str) -> tuple[str, str]:
    """把关注条件拆成 (颜色, 尺码)。"""
    raw = (text or "").strip()
    if not raw:
        return "", ""
    if _SIZE_FULL_RE.fullmatch(raw):  # 175/92A 这类完整尺码
        return "", raw

    parts = [p for p in _SEP_RE.split(raw) if p]
    color_parts: list[str] = []
    size = ""
    for part in parts:
        if _looks_size(part):
            size = part  # 最后一个像尺码的片段生效
        else:
            color_parts.append(part)
    return " ".join(color_parts).strip(), size


def match_variants(
    variants: list[VariantSnapshot], watched: str
) -> list[VariantSnapshot]:
    """返回符合关注条件的 SKU；条件为空时返回全部。"""
    color, size = split_watched(watched)
    if not color and not size:
        return list(variants)

    matched = []
    for v in variants:
        v_color, v_size = _variant_color_size(v)
        if size and not _size_equal(v_size, size):
            continue
        if color and not _color_equal(v_color, color):
            continue
        matched.append(v)
    return matched


def describe_watched(watched: str) -> str:
    color, size = split_watched(watched)
    if color and size:
        return f"颜色「{color}」+ 尺码「{size}」"
    if size:
        return f"尺码「{size}」（所有颜色）"
    if color:
        return f"颜色「{color}」（所有尺码）"
    return "未设置"


def _variant_color_size(v: VariantSnapshot) -> tuple[str, str]:
    color, size = v.color or "", v.size or ""
    if color and size:
        return color, size
    label = v.variant or ""
    if "/" in label:
        left, right = label.split("/", 1)
        return color or left.strip(), size or right.strip()
    return color, size or label


def _looks_size(text: str) -> bool:
    value = text.strip().upper()
    return bool(_SIZE_RE.fullmatch(value))


def _size_equal(left: str, right: str) -> bool:
    a = _ALIASES.get((left or "").strip().upper(), (left or "").strip().upper())
    b = _ALIASES.get((right or "").strip().upper(), (right or "").strip().upper())
    return a == b


def _color_equal(left: str, right: str) -> bool:
    def norm(value: str) -> str:
        return re.sub(r"[\s\-_/（）()]+", "", (value or "").lower())

    a, b = norm(left), norm(right)
    if not a or not b:
        return False
    return a == b or (len(b) >= 2 and b in a) or (len(a) >= 2 and a in b)
