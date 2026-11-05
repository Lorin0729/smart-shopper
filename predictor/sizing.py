"""尺码换算助手：中国号型 / 国际字母码 / 美码 / 欧码 / 日码对照，以及鞋码互换。

数据为常见成衣尺码表的近似整理（优衣库、京东等主流品牌通用性较高），
不同品牌、版型（修身/宽松）会有偏差，仅作选购参考；下单前请以商品页尺码表为准。
"""
from __future__ import annotations

from dataclasses import dataclass

# ------------------------------------------------------------------ 上衣
TOPS: dict[str, list[dict]] = {
    "men": [
        {"alpha": "S", "cn": "165/84A", "height": (160, 168), "chest": (80, 88),
         "us": "34-36", "eu": "44-46", "jp": "S"},
        {"alpha": "M", "cn": "170/88A", "height": (168, 175), "chest": (88, 96),
         "us": "38-40", "eu": "48-50", "jp": "M"},
        {"alpha": "L", "cn": "175/92A", "height": (175, 182), "chest": (96, 104),
         "us": "42-44", "eu": "52-54", "jp": "L"},
        {"alpha": "XL", "cn": "180/96A", "height": (180, 188), "chest": (104, 112),
         "us": "46-48", "eu": "56-58", "jp": "XL"},
        {"alpha": "XXL", "cn": "185/100A", "height": (185, 193), "chest": (112, 120),
         "us": "50-52", "eu": "60-62", "jp": "XXL"},
    ],
    "women": [
        {"alpha": "S", "cn": "155/80A", "height": (150, 158), "chest": (76, 84),
         "us": "4-6", "eu": "34-36", "jp": "S"},
        {"alpha": "M", "cn": "160/84A", "height": (158, 165), "chest": (84, 90),
         "us": "8-10", "eu": "38-40", "jp": "M"},
        {"alpha": "L", "cn": "165/88A", "height": (165, 172), "chest": (90, 98),
         "us": "12-14", "eu": "42-44", "jp": "L"},
        {"alpha": "XL", "cn": "170/92A", "height": (170, 178), "chest": (98, 106),
         "us": "16-18", "eu": "46-48", "jp": "XL"},
        {"alpha": "XXL", "cn": "175/96A", "height": (175, 183), "chest": (106, 114),
         "us": "20", "eu": "50", "jp": "XXL"},
    ],
}

# ------------------------------------------------------------------ 裤装
PANTS: dict[str, list[dict]] = {
    "men": [
        {"alpha": "S", "cn": "165/74A", "waist": (70, 76), "us": "29-30"},
        {"alpha": "M", "cn": "170/78A", "waist": (76, 82), "us": "31-32"},
        {"alpha": "L", "cn": "175/82A", "waist": (82, 88), "us": "33-34"},
        {"alpha": "XL", "cn": "180/88A", "waist": (88, 94), "us": "35-36"},
        {"alpha": "XXL", "cn": "185/94A", "waist": (94, 102), "us": "38"},
    ],
    "women": [
        {"alpha": "S", "cn": "155/62A", "waist": (58, 64), "us": "25-26"},
        {"alpha": "M", "cn": "160/66A", "waist": (64, 70), "us": "27-28"},
        {"alpha": "L", "cn": "165/70A", "waist": (70, 76), "us": "29-30"},
        {"alpha": "XL", "cn": "170/76A", "waist": (76, 84), "us": "31-32"},
        {"alpha": "XXL", "cn": "175/82A", "waist": (84, 92), "us": "33-34"},
    ],
}

# ------------------------------------------------------------------ 鞋码
SHOES: dict[str, list[dict]] = {
    "men": [
        {"cn": 245, "eu": 39, "us": 7.0, "uk": 6.0},
        {"cn": 250, "eu": 40, "us": 7.5, "uk": 6.5},
        {"cn": 255, "eu": 41, "us": 8.0, "uk": 7.5},
        {"cn": 260, "eu": 42, "us": 8.5, "uk": 8.0},
        {"cn": 265, "eu": 43, "us": 9.5, "uk": 9.0},
        {"cn": 270, "eu": 44, "us": 10.0, "uk": 9.5},
        {"cn": 275, "eu": 45, "us": 11.0, "uk": 10.0},
        {"cn": 280, "eu": 46, "us": 12.0, "uk": 11.0},
    ],
    "women": [
        {"cn": 225, "eu": 35, "us": 5.0, "uk": 3.0},
        {"cn": 230, "eu": 36, "us": 6.0, "uk": 4.0},
        {"cn": 235, "eu": 37, "us": 6.5, "uk": 4.5},
        {"cn": 240, "eu": 38, "us": 7.5, "uk": 5.5},
        {"cn": 245, "eu": 39, "us": 8.0, "uk": 6.0},
        {"cn": 250, "eu": 40, "us": 9.0, "uk": 7.0},
        {"cn": 255, "eu": 41, "us": 9.5, "uk": 7.5},
        {"cn": 260, "eu": 42, "us": 10.0, "uk": 8.0},
    ],
}


@dataclass
class SizeMatch:
    row: dict
    exact: bool       # 是否落在标准区间内
    note: str = ""


def _gender(gender: str | None) -> str:
    return "women" if (gender or "").lower() in ("women", "female", "女", "w", "f") else "men"


def tops_table(gender: str = "men") -> list[dict]:
    return TOPS[_gender(gender)]


def shoes_table(gender: str = "men") -> list[dict]:
    return SHOES[_gender(gender)]


def recommend_top(gender: str = "men", height: float | None = None,
                  chest: float | None = None) -> SizeMatch:
    """按身高/胸围推荐尺码；只给一个维度时用该维度命中。"""
    table = tops_table(gender)
    if height is None and chest is None:
        raise ValueError("至少提供身高或胸围")

    best, best_score = table[0], float("-inf")
    for row in table:
        score = 0.0
        if chest is not None:
            lo, hi = row["chest"]
            score += 2.0 if lo <= chest <= hi else -min(abs(chest - lo), abs(chest - hi)) / 10
        if height is not None:
            lo, hi = row["height"]
            score += 1.0 if lo <= height <= hi else -min(abs(height - lo), abs(height - hi)) / 10
        if score > best_score:
            best, best_score = row, score
    exact = True
    if chest is not None:
        lo, hi = best["chest"]
        exact = lo <= chest <= hi
    if height is not None:
        lo, hi = best["height"]
        exact = exact and lo <= height <= hi
    note = "" if exact else "身体数据处于尺码交界，建议按实际试穿或选大一码"
    return SizeMatch(best, exact, note)


def pants_table(gender: str = "men") -> list[dict]:
    return PANTS[_gender(gender)]


def recommend_pants(gender: str = "men", waist: float | None = None,
                    height: float | None = None) -> SizeMatch:
    """按腰围推荐裤装尺码；只给身高时取中间码。"""
    table = pants_table(gender)
    if waist is None and height is None:
        raise ValueError("裤装至少提供腰围或身高")

    if waist is not None:
        def in_range(row: dict) -> bool:
            lo, hi = row["waist"]
            # 用半开区间避免 82cm 同时命中 M/L 两个码；最后一档包含上界
            return lo <= waist < hi or (row is table[-1] and waist == hi)

        best = min(
            table,
            key=lambda r: (0, 0) if in_range(r)
            else (1, min(abs(waist - r["waist"][0]), abs(waist - r["waist"][1]))),
        )
        exact = in_range(best)
    else:
        best = table[len(table) // 2]
        exact = False
    note = "" if exact else "腰围处于尺码交界，建议按实际试穿或选大一码"
    return SizeMatch(best, exact, note)


def recommend_by_body(kind: str = "top", gender: str = "men",
                      height: float | None = None, weight: float | None = None,
                      waist: float | None = None,
                      chest: float | None = None) -> SizeMatch:
    """按身高/体重/腰围/胸围推荐尺码。

    - 上衣：身高 + 胸围为主；没有胸围时用 BMI 做一档修正（体重只是辅助，不是精确量体）。
    - 裤装：腰围为主。
    """
    if kind == "pants":
        if waist is None:
            raise ValueError("裤装推荐需要腰围（cm）")
        return recommend_pants(gender, waist, height)

    if chest is None and height is None:
        raise ValueError("上衣推荐至少需要身高或胸围")

    match = recommend_top(gender, height, chest)
    if weight is not None and height and chest is None:
        bmi = weight / (height / 100) ** 2
        table = tops_table(gender)
        idx = table.index(match.row)
        if bmi >= 26 and idx + 1 < len(table):
            match = SizeMatch(table[idx + 1], False,
                              f"BMI {bmi:.1f} 偏大，按身高体重上调一码；提供胸围会更准")
        elif bmi < 18.5 and idx > 0:
            match = SizeMatch(table[idx - 1], False,
                              f"BMI {bmi:.1f} 偏瘦，按身高体重下调一码；提供胸围会更准")
        elif not match.note:
            match.note = f"BMI {bmi:.1f}，按身高推荐；提供胸围会更准"
    return match


def infer_kind(name: str = "", category: str = "") -> str:
    """从商品名/类目猜品类，用于选择尺码表。"""
    text = f"{name or ''} {category or ''}".lower()
    if any(k in text for k in ("鞋", "靴", "sneaker", "shoe")):
        return "shoes"
    if any(k in text for k in ("裤", "牛仔", "裙", "pants", "trouser", "jeans", "skirt")):
        return "pants"
    return "top"


def convert_shoe(gender: str = "men", cn: float | None = None, eu: float | None = None,
                 us: float | None = None, uk: float | None = None) -> SizeMatch:
    """鞋码互换：给定任一体系，返回四套体系数值。"""
    table = shoes_table(gender)
    field, value = None, None
    for name, v in (("cn", cn), ("eu", eu), ("us", us), ("uk", uk)):
        if v is not None:
            field, value = name, float(v)
            break
    if field is None:
        raise ValueError("请提供 cn/eu/us/uk 中的一个")
    best = min(table, key=lambda r: abs(float(r[field]) - value))
    exact = abs(float(best[field]) - value) < 1e-9
    note = "" if exact else "该数值不在标准码表中，已取最接近的一档"
    return SizeMatch(best, exact, note)


def format_top_row(row: dict) -> str:
    return (f"{row['alpha']:<4} 中国 {row['cn']:<10} 美码 {row['us']:<6} "
            f"欧码 {row['eu']:<6} 日码 {row['jp']:<4} "
            f"（胸围 {row['chest'][0]}~{row['chest'][1]} cm，身高 {row['height'][0]}~{row['height'][1]} cm）")


def format_shoe_row(row: dict) -> str:
    return (f"中国 {row['cn']}mm  欧码 {row['eu']}  美码 {row['us']:g}  "
            f"英码 {row['uk']:g}")
