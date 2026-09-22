"""静态数据表：妖兽、法宝、丹药、品质、秘典残页。

全部用 Python 字典定义，不硬编码进逻辑。
加妖兽 / 加法宝只需要往对应列表里加一条。

v2.0 变更：
- 怪物 6 -> 18 种，按新手/中级/高级三档分布
- 装备 11 -> 30 件，引入品质系统（普/精/稀/史/传）
- 消耗品 4 -> 12 种，新增增益类与特殊类

v3.0 修仙化变更：
- 命名全部换修仙口径：妖兽 / 法宝 / 丹药 / 灵石
- 新增渡劫丹（渡天劫用）、秘典残页（修行大道用）
- 新增 explore_consumables() / explore_equipment_pool() 供探索产出用
- 物品的旧名保留为别名，老存档里的物品照样能查到定义
"""

from __future__ import annotations

import random
from typing import Any

# ---------------------------------------------------------------------------
# 品质系统
# ---------------------------------------------------------------------------
# 品质在「掉落时」决定，同一件装备可以有不同品质。
# 存档里的物品名会带品质后缀，例如 "铁剑[稀]"，
# 这样同名装备不同品质可以同时存在于背包。

QUALITY_COMMON = "common"
QUALITY_FINE = "fine"
QUALITY_RARE = "rare"
QUALITY_EPIC = "epic"
QUALITY_LEGEND = "legend"

# name -> (标记, 属性倍率, 掉落权重)
QUALITIES: dict[str, tuple[str, float, int]] = {
    QUALITY_COMMON: ("普", 1.0, 60),
    QUALITY_FINE: ("精", 1.3, 25),
    QUALITY_RARE: ("稀", 1.7, 10),
    QUALITY_EPIC: ("史", 2.2, 4),
    QUALITY_LEGEND: ("传", 3.0, 1),
}

# 品质优先级（高的优先匹配，避免 "普" 匹配到 "普通" 之类的歧义）
QUALITY_ORDER = [QUALITY_LEGEND, QUALITY_EPIC, QUALITY_RARE, QUALITY_FINE, QUALITY_COMMON]


def quality_tag(quality: str) -> str:
    """品质的显示标记，如 '稀'。"""
    return QUALITIES.get(quality, ("普", 1.0, 60))[0]


def quality_mult(quality: str) -> float:
    """品质的属性倍率。"""
    return QUALITIES.get(quality, ("普", 1.0, 60))[1]


def roll_quality() -> str:
    """按权重随机抽一个品质。"""
    names = list(QUALITIES)
    weights = [QUALITIES[q][2] for q in names]
    return random.choices(names, weights=weights, k=1)[0]


def decorate(base_name: str, quality: str) -> str:
    """把基础物品名加工成带品质的完整名，如 铁剑 -> 铁剑[稀]。

    普通品质也加标记，好处是「掉落物名」永远是唯一的，
    背包里不会出现同名不同属性的东西。
    """
    return f"{base_name}[{quality_tag(quality)}]"


def split_quality(item_name: str) -> tuple[str, str]:
    """拆解带品质的物品名，返回 (基础名, 品质)。

    对不带后缀的老物品名，返回 (原名, common)。
    """
    if not item_name.endswith("]"):
        return item_name, QUALITY_COMMON
    idx = item_name.rfind("[")
    if idx <= 0:
        return item_name, QUALITY_COMMON
    base = item_name[:idx]
    tag = item_name[idx + 1 : -1]
    for q, (t, _, _) in QUALITIES.items():
        if t == tag:
            return base, q
    return item_name, QUALITY_COMMON


# ---------------------------------------------------------------------------
# 怪物表
# ---------------------------------------------------------------------------
# 字段说明：
#   name    怪物名
#   level   建议挑战等级，用于筛选怪池
#   hp      基础生命，实际生命 = hp + level * 15
#   atk     基础攻击
#   defense 基础防御
#   exp     击杀经验
#   gold    (最小值, 最大值) 掉落金币范围
#   tier    档位：novice / mid / high（只用于分组展示）
#   drops   掉落表，rate 为独立概率；item 是基础名，掉落时会附加随机品质
# ---------------------------------------------------------------------------

MONSTERS: list[dict[str, Any]] = [
    {
        "name": "史莱姆",
        "element": "water",
    # ---------------- Lv.1-9   炼气期 ----------------
        "level": 1,
        "hp": 76,
        "atk": 8,
        "defense": 2,
        "exp": 7,
        "gold": (3, 6),
        "tier": "novice",
        "drops": [
            {"item": "小回气丹", "rate": 0.3},
            {"item": "史莱姆凝胶", "rate": 0.18}
        ],
    },
    {
        "name": "田鼠",
        "element": "earth",
        "level": 2,
        "hp": 117,
        "atk": 14,
        "defense": 4,
        "exp": 7,
        "gold": (5, 11),
        "tier": "novice",
        "drops": [
            {"item": "小回气丹", "rate": 0.3},
            {"item": "史莱姆凝胶", "rate": 0.18}
        ],
    },
    {
        "name": "野狼",
        "element": "wind",
        "level": 3,
        "hp": 153,
        "atk": 20,
        "defense": 7,
        "exp": 7,
        "gold": (7, 15),
        "tier": "novice",
        "drops": [
            {"item": "小回气丹", "rate": 0.3},
            {"item": "史莱姆凝胶", "rate": 0.18}
        ],
    },
    {
        "name": "山贼",
        "element": "earth",
        "level": 4,
        "hp": 193,
        "atk": 26,
        "defense": 10,
        "exp": 7,
        "gold": (9, 19),
        "tier": "novice",
        "drops": [
            {"item": "小回气丹", "rate": 0.3},
            {"item": "史莱姆凝胶", "rate": 0.18}
        ],
    },
    {
        "name": "毒蘑菇",
        "element": "water",
        "level": 5,
        "hp": 229,
        "atk": 32,
        "defense": 13,
        "exp": 7,
        "gold": (12, 26),
        "tier": "novice",
        "drops": [
            {"item": "小回气丹", "rate": 0.3},
            {"item": "史莱姆凝胶", "rate": 0.18}
        ],
    },
    {
        "name": "荒原土狼",
        "element": "wind",
        "level": 6,
        "hp": 270,
        "atk": 38,
        "defense": 15,
        "exp": 7,
        "gold": (15, 33),
        "tier": "novice",
        "drops": [
            {"item": "中回气丹", "rate": 0.3},
            {"item": "力量药剂", "rate": 0.1},
            {"item": "斩婴战符", "rate": 0.08}
        ],
    },
    {
        "name": "石魔像",
        "element": "earth",
        "level": 7,
        "hp": 310,
        "atk": 44,
        "defense": 18,
        "exp": 7,
        "gold": (19, 41),
        "tier": "novice",
        "drops": [
            {"item": "中回气丹", "rate": 0.3},
            {"item": "力量药剂", "rate": 0.1},
            {"item": "斩婴战符", "rate": 0.08}
        ],
    },
    {
        "name": "荒野巨蝎",
        "element": "earth",
        "level": 8,
        "hp": 346,
        "atk": 50,
        "defense": 21,
        "exp": 7,
        "gold": (23, 50),
        "tier": "novice",
        "drops": [
            {"item": "中回气丹", "rate": 0.3},
            {"item": "力量药剂", "rate": 0.1},
            {"item": "斩婴战符", "rate": 0.08}
        ],
    },
    {
        "name": "食人藤",
        "element": "earth",
        "level": 9,
        "hp": 387,
        "atk": 55,
        "defense": 23,
        "exp": 7,
        "gold": (27, 59),
        "tier": "novice",
        "drops": [
            {"item": "中回气丹", "rate": 0.3},
            {"item": "力量药剂", "rate": 0.1},
            {"item": "斩婴战符", "rate": 0.08}
        ],
    },
    {
        "name": "骷髅战士",
        "element": "earth",

    # ---------------- Lv.10-18 筑基期 ----------------
        "level": 10,
        "hp": 535,
        "atk": 76,
        "defense": 32,
        "exp": 14,
        "gold": (31, 68),
        "tier": "mid",
        "drops": [
            {"item": "中回气丹", "rate": 0.3},
            {"item": "铁壁药剂", "rate": 0.1},
            {"item": "悟道石", "rate": 0.05}
        ],
    },
    {
        "name": "沙暴蝎王",
        "element": "earth",
        "level": 11,
        "hp": 580,
        "atk": 84,
        "defense": 36,
        "exp": 14,
        "gold": (35, 77),
        "tier": "mid",
        "drops": [
            {"item": "中回气丹", "rate": 0.3},
            {"item": "铁壁药剂", "rate": 0.1},
            {"item": "悟道石", "rate": 0.05}
        ],
    },
    {
        "name": "暗影刺客",
        "element": "wind",
        "level": 12,
        "hp": 630,
        "atk": 91,
        "defense": 39,
        "exp": 14,
        "gold": (40, 88),
        "tier": "mid",
        "drops": [
            {"item": "中回气丹", "rate": 0.3},
            {"item": "铁壁药剂", "rate": 0.1},
            {"item": "悟道石", "rate": 0.05}
        ],
    },
    {
        "name": "熔岩巨人",
        "element": "fire",
        "level": 13,
        "hp": 675,
        "atk": 99,
        "defense": 43,
        "exp": 14,
        "gold": (45, 99),
        "tier": "mid",
        "drops": [
            {"item": "中回气丹", "rate": 0.3},
            {"item": "铁壁药剂", "rate": 0.1},
            {"item": "悟道石", "rate": 0.05}
        ],
    },
    {
        "name": "深渊魔犬",
        "element": "fire",
        "level": 14,
        "hp": 729,
        "atk": 106,
        "defense": 46,
        "exp": 14,
        "gold": (50, 110),
        "tier": "mid",
        "drops": [
            {"item": "大回气丹", "rate": 0.32},
            {"item": "炼虚剑", "rate": 0.08},
            {"item": "迅捷药剂", "rate": 0.08}
        ],
    },
    {
        "name": "幽泉蛇妖",
        "element": "water",
        "level": 15,
        "hp": 774,
        "atk": 114,
        "defense": 49,
        "exp": 14,
        "gold": (56, 123),
        "tier": "mid",
        "drops": [
            {"item": "大回气丹", "rate": 0.32},
            {"item": "炼虚剑", "rate": 0.08},
            {"item": "迅捷药剂", "rate": 0.08}
        ],
    },
    {
        "name": "裂空雕王",
        "element": "wind",
        "level": 16,
        "hp": 823,
        "atk": 121,
        "defense": 53,
        "exp": 14,
        "gold": (61, 134),
        "tier": "mid",
        "drops": [
            {"item": "大回气丹", "rate": 0.32},
            {"item": "炼虚剑", "rate": 0.08},
            {"item": "迅捷药剂", "rate": 0.08}
        ],
    },
    {
        "name": "亡灵法师",
        "element": "water",
        "level": 17,
        "hp": 873,
        "atk": 128,
        "defense": 56,
        "exp": 14,
        "gold": (67, 147),
        "tier": "mid",
        "drops": [
            {"item": "大回气丹", "rate": 0.32},
            {"item": "炼虚剑", "rate": 0.08},
            {"item": "迅捷药剂", "rate": 0.08}
        ],
    },
    {
        "name": "黄泉判官",
        "element": "water",
        "level": 18,
        "hp": 922,
        "atk": 135,
        "defense": 59,
        "exp": 14,
        "gold": (73, 160),
        "tier": "mid",
        "drops": [
            {"item": "大回气丹", "rate": 0.32},
            {"item": "炼虚剑", "rate": 0.08},
            {"item": "迅捷药剂", "rate": 0.08}
        ],
    },
    {
        "name": "冰霜巨魔",
        "element": "water",

    # ---------------- Lv.19-27 金丹期 ----------------
        "level": 19,
        "hp": 1165,
        "atk": 172,
        "defense": 76,
        "exp": 28,
        "gold": (79, 173),
        "tier": "mid",
        "drops": [
            {"item": "大回气丹", "rate": 0.36},
            {"item": "还魂丹", "rate": 0.06},
            {"item": "悟道石", "rate": 0.08},
            {"item": "火之残页", "rate": 0.08}
        ],
    },
    {
        "name": "血影魔修",
        "element": "fire",
        "level": 20,
        "hp": 1224,
        "atk": 180,
        "defense": 80,
        "exp": 28,
        "gold": (86, 189),
        "tier": "mid",
        "drops": [
            {"item": "大回气丹", "rate": 0.36},
            {"item": "还魂丹", "rate": 0.06},
            {"item": "悟道石", "rate": 0.08},
            {"item": "火之残页", "rate": 0.08}
        ],
    },
    {
        "name": "玄铁傀儡",
        "element": "earth",
        "level": 21,
        "hp": 1282,
        "atk": 189,
        "defense": 84,
        "exp": 28,
        "gold": (92, 202),
        "tier": "mid",
        "drops": [
            {"item": "大回气丹", "rate": 0.36},
            {"item": "还魂丹", "rate": 0.06},
            {"item": "悟道石", "rate": 0.08},
            {"item": "火之残页", "rate": 0.08}
        ],
    },
    {
        "name": "烈焰巨龙",
        "element": "fire",
        "level": 22,
        "hp": 1341,
        "atk": 198,
        "defense": 88,
        "exp": 28,
        "gold": (99, 217),
        "tier": "mid",
        "drops": [
            {"item": "大回气丹", "rate": 0.36},
            {"item": "还魂丹", "rate": 0.06},
            {"item": "悟道石", "rate": 0.08},
            {"item": "火之残页", "rate": 0.08}
        ],
    },
    {
        "name": "赤炎凤凰",
        "element": "fire",
        "level": 23,
        "hp": 1399,
        "atk": 207,
        "defense": 92,
        "exp": 28,
        "gold": (106, 233),
        "tier": "mid",
        "drops": [
            {"item": "大回气丹", "rate": 0.36},
            {"item": "还魂丹", "rate": 0.06},
            {"item": "悟道石", "rate": 0.08},
            {"item": "火之残页", "rate": 0.08}
        ],
    },
    {
        "name": "九幽鬼将",
        "element": "water",
        "level": 24,
        "hp": 1458,
        "atk": 216,
        "defense": 96,
        "exp": 28,
        "gold": (113, 248),
        "tier": "mid",
        "drops": [
            {"item": "大回气丹", "rate": 0.36},
            {"item": "还魂丹", "rate": 0.06},
            {"item": "悟道石", "rate": 0.08},
            {"item": "火之残页", "rate": 0.08}
        ],
    },
    {
        "name": "混沌魔神",
        "element": "fire",
        "level": 25,
        "hp": 1516,
        "atk": 225,
        "defense": 100,
        "exp": 28,
        "gold": (120, 264),
        "tier": "mid",
        "drops": [
            {"item": "大回气丹", "rate": 0.36},
            {"item": "还魂丹", "rate": 0.06},
            {"item": "悟道石", "rate": 0.08},
            {"item": "火之残页", "rate": 0.08}
        ],
    },
    {
        "name": "万载古树妖",
        "element": "earth",
        "level": 26,
        "hp": 1575,
        "atk": 234,
        "defense": 104,
        "exp": 28,
        "gold": (127, 279),
        "tier": "mid",
        "drops": [
            {"item": "大回气丹", "rate": 0.36},
            {"item": "还魂丹", "rate": 0.06},
            {"item": "悟道石", "rate": 0.08},
            {"item": "火之残页", "rate": 0.08}
        ],
    },
    {
        "name": "罡风鹏王",
        "element": "wind",
        "level": 27,
        "hp": 1633,
        "atk": 242,
        "defense": 108,
        "exp": 28,
        "gold": (135, 297),
        "tier": "mid",
        "drops": [
            {"item": "大回气丹", "rate": 0.36},
            {"item": "还魂丹", "rate": 0.06},
            {"item": "悟道石", "rate": 0.08},
            {"item": "火之残页", "rate": 0.08}
        ],
    },
    {
        "name": "渡厄天魔",
        "element": "fire",

    # ---------------- Lv.28-36 元婴期 ----------------
        "level": 28,
        "hp": 2025,
        "atk": 302,
        "defense": 135,
        "exp": 57,
        "gold": (143, 314),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.38},
            {"item": "还魂丹", "rate": 0.08},
            {"item": "渡劫丹", "rate": 0.06},
            {"item": "金之残页", "rate": 0.09},
            {"item": "风之残页", "rate": 0.09}
        ],
    },
    {
        "name": "雷泽蛟龙",
        "element": "water",
        "level": 29,
        "hp": 2097,
        "atk": 312,
        "defense": 139,
        "exp": 57,
        "gold": (150, 330),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.38},
            {"item": "还魂丹", "rate": 0.08},
            {"item": "渡劫丹", "rate": 0.06},
            {"item": "金之残页", "rate": 0.09},
            {"item": "风之残页", "rate": 0.09}
        ],
    },
    {
        "name": "太虚剑灵",
        "element": "wind",
        "level": 30,
        "hp": 2169,
        "atk": 323,
        "defense": 144,
        "exp": 57,
        "gold": (158, 347),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.38},
            {"item": "还魂丹", "rate": 0.08},
            {"item": "渡劫丹", "rate": 0.06},
            {"item": "金之残页", "rate": 0.09},
            {"item": "风之残页", "rate": 0.09}
        ],
    },
    {
        "name": "焚天火鸦",
        "element": "fire",
        "level": 31,
        "hp": 2236,
        "atk": 334,
        "defense": 149,
        "exp": 57,
        "gold": (166, 365),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.38},
            {"item": "还魂丹", "rate": 0.08},
            {"item": "渡劫丹", "rate": 0.06},
            {"item": "金之残页", "rate": 0.09},
            {"item": "风之残页", "rate": 0.09}
        ],
    },
    {
        "name": "玄龟老祖",
        "element": "earth",
        "level": 32,
        "hp": 2308,
        "atk": 344,
        "defense": 154,
        "exp": 57,
        "gold": (175, 385),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.38},
            {"item": "还魂丹", "rate": 0.08},
            {"item": "渡劫丹", "rate": 0.06},
            {"item": "金之残页", "rate": 0.09},
            {"item": "风之残页", "rate": 0.09}
        ],
    },
    {
        "name": "幽冥鬼帝",
        "element": "water",
        "level": 33,
        "hp": 2376,
        "atk": 355,
        "defense": 159,
        "exp": 57,
        "gold": (183, 402),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.38},
            {"item": "还魂丹", "rate": 0.08},
            {"item": "渡劫丹", "rate": 0.06},
            {"item": "金之残页", "rate": 0.09},
            {"item": "风之残页", "rate": 0.09}
        ],
    },
    {
        "name": "赤霄雷兽",
        "element": "fire",
        "level": 34,
        "hp": 2448,
        "atk": 365,
        "defense": 164,
        "exp": 57,
        "gold": (192, 422),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.38},
            {"item": "还魂丹", "rate": 0.08},
            {"item": "渡劫丹", "rate": 0.06},
            {"item": "金之残页", "rate": 0.09},
            {"item": "风之残页", "rate": 0.09}
        ],
    },
    {
        "name": "镇岳石君",
        "element": "earth",
        "level": 35,
        "hp": 2515,
        "atk": 376,
        "defense": 169,
        "exp": 57,
        "gold": (200, 440),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.38},
            {"item": "还魂丹", "rate": 0.08},
            {"item": "渡劫丹", "rate": 0.06},
            {"item": "金之残页", "rate": 0.09},
            {"item": "风之残页", "rate": 0.09}
        ],
    },
    {
        "name": "化神魔尊",
        "element": "fire",
        "level": 36,
        "hp": 2587,
        "atk": 387,
        "defense": 174,
        "exp": 57,
        "gold": (209, 459),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.38},
            {"item": "还魂丹", "rate": 0.08},
            {"item": "渡劫丹", "rate": 0.06},
            {"item": "金之残页", "rate": 0.09},
            {"item": "风之残页", "rate": 0.09}
        ],
    },
    {
        "name": "虚空噬虫",
        "element": "wind",

    # ---------------- Lv.37-45 化神期 ----------------
        "level": 37,
        "hp": 3249,
        "atk": 486,
        "defense": 218,
        "exp": 114,
        "gold": (218, 479),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.4},
            {"item": "还魂丹", "rate": 0.09},
            {"item": "渡劫丹", "rate": 0.09},
            {"item": "水之残页", "rate": 0.1},
            {"item": "土之残页", "rate": 0.1}
        ],
    },
    {
        "name": "九头相柳",
        "element": "water",
        "level": 38,
        "hp": 3334,
        "atk": 499,
        "defense": 224,
        "exp": 114,
        "gold": (227, 499),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.4},
            {"item": "还魂丹", "rate": 0.09},
            {"item": "渡劫丹", "rate": 0.09},
            {"item": "水之残页", "rate": 0.1},
            {"item": "土之残页", "rate": 0.1}
        ],
    },
    {
        "name": "太乙金甲",
        "element": "earth",
        "level": 39,
        "hp": 3420,
        "atk": 511,
        "defense": 230,
        "exp": 114,
        "gold": (237, 521),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.4},
            {"item": "还魂丹", "rate": 0.09},
            {"item": "渡劫丹", "rate": 0.09},
            {"item": "水之残页", "rate": 0.1},
            {"item": "土之残页", "rate": 0.1}
        ],
    },
    {
        "name": "燎原火麟",
        "element": "fire",
        "level": 40,
        "hp": 3505,
        "atk": 524,
        "defense": 236,
        "exp": 114,
        "gold": (246, 541),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.4},
            {"item": "还魂丹", "rate": 0.09},
            {"item": "渡劫丹", "rate": 0.09},
            {"item": "水之残页", "rate": 0.1},
            {"item": "土之残页", "rate": 0.1}
        ],
    },
    {
        "name": "碧海龙鲸",
        "element": "water",
        "level": 41,
        "hp": 3591,
        "atk": 538,
        "defense": 242,
        "exp": 114,
        "gold": (255, 561),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.4},
            {"item": "还魂丹", "rate": 0.09},
            {"item": "渡劫丹", "rate": 0.09},
            {"item": "水之残页", "rate": 0.1},
            {"item": "土之残页", "rate": 0.1}
        ],
    },
    {
        "name": "裂天金鹏",
        "element": "wind",
        "level": 42,
        "hp": 3676,
        "atk": 551,
        "defense": 248,
        "exp": 114,
        "gold": (265, 583),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.4},
            {"item": "还魂丹", "rate": 0.09},
            {"item": "渡劫丹", "rate": 0.09},
            {"item": "水之残页", "rate": 0.1},
            {"item": "土之残页", "rate": 0.1}
        ],
    },
    {
        "name": "幽冥判官",
        "element": "water",
        "level": 43,
        "hp": 3762,
        "atk": 563,
        "defense": 254,
        "exp": 114,
        "gold": (275, 605),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.4},
            {"item": "还魂丹", "rate": 0.09},
            {"item": "渡劫丹", "rate": 0.09},
            {"item": "水之残页", "rate": 0.1},
            {"item": "土之残页", "rate": 0.1}
        ],
    },
    {
        "name": "厚土山神",
        "element": "earth",
        "level": 44,
        "hp": 3847,
        "atk": 576,
        "defense": 260,
        "exp": 114,
        "gold": (285, 627),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.4},
            {"item": "还魂丹", "rate": 0.09},
            {"item": "渡劫丹", "rate": 0.09},
            {"item": "水之残页", "rate": 0.1},
            {"item": "土之残页", "rate": 0.1}
        ],
    },
    {
        "name": "炼虚天魔",
        "element": "fire",
        "level": 45,
        "hp": 3933,
        "atk": 589,
        "defense": 265,
        "exp": 114,
        "gold": (295, 649),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.4},
            {"item": "还魂丹", "rate": 0.09},
            {"item": "渡劫丹", "rate": 0.09},
            {"item": "水之残页", "rate": 0.1},
            {"item": "土之残页", "rate": 0.1}
        ],
    },
    {
        "name": "混沌凶兽",
        "element": "earth",

    # ---------------- Lv.46-54 炼虚期 ----------------
        "level": 46,
        "hp": 4932,
        "atk": 739,
        "defense": 333,
        "exp": 228,
        "gold": (305, 671),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.42},
            {"item": "还魂丹", "rate": 0.1},
            {"item": "渡劫丹", "rate": 0.11},
            {"item": "风之残页", "rate": 0.1},
            {"item": "火之残页", "rate": 0.1}
        ],
    },
    {
        "name": "九霄雷帝",
        "element": "fire",
        "level": 47,
        "hp": 5035,
        "atk": 755,
        "defense": 341,
        "exp": 228,
        "gold": (315, 693),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.42},
            {"item": "还魂丹", "rate": 0.1},
            {"item": "渡劫丹", "rate": 0.11},
            {"item": "风之残页", "rate": 0.1},
            {"item": "火之残页", "rate": 0.1}
        ],
    },
    {
        "name": "寒渊冰螭",
        "element": "water",
        "level": 48,
        "hp": 5143,
        "atk": 771,
        "defense": 348,
        "exp": 228,
        "gold": (325, 715),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.42},
            {"item": "还魂丹", "rate": 0.1},
            {"item": "渡劫丹", "rate": 0.11},
            {"item": "风之残页", "rate": 0.1},
            {"item": "火之残页", "rate": 0.1}
        ],
    },
    {
        "name": "天罡剑仙",
        "element": "wind",
        "level": 49,
        "hp": 5247,
        "atk": 787,
        "defense": 355,
        "exp": 228,
        "gold": (336, 739),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.42},
            {"item": "还魂丹", "rate": 0.1},
            {"item": "渡劫丹", "rate": 0.11},
            {"item": "风之残页", "rate": 0.1},
            {"item": "火之残页", "rate": 0.1}
        ],
    },
    {
        "name": "焚世火魔",
        "element": "fire",
        "level": 50,
        "hp": 5350,
        "atk": 803,
        "defense": 363,
        "exp": 228,
        "gold": (346, 761),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.42},
            {"item": "还魂丹", "rate": 0.1},
            {"item": "渡劫丹", "rate": 0.11},
            {"item": "风之残页", "rate": 0.1},
            {"item": "火之残页", "rate": 0.1}
        ],
    },
    {
        "name": "玄黄巨灵",
        "element": "earth",
        "level": 51,
        "hp": 5458,
        "atk": 819,
        "defense": 370,
        "exp": 228,
        "gold": (357, 785),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.42},
            {"item": "还魂丹", "rate": 0.1},
            {"item": "渡劫丹", "rate": 0.11},
            {"item": "风之残页", "rate": 0.1},
            {"item": "火之残页", "rate": 0.1}
        ],
    },
    {
        "name": "万魂鬼王",
        "element": "water",
        "level": 52,
        "hp": 5562,
        "atk": 835,
        "defense": 377,
        "exp": 228,
        "gold": (368, 809),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.42},
            {"item": "还魂丹", "rate": 0.1},
            {"item": "渡劫丹", "rate": 0.11},
            {"item": "风之残页", "rate": 0.1},
            {"item": "火之残页", "rate": 0.1}
        ],
    },
    {
        "name": "斩星鹏皇",
        "element": "wind",
        "level": 53,
        "hp": 5670,
        "atk": 850,
        "defense": 384,
        "exp": 228,
        "gold": (379, 833),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.42},
            {"item": "还魂丹", "rate": 0.1},
            {"item": "渡劫丹", "rate": 0.11},
            {"item": "风之残页", "rate": 0.1},
            {"item": "火之残页", "rate": 0.1}
        ],
    },
    {
        "name": "合体魔帝",
        "element": "fire",
        "level": 54,
        "hp": 5773,
        "atk": 867,
        "defense": 392,
        "exp": 228,
        "gold": (390, 858),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.42},
            {"item": "还魂丹", "rate": 0.1},
            {"item": "渡劫丹", "rate": 0.11},
            {"item": "风之残页", "rate": 0.1},
            {"item": "火之残页", "rate": 0.1}
        ],
    },
    {
        "name": "大乘佛魔",
        "element": "earth",

    # ---------------- Lv.55-63 合体期 ----------------
        "level": 55,
        "hp": 7186,
        "atk": 1079,
        "defense": 488,
        "exp": 455,
        "gold": (401, 882),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.44},
            {"item": "还魂丹", "rate": 0.11},
            {"item": "渡劫丹", "rate": 0.13},
            {"item": "金之残页", "rate": 0.11},
            {"item": "风之残页", "rate": 0.11}
        ],
    },
    {
        "name": "灭世雷劫兽",
        "element": "fire",
        "level": 56,
        "hp": 7312,
        "atk": 1098,
        "defense": 497,
        "exp": 455,
        "gold": (412, 906),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.44},
            {"item": "还魂丹", "rate": 0.11},
            {"item": "渡劫丹", "rate": 0.13},
            {"item": "金之残页", "rate": 0.11},
            {"item": "风之残页", "rate": 0.11}
        ],
    },
    {
        "name": "归墟海皇",
        "element": "water",
        "level": 57,
        "hp": 7443,
        "atk": 1118,
        "defense": 506,
        "exp": 455,
        "gold": (424, 932),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.44},
            {"item": "还魂丹", "rate": 0.11},
            {"item": "渡劫丹", "rate": 0.13},
            {"item": "金之残页", "rate": 0.11},
            {"item": "风之残页", "rate": 0.11}
        ],
    },
    {
        "name": "太初剑祖",
        "element": "wind",
        "level": 58,
        "hp": 7569,
        "atk": 1137,
        "defense": 515,
        "exp": 455,
        "gold": (435, 957),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.44},
            {"item": "还魂丹", "rate": 0.11},
            {"item": "渡劫丹", "rate": 0.13},
            {"item": "金之残页", "rate": 0.11},
            {"item": "风之残页", "rate": 0.11}
        ],
    },
    {
        "name": "焚天焱君",
        "element": "fire",
        "level": 59,
        "hp": 7699,
        "atk": 1156,
        "defense": 523,
        "exp": 455,
        "gold": (447, 983),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.44},
            {"item": "还魂丹", "rate": 0.11},
            {"item": "渡劫丹", "rate": 0.13},
            {"item": "金之残页", "rate": 0.11},
            {"item": "风之残页", "rate": 0.11}
        ],
    },
    {
        "name": "镇天神龟",
        "element": "earth",
        "level": 60,
        "hp": 7830,
        "atk": 1176,
        "defense": 532,
        "exp": 455,
        "gold": (459, 1009),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.44},
            {"item": "还魂丹", "rate": 0.11},
            {"item": "渡劫丹", "rate": 0.13},
            {"item": "金之残页", "rate": 0.11},
            {"item": "风之残页", "rate": 0.11}
        ],
    },
    {
        "name": "幽冥阎罗",
        "element": "water",
        "level": 61,
        "hp": 7956,
        "atk": 1196,
        "defense": 541,
        "exp": 455,
        "gold": (471, 1036),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.44},
            {"item": "还魂丹", "rate": 0.11},
            {"item": "渡劫丹", "rate": 0.13},
            {"item": "金之残页", "rate": 0.11},
            {"item": "风之残页", "rate": 0.11}
        ],
    },
    {
        "name": "裂穹鹏圣",
        "element": "wind",
        "level": 62,
        "hp": 8086,
        "atk": 1215,
        "defense": 550,
        "exp": 455,
        "gold": (483, 1062),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.44},
            {"item": "还魂丹", "rate": 0.11},
            {"item": "渡劫丹", "rate": 0.13},
            {"item": "金之残页", "rate": 0.11},
            {"item": "风之残页", "rate": 0.11}
        ],
    },
    {
        "name": "渡厄魔尊",
        "element": "fire",
        "level": 63,
        "hp": 8212,
        "atk": 1235,
        "defense": 559,
        "exp": 455,
        "gold": (495, 1089),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.44},
            {"item": "还魂丹", "rate": 0.11},
            {"item": "渡劫丹", "rate": 0.13},
            {"item": "金之残页", "rate": 0.11},
            {"item": "风之残页", "rate": 0.11}
        ],
    },
    {
        "name": "太虚道尊",
        "element": "earth",

    # ---------------- Lv.64-72 大乘期 ----------------
        "level": 64,
        "hp": 10111,
        "atk": 1520,
        "defense": 689,
        "exp": 910,
        "gold": (507, 1115),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.45},
            {"item": "还魂丹", "rate": 0.12},
            {"item": "渡劫丹", "rate": 0.15},
            {"item": "水之残页", "rate": 0.12},
            {"item": "土之残页", "rate": 0.12},
            {"item": "悟道石", "rate": 0.12}
        ],
    },
    {
        "name": "九天神雷兽",
        "element": "fire",
        "level": 65,
        "hp": 10269,
        "atk": 1544,
        "defense": 700,
        "exp": 910,
        "gold": (519, 1141),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.45},
            {"item": "还魂丹", "rate": 0.12},
            {"item": "渡劫丹", "rate": 0.15},
            {"item": "水之残页", "rate": 0.12},
            {"item": "土之残页", "rate": 0.12},
            {"item": "悟道石", "rate": 0.12}
        ],
    },
    {
        "name": "沧溟龙帝",
        "element": "water",
        "level": 66,
        "hp": 10422,
        "atk": 1567,
        "defense": 711,
        "exp": 910,
        "gold": (531, 1168),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.45},
            {"item": "还魂丹", "rate": 0.12},
            {"item": "渡劫丹", "rate": 0.15},
            {"item": "水之残页", "rate": 0.12},
            {"item": "土之残页", "rate": 0.12},
            {"item": "悟道石", "rate": 0.12}
        ],
    },
    {
        "name": "剑祖太一",
        "element": "wind",
        "level": 67,
        "hp": 10579,
        "atk": 1591,
        "defense": 721,
        "exp": 910,
        "gold": (544, 1196),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.45},
            {"item": "还魂丹", "rate": 0.12},
            {"item": "渡劫丹", "rate": 0.15},
            {"item": "水之残页", "rate": 0.12},
            {"item": "土之残页", "rate": 0.12},
            {"item": "悟道石", "rate": 0.12}
        ],
    },
    {
        "name": "焚天炎帝",
        "element": "fire",
        "level": 68,
        "hp": 10737,
        "atk": 1615,
        "defense": 732,
        "exp": 910,
        "gold": (556, 1223),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.45},
            {"item": "还魂丹", "rate": 0.12},
            {"item": "渡劫丹", "rate": 0.15},
            {"item": "水之残页", "rate": 0.12},
            {"item": "土之残页", "rate": 0.12},
            {"item": "悟道石", "rate": 0.12}
        ],
    },
    {
        "name": "玄武神君",
        "element": "earth",
        "level": 69,
        "hp": 10890,
        "atk": 1638,
        "defense": 743,
        "exp": 910,
        "gold": (569, 1251),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.45},
            {"item": "还魂丹", "rate": 0.12},
            {"item": "渡劫丹", "rate": 0.15},
            {"item": "水之残页", "rate": 0.12},
            {"item": "土之残页", "rate": 0.12},
            {"item": "悟道石", "rate": 0.12}
        ],
    },
    {
        "name": "十殿阎罗",
        "element": "water",
        "level": 70,
        "hp": 11047,
        "atk": 1662,
        "defense": 754,
        "exp": 910,
        "gold": (582, 1280),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.45},
            {"item": "还魂丹", "rate": 0.12},
            {"item": "渡劫丹", "rate": 0.15},
            {"item": "水之残页", "rate": 0.12},
            {"item": "土之残页", "rate": 0.12},
            {"item": "悟道石", "rate": 0.12}
        ],
    },
    {
        "name": "金翅鹏圣",
        "element": "wind",
        "level": 71,
        "hp": 11200,
        "atk": 1685,
        "defense": 765,
        "exp": 910,
        "gold": (595, 1309),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.45},
            {"item": "还魂丹", "rate": 0.12},
            {"item": "渡劫丹", "rate": 0.15},
            {"item": "水之残页", "rate": 0.12},
            {"item": "土之残页", "rate": 0.12},
            {"item": "悟道石", "rate": 0.12}
        ],
    },
    {
        "name": "天劫魔尊",
        "element": "fire",
        "level": 72,
        "hp": 11358,
        "atk": 1709,
        "defense": 775,
        "exp": 910,
        "gold": (608, 1337),
        "tier": "high",
        "drops": [
            {"item": "大回气丹", "rate": 0.45},
            {"item": "还魂丹", "rate": 0.12},
            {"item": "渡劫丹", "rate": 0.15},
            {"item": "水之残页", "rate": 0.12},
            {"item": "土之残页", "rate": 0.12},
            {"item": "悟道石", "rate": 0.12}
        ],
    },
]

# 名字 -> 怪物定义，加速查找
MONSTER_BY_NAME: dict[str, dict[str, Any]] = {m["name"]: m for m in MONSTERS}

# 按档位索引，方便 /图鉴 之类的展示
MONSTERS_BY_TIER: dict[str, list[dict[str, Any]]] = {}
for _m in MONSTERS:
    MONSTERS_BY_TIER.setdefault(_m.get("tier", "novice"), []).append(_m)


# ---------------------------------------------------------------------------
# 装备表（十品体系，v3.4 重做）
# ---------------------------------------------------------------------------
# slot: weapon / armor / accessory / boots / talisman
#
# 【v3.4 的核心变化：从「绝对值」改为「百分比」】
#
#   旧（v3.3 及以前）：面板 = 基础 × 乘区 + 装备绝对值
#                         ↑ 跨境 ×2      ↑ 固定值
#     问题：乘区随境界翻倍，装备纹丝不动 ——
#     顶级 5 件装备在炼气期给 +722%，在大乘期只剩 +5%。越后期越没用。
#
#   新（v3.4）：面板 = 基础 × 乘区 × (1 + 装备百分比)
#     装备跟着面板一起涨，任何境界都保持同一份「相对增益」。
#
# 【十品体系】
#   一品~三品 = 下品（满装 +6% / +8% / +11%）
#   四品~六品 = 中品（满装 +15% / +20% / +26%）
#   七品~九品 = 上品（满装 +33% / +41% / +50%）
#   帝兵      = 特殊装备，见 SPECIAL_EQUIPMENTS（不占这里的 5 部位槽）
#
#   每品 3 套（物穿 / 法穿 / 肉盾）× 5 部位 = 15 件，九品共 135 件。
#
#   ⚠️ 老板给的「×2 / ×2.5 / ×3」是**品阶骨架**，不是装备数值的连乘。
#      若真连乘 = 1688 倍，玩家面板会爆（境界从炼气到大乘才涨 30 倍）。
#      所以实际增益走「数值平滑」：6% → 50%（8.3 倍）。
#      高品的优势靠**机制分层**体现（无视防御 / 反伤 / 多层免伤）。
#
# 【每个字段】
#   name        显示名（品阶前缀 + 部位名，如「青锋剑」）
#   slot        部位
#   atk_pct / def_pct / hp_pct   面板百分比加成（不是绝对值！）
#   tier        品阶名（一品~九品）
#   tier_rank   品阶序号 1~9（方便排序 / 门槛判定）
#   set_id      套系标识（物穿 / 法穿 / 肉盾）—— 同套满 5 件触发套装机制
#   req_turns   穿戴门槛（转数下限）
#   ignore_def_pct / crit_bonus / magic_dmg_bonus / cd_reduce /
#   reflect_pct / low_hp_extra_dr_pct   ← 套装机制，穿满才生效
#   price       出售价（买入价 = price * 3）
#
# v2 起：加成是「普通品质（×1.0）」的基准值，
#        实际数值 = 基准 × 品质倍率，在 get_item() 里算。
# ---------------------------------------------------------------------------

EQUIPMENTS: list[dict[str, Any]] = [

    # ============================================================================
    # 一品（下品）  门槛 1 转  满装 +6%  穿甲 10%  减伤 6%
    # ============================================================================

    # --- 一品·物穿套 ---
    {"name": "青锋剑", "slot": "weapon", "atk_pct": 1.20, "tier": "一品", "tier_rank": 1, "set_id": "物穿", "req_turns": 1, "price": 100},
    {"name": "青锋战甲", "slot": "armor", "def_pct": 0.66, "hp_pct": 0.54, "tier": "一品", "tier_rank": 1, "set_id": "物穿", "req_turns": 1, "price": 100},
    {"name": "青锋战符", "slot": "accessory", "atk_pct": 0.60, "def_pct": 0.22, "hp_pct": 0.38, "tier": "一品", "tier_rank": 1, "set_id": "物穿", "req_turns": 1, "price": 100},
    {"name": "青锋战靴", "slot": "boots", "atk_pct": 0.88, "def_pct": 0.09, "hp_pct": 0.23, "tier": "一品", "tier_rank": 1, "set_id": "物穿", "req_turns": 1, "price": 100},
    {"name": "青锋战印", "slot": "talisman", "def_pct": 0.51, "hp_pct": 0.69, "tier": "一品", "tier_rank": 1, "set_id": "物穿", "req_turns": 1, "price": 100},

    # --- 一品·法穿套 ---
    {"name": "青锋杖", "slot": "weapon", "atk_pct": 1.20, "tier": "一品", "tier_rank": 1, "set_id": "法穿", "req_turns": 1, "price": 100},
    {"name": "青锋道袍", "slot": "armor", "def_pct": 0.66, "hp_pct": 0.54, "tier": "一品", "tier_rank": 1, "set_id": "法穿", "req_turns": 1, "price": 100},
    {"name": "青锋灵坠", "slot": "accessory", "atk_pct": 0.56, "def_pct": 0.24, "hp_pct": 0.40, "tier": "一品", "tier_rank": 1, "set_id": "法穿", "req_turns": 1, "price": 100},
    {"name": "青锋道履", "slot": "boots", "atk_pct": 0.85, "def_pct": 0.10, "hp_pct": 0.25, "tier": "一品", "tier_rank": 1, "set_id": "法穿", "req_turns": 1, "price": 100},
    {"name": "青锋灵印", "slot": "talisman", "def_pct": 0.51, "hp_pct": 0.69, "tier": "一品", "tier_rank": 1, "set_id": "法穿", "req_turns": 1, "price": 100},

    # --- 一品·肉盾套 ---
    {"name": "青锋重剑", "slot": "weapon", "atk_pct": 1.20, "tier": "一品", "tier_rank": 1, "set_id": "肉盾", "req_turns": 1, "price": 100},
    {"name": "青锋重铠", "slot": "armor", "def_pct": 0.72, "hp_pct": 0.48, "tier": "一品", "tier_rank": 1, "set_id": "肉盾", "req_turns": 1, "price": 100},
    {"name": "青锋护符", "slot": "accessory", "atk_pct": 0.33, "def_pct": 0.37, "hp_pct": 0.51, "tier": "一品", "tier_rank": 1, "set_id": "肉盾", "req_turns": 1, "price": 100},
    {"name": "青锋重靴", "slot": "boots", "atk_pct": 0.62, "def_pct": 0.19, "hp_pct": 0.39, "tier": "一品", "tier_rank": 1, "set_id": "肉盾", "req_turns": 1, "price": 100},
    {"name": "青锋护印", "slot": "talisman", "def_pct": 0.56, "hp_pct": 0.64, "tier": "一品", "tier_rank": 1, "set_id": "肉盾", "req_turns": 1, "price": 100},

    # ============================================================================
    # 二品（下品）  门槛 10 转  满装 +8%  穿甲 12%  减伤 7%
    # ============================================================================

    # --- 二品·物穿套 ---
    {"name": "寒铁剑", "slot": "weapon", "atk_pct": 1.60, "tier": "二品", "tier_rank": 2, "set_id": "物穿", "req_turns": 10, "price": 133},
    {"name": "寒铁战甲", "slot": "armor", "def_pct": 0.88, "hp_pct": 0.72, "tier": "二品", "tier_rank": 2, "set_id": "物穿", "req_turns": 10, "price": 133},
    {"name": "寒铁战符", "slot": "accessory", "atk_pct": 0.80, "def_pct": 0.30, "hp_pct": 0.50, "tier": "二品", "tier_rank": 2, "set_id": "物穿", "req_turns": 10, "price": 133},
    {"name": "寒铁战靴", "slot": "boots", "atk_pct": 1.18, "def_pct": 0.12, "hp_pct": 0.30, "tier": "二品", "tier_rank": 2, "set_id": "物穿", "req_turns": 10, "price": 133},
    {"name": "寒铁战印", "slot": "talisman", "def_pct": 0.67, "hp_pct": 0.93, "tier": "二品", "tier_rank": 2, "set_id": "物穿", "req_turns": 10, "price": 133},

    # --- 二品·法穿套 ---
    {"name": "寒铁杖", "slot": "weapon", "atk_pct": 1.60, "tier": "二品", "tier_rank": 2, "set_id": "法穿", "req_turns": 10, "price": 133},
    {"name": "寒铁道袍", "slot": "armor", "def_pct": 0.88, "hp_pct": 0.72, "tier": "二品", "tier_rank": 2, "set_id": "法穿", "req_turns": 10, "price": 133},
    {"name": "寒铁灵坠", "slot": "accessory", "atk_pct": 0.75, "def_pct": 0.32, "hp_pct": 0.53, "tier": "二品", "tier_rank": 2, "set_id": "法穿", "req_turns": 10, "price": 133},
    {"name": "寒铁道履", "slot": "boots", "atk_pct": 1.13, "def_pct": 0.14, "hp_pct": 0.33, "tier": "二品", "tier_rank": 2, "set_id": "法穿", "req_turns": 10, "price": 133},
    {"name": "寒铁灵印", "slot": "talisman", "def_pct": 0.68, "hp_pct": 0.92, "tier": "二品", "tier_rank": 2, "set_id": "法穿", "req_turns": 10, "price": 133},

    # --- 二品·肉盾套 ---
    {"name": "寒铁重剑", "slot": "weapon", "atk_pct": 1.60, "tier": "二品", "tier_rank": 2, "set_id": "肉盾", "req_turns": 10, "price": 133},
    {"name": "寒铁重铠", "slot": "armor", "def_pct": 0.96, "hp_pct": 0.64, "tier": "二品", "tier_rank": 2, "set_id": "肉盾", "req_turns": 10, "price": 133},
    {"name": "寒铁护符", "slot": "accessory", "atk_pct": 0.44, "def_pct": 0.49, "hp_pct": 0.68, "tier": "二品", "tier_rank": 2, "set_id": "肉盾", "req_turns": 10, "price": 133},
    {"name": "寒铁重靴", "slot": "boots", "atk_pct": 0.82, "def_pct": 0.26, "hp_pct": 0.52, "tier": "二品", "tier_rank": 2, "set_id": "肉盾", "req_turns": 10, "price": 133},
    {"name": "寒铁护印", "slot": "talisman", "def_pct": 0.75, "hp_pct": 0.85, "tier": "二品", "tier_rank": 2, "set_id": "肉盾", "req_turns": 10, "price": 133},

    # ============================================================================
    # 三品（下品）  门槛 19 转  满装 +11%  穿甲 15%  减伤 9%
    # ============================================================================

    # --- 三品·物穿套 ---
    {"name": "碧血剑", "slot": "weapon", "atk_pct": 2.20, "tier": "三品", "tier_rank": 3, "set_id": "物穿", "req_turns": 19, "price": 183},
    {"name": "碧血战甲", "slot": "armor", "def_pct": 1.21, "hp_pct": 0.99, "tier": "三品", "tier_rank": 3, "set_id": "物穿", "req_turns": 19, "price": 183},
    {"name": "碧血战符", "slot": "accessory", "atk_pct": 1.10, "def_pct": 0.41, "hp_pct": 0.69, "tier": "三品", "tier_rank": 3, "set_id": "物穿", "req_turns": 19, "price": 183},
    {"name": "碧血战靴", "slot": "boots", "atk_pct": 1.62, "def_pct": 0.17, "hp_pct": 0.41, "tier": "三品", "tier_rank": 3, "set_id": "物穿", "req_turns": 19, "price": 183},
    {"name": "碧血战印", "slot": "talisman", "def_pct": 0.93, "hp_pct": 1.27, "tier": "三品", "tier_rank": 3, "set_id": "物穿", "req_turns": 19, "price": 183},

    # --- 三品·法穿套 ---
    {"name": "碧血杖", "slot": "weapon", "atk_pct": 2.20, "tier": "三品", "tier_rank": 3, "set_id": "法穿", "req_turns": 19, "price": 183},
    {"name": "碧血道袍", "slot": "armor", "def_pct": 1.22, "hp_pct": 0.98, "tier": "三品", "tier_rank": 3, "set_id": "法穿", "req_turns": 19, "price": 183},
    {"name": "碧血灵坠", "slot": "accessory", "atk_pct": 1.03, "def_pct": 0.44, "hp_pct": 0.73, "tier": "三品", "tier_rank": 3, "set_id": "法穿", "req_turns": 19, "price": 183},
    {"name": "碧血道履", "slot": "boots", "atk_pct": 1.56, "def_pct": 0.19, "hp_pct": 0.46, "tier": "三品", "tier_rank": 3, "set_id": "法穿", "req_turns": 19, "price": 183},
    {"name": "碧血灵印", "slot": "talisman", "def_pct": 0.93, "hp_pct": 1.27, "tier": "三品", "tier_rank": 3, "set_id": "法穿", "req_turns": 19, "price": 183},

    # --- 三品·肉盾套 ---
    {"name": "碧血重剑", "slot": "weapon", "atk_pct": 2.20, "tier": "三品", "tier_rank": 3, "set_id": "肉盾", "req_turns": 19, "price": 183},
    {"name": "碧血重铠", "slot": "armor", "def_pct": 1.32, "hp_pct": 0.88, "tier": "三品", "tier_rank": 3, "set_id": "肉盾", "req_turns": 19, "price": 183},
    {"name": "碧血护符", "slot": "accessory", "atk_pct": 0.60, "def_pct": 0.67, "hp_pct": 0.93, "tier": "三品", "tier_rank": 3, "set_id": "肉盾", "req_turns": 19, "price": 183},
    {"name": "碧血重靴", "slot": "boots", "atk_pct": 1.13, "def_pct": 0.36, "hp_pct": 0.72, "tier": "三品", "tier_rank": 3, "set_id": "肉盾", "req_turns": 19, "price": 183},
    {"name": "碧血护印", "slot": "talisman", "def_pct": 1.03, "hp_pct": 1.17, "tier": "三品", "tier_rank": 3, "set_id": "肉盾", "req_turns": 19, "price": 183},

    # ============================================================================
    # 四品（中品）  门槛 28 转  满装 +15%  穿甲 19%  减伤 12%
    # ============================================================================

    # --- 四品·物穿套 ---
    {"name": "斩婴剑", "slot": "weapon", "atk_pct": 3.00, "tier": "四品", "tier_rank": 4, "set_id": "物穿", "req_turns": 28, "ignore_def_pct": 10, "price": 250},
    {"name": "斩婴战甲", "slot": "armor", "def_pct": 1.65, "hp_pct": 1.35, "tier": "四品", "tier_rank": 4, "set_id": "物穿", "req_turns": 28, "ignore_def_pct": 10, "price": 250},
    {"name": "斩婴战符", "slot": "accessory", "atk_pct": 1.51, "def_pct": 0.56, "hp_pct": 0.94, "tier": "四品", "tier_rank": 4, "set_id": "物穿", "req_turns": 28, "ignore_def_pct": 10, "price": 250},
    {"name": "斩婴战靴", "slot": "boots", "atk_pct": 2.21, "def_pct": 0.23, "hp_pct": 0.56, "tier": "四品", "tier_rank": 4, "set_id": "物穿", "req_turns": 28, "ignore_def_pct": 10, "price": 250},
    {"name": "斩婴战印", "slot": "talisman", "def_pct": 1.26, "hp_pct": 1.74, "tier": "四品", "tier_rank": 4, "set_id": "物穿", "req_turns": 28, "ignore_def_pct": 10, "price": 250},

    # --- 四品·法穿套 ---
    {"name": "斩婴杖", "slot": "weapon", "atk_pct": 3.00, "tier": "四品", "tier_rank": 4, "set_id": "法穿", "req_turns": 28, "magic_dmg_bonus": 8, "price": 250},
    {"name": "斩婴道袍", "slot": "armor", "def_pct": 1.66, "hp_pct": 1.34, "tier": "四品", "tier_rank": 4, "set_id": "法穿", "req_turns": 28, "magic_dmg_bonus": 8, "price": 250},
    {"name": "斩婴灵坠", "slot": "accessory", "atk_pct": 1.40, "def_pct": 0.60, "hp_pct": 1.00, "tier": "四品", "tier_rank": 4, "set_id": "法穿", "req_turns": 28, "magic_dmg_bonus": 8, "price": 250},
    {"name": "斩婴道履", "slot": "boots", "atk_pct": 2.12, "def_pct": 0.25, "hp_pct": 0.62, "tier": "四品", "tier_rank": 4, "set_id": "法穿", "req_turns": 28, "magic_dmg_bonus": 8, "price": 250},
    {"name": "斩婴灵印", "slot": "talisman", "def_pct": 1.27, "hp_pct": 1.73, "tier": "四品", "tier_rank": 4, "set_id": "法穿", "req_turns": 28, "magic_dmg_bonus": 8, "price": 250},

    # --- 四品·肉盾套 ---
    {"name": "斩婴重剑", "slot": "weapon", "atk_pct": 3.00, "tier": "四品", "tier_rank": 4, "set_id": "肉盾", "req_turns": 28, "reflect_pct": 8, "price": 250},
    {"name": "斩婴重铠", "slot": "armor", "def_pct": 1.80, "hp_pct": 1.20, "tier": "四品", "tier_rank": 4, "set_id": "肉盾", "req_turns": 28, "reflect_pct": 8, "price": 250},
    {"name": "斩婴护符", "slot": "accessory", "atk_pct": 0.82, "def_pct": 0.91, "hp_pct": 1.27, "tier": "四品", "tier_rank": 4, "set_id": "肉盾", "req_turns": 28, "reflect_pct": 8, "price": 250},
    {"name": "斩婴重靴", "slot": "boots", "atk_pct": 1.54, "def_pct": 0.48, "hp_pct": 0.98, "tier": "四品", "tier_rank": 4, "set_id": "肉盾", "req_turns": 28, "reflect_pct": 8, "price": 250},
    {"name": "斩婴护印", "slot": "talisman", "def_pct": 1.41, "hp_pct": 1.59, "tier": "四品", "tier_rank": 4, "set_id": "肉盾", "req_turns": 28, "reflect_pct": 8, "price": 250},

    # ============================================================================
    # 五品（中品）  门槛 37 转  满装 +20%  穿甲 23%  减伤 15%
    # ============================================================================

    # --- 五品·物穿套 ---
    {"name": "化血剑", "slot": "weapon", "atk_pct": 4.00, "tier": "五品", "tier_rank": 5, "set_id": "物穿", "req_turns": 37, "ignore_def_pct": 10, "price": 333},
    {"name": "化血战甲", "slot": "armor", "def_pct": 2.20, "hp_pct": 1.80, "tier": "五品", "tier_rank": 5, "set_id": "物穿", "req_turns": 37, "ignore_def_pct": 10, "price": 333},
    {"name": "化血战符", "slot": "accessory", "atk_pct": 2.01, "def_pct": 0.74, "hp_pct": 1.25, "tier": "五品", "tier_rank": 5, "set_id": "物穿", "req_turns": 37, "ignore_def_pct": 10, "price": 333},
    {"name": "化血战靴", "slot": "boots", "atk_pct": 2.94, "def_pct": 0.31, "hp_pct": 0.75, "tier": "五品", "tier_rank": 5, "set_id": "物穿", "req_turns": 37, "ignore_def_pct": 10, "price": 333},
    {"name": "化血战印", "slot": "talisman", "def_pct": 1.68, "hp_pct": 2.32, "tier": "五品", "tier_rank": 5, "set_id": "物穿", "req_turns": 37, "ignore_def_pct": 10, "price": 333},

    # --- 五品·法穿套 ---
    {"name": "化血杖", "slot": "weapon", "atk_pct": 4.00, "tier": "五品", "tier_rank": 5, "set_id": "法穿", "req_turns": 37, "magic_dmg_bonus": 8, "price": 333},
    {"name": "化血道袍", "slot": "armor", "def_pct": 2.21, "hp_pct": 1.79, "tier": "五品", "tier_rank": 5, "set_id": "法穿", "req_turns": 37, "magic_dmg_bonus": 8, "price": 333},
    {"name": "化血灵坠", "slot": "accessory", "atk_pct": 1.87, "def_pct": 0.80, "hp_pct": 1.33, "tier": "五品", "tier_rank": 5, "set_id": "法穿", "req_turns": 37, "magic_dmg_bonus": 8, "price": 333},
    {"name": "化血道履", "slot": "boots", "atk_pct": 2.83, "def_pct": 0.34, "hp_pct": 0.83, "tier": "五品", "tier_rank": 5, "set_id": "法穿", "req_turns": 37, "magic_dmg_bonus": 8, "price": 333},
    {"name": "化血灵印", "slot": "talisman", "def_pct": 1.69, "hp_pct": 2.31, "tier": "五品", "tier_rank": 5, "set_id": "法穿", "req_turns": 37, "magic_dmg_bonus": 8, "price": 333},

    # --- 五品·肉盾套 ---
    {"name": "化血重剑", "slot": "weapon", "atk_pct": 4.00, "tier": "五品", "tier_rank": 5, "set_id": "肉盾", "req_turns": 37, "reflect_pct": 8, "price": 333},
    {"name": "化血重铠", "slot": "armor", "def_pct": 2.40, "hp_pct": 1.60, "tier": "五品", "tier_rank": 5, "set_id": "肉盾", "req_turns": 37, "reflect_pct": 8, "price": 333},
    {"name": "化血护符", "slot": "accessory", "atk_pct": 1.09, "def_pct": 1.22, "hp_pct": 1.69, "tier": "五品", "tier_rank": 5, "set_id": "肉盾", "req_turns": 37, "reflect_pct": 8, "price": 333},
    {"name": "化血重靴", "slot": "boots", "atk_pct": 2.05, "def_pct": 0.65, "hp_pct": 1.30, "tier": "五品", "tier_rank": 5, "set_id": "肉盾", "req_turns": 37, "reflect_pct": 8, "price": 333},
    {"name": "化血护印", "slot": "talisman", "def_pct": 1.88, "hp_pct": 2.12, "tier": "五品", "tier_rank": 5, "set_id": "肉盾", "req_turns": 37, "reflect_pct": 8, "price": 333},

    # ============================================================================
    # 六品（中品）  门槛 46 转  满装 +26%  穿甲 28%  减伤 18%
    # ============================================================================

    # --- 六品·物穿套 ---
    {"name": "炼虚剑", "slot": "weapon", "atk_pct": 5.20, "tier": "六品", "tier_rank": 6, "set_id": "物穿", "req_turns": 46, "ignore_def_pct": 10, "price": 433},
    {"name": "炼虚战甲", "slot": "armor", "def_pct": 2.87, "hp_pct": 2.33, "tier": "六品", "tier_rank": 6, "set_id": "物穿", "req_turns": 46, "ignore_def_pct": 10, "price": 433},
    {"name": "炼虚战符", "slot": "accessory", "atk_pct": 2.61, "def_pct": 0.96, "hp_pct": 1.63, "tier": "六品", "tier_rank": 6, "set_id": "物穿", "req_turns": 46, "ignore_def_pct": 10, "price": 433},
    {"name": "炼虚战靴", "slot": "boots", "atk_pct": 3.82, "def_pct": 0.40, "hp_pct": 0.98, "tier": "六品", "tier_rank": 6, "set_id": "物穿", "req_turns": 46, "ignore_def_pct": 10, "price": 433},
    {"name": "炼虚战印", "slot": "talisman", "def_pct": 2.19, "hp_pct": 3.01, "tier": "六品", "tier_rank": 6, "set_id": "物穿", "req_turns": 46, "ignore_def_pct": 10, "price": 433},

    # --- 六品·法穿套 ---
    {"name": "炼虚杖", "slot": "weapon", "atk_pct": 5.20, "tier": "六品", "tier_rank": 6, "set_id": "法穿", "req_turns": 46, "magic_dmg_bonus": 8, "price": 433},
    {"name": "炼虚道袍", "slot": "armor", "def_pct": 2.87, "hp_pct": 2.33, "tier": "六品", "tier_rank": 6, "set_id": "法穿", "req_turns": 46, "magic_dmg_bonus": 8, "price": 433},
    {"name": "炼虚灵坠", "slot": "accessory", "atk_pct": 2.43, "def_pct": 1.03, "hp_pct": 1.73, "tier": "六品", "tier_rank": 6, "set_id": "法穿", "req_turns": 46, "magic_dmg_bonus": 8, "price": 433},
    {"name": "炼虚道履", "slot": "boots", "atk_pct": 3.68, "def_pct": 0.44, "hp_pct": 1.08, "tier": "六品", "tier_rank": 6, "set_id": "法穿", "req_turns": 46, "magic_dmg_bonus": 8, "price": 433},
    {"name": "炼虚灵印", "slot": "talisman", "def_pct": 2.20, "hp_pct": 3.00, "tier": "六品", "tier_rank": 6, "set_id": "法穿", "req_turns": 46, "magic_dmg_bonus": 8, "price": 433},

    # --- 六品·肉盾套 ---
    {"name": "炼虚重剑", "slot": "weapon", "atk_pct": 5.20, "tier": "六品", "tier_rank": 6, "set_id": "肉盾", "req_turns": 46, "reflect_pct": 8, "price": 433},
    {"name": "炼虚重铠", "slot": "armor", "def_pct": 3.12, "hp_pct": 2.08, "tier": "六品", "tier_rank": 6, "set_id": "肉盾", "req_turns": 46, "reflect_pct": 8, "price": 433},
    {"name": "炼虚护符", "slot": "accessory", "atk_pct": 1.42, "def_pct": 1.58, "hp_pct": 2.19, "tier": "六品", "tier_rank": 6, "set_id": "肉盾", "req_turns": 46, "reflect_pct": 8, "price": 433},
    {"name": "炼虚重靴", "slot": "boots", "atk_pct": 2.67, "def_pct": 0.84, "hp_pct": 1.69, "tier": "六品", "tier_rank": 6, "set_id": "肉盾", "req_turns": 46, "reflect_pct": 8, "price": 433},
    {"name": "炼虚护印", "slot": "talisman", "def_pct": 2.44, "hp_pct": 2.76, "tier": "六品", "tier_rank": 6, "set_id": "肉盾", "req_turns": 46, "reflect_pct": 8, "price": 433},

    # ============================================================================
    # 七品（上品）  门槛 55 转  满装 +33%  穿甲 33%  减伤 22%
    # ============================================================================

    # --- 七品·物穿套 ---
    {"name": "七杀剑", "slot": "weapon", "atk_pct": 6.60, "tier": "七品", "tier_rank": 7, "set_id": "物穿", "req_turns": 55, "ignore_def_pct": 18, "crit_bonus": 15, "price": 550},
    {"name": "七杀战甲", "slot": "armor", "def_pct": 3.64, "hp_pct": 2.96, "tier": "七品", "tier_rank": 7, "set_id": "物穿", "req_turns": 55, "ignore_def_pct": 18, "crit_bonus": 15, "price": 550},
    {"name": "七杀战符", "slot": "accessory", "atk_pct": 3.31, "def_pct": 1.22, "hp_pct": 2.06, "tier": "七品", "tier_rank": 7, "set_id": "物穿", "req_turns": 55, "ignore_def_pct": 18, "crit_bonus": 15, "price": 550},
    {"name": "七杀战靴", "slot": "boots", "atk_pct": 4.85, "def_pct": 0.51, "hp_pct": 1.24, "tier": "七品", "tier_rank": 7, "set_id": "物穿", "req_turns": 55, "ignore_def_pct": 18, "crit_bonus": 15, "price": 550},
    {"name": "七杀战印", "slot": "talisman", "def_pct": 2.78, "hp_pct": 3.82, "tier": "七品", "tier_rank": 7, "set_id": "物穿", "req_turns": 55, "ignore_def_pct": 18, "crit_bonus": 15, "price": 550},

    # --- 七品·法穿套 ---
    {"name": "七杀杖", "slot": "weapon", "atk_pct": 6.60, "tier": "七品", "tier_rank": 7, "set_id": "法穿", "req_turns": 55, "magic_dmg_bonus": 20, "cd_reduce": 1, "price": 550},
    {"name": "七杀道袍", "slot": "armor", "def_pct": 3.65, "hp_pct": 2.95, "tier": "七品", "tier_rank": 7, "set_id": "法穿", "req_turns": 55, "magic_dmg_bonus": 20, "cd_reduce": 1, "price": 550},
    {"name": "七杀灵坠", "slot": "accessory", "atk_pct": 3.09, "def_pct": 1.31, "hp_pct": 2.20, "tier": "七品", "tier_rank": 7, "set_id": "法穿", "req_turns": 55, "magic_dmg_bonus": 20, "cd_reduce": 1, "price": 550},
    {"name": "七杀道履", "slot": "boots", "atk_pct": 4.67, "def_pct": 0.56, "hp_pct": 1.37, "tier": "七品", "tier_rank": 7, "set_id": "法穿", "req_turns": 55, "magic_dmg_bonus": 20, "cd_reduce": 1, "price": 550},
    {"name": "七杀灵印", "slot": "talisman", "def_pct": 2.79, "hp_pct": 3.81, "tier": "七品", "tier_rank": 7, "set_id": "法穿", "req_turns": 55, "magic_dmg_bonus": 20, "cd_reduce": 1, "price": 550},

    # --- 七品·肉盾套 ---
    {"name": "七杀重剑", "slot": "weapon", "atk_pct": 6.60, "tier": "七品", "tier_rank": 7, "set_id": "肉盾", "req_turns": 55, "reflect_pct": 18, "low_hp_extra_dr_pct": 15, "price": 550},
    {"name": "七杀重铠", "slot": "armor", "def_pct": 3.96, "hp_pct": 2.64, "tier": "七品", "tier_rank": 7, "set_id": "肉盾", "req_turns": 55, "reflect_pct": 18, "low_hp_extra_dr_pct": 15, "price": 550},
    {"name": "七杀护符", "slot": "accessory", "atk_pct": 1.80, "def_pct": 2.01, "hp_pct": 2.78, "tier": "七品", "tier_rank": 7, "set_id": "肉盾", "req_turns": 55, "reflect_pct": 18, "low_hp_extra_dr_pct": 15, "price": 550},
    {"name": "七杀重靴", "slot": "boots", "atk_pct": 3.39, "def_pct": 1.07, "hp_pct": 2.15, "tier": "七品", "tier_rank": 7, "set_id": "肉盾", "req_turns": 55, "reflect_pct": 18, "low_hp_extra_dr_pct": 15, "price": 550},
    {"name": "七杀护印", "slot": "talisman", "def_pct": 3.10, "hp_pct": 3.50, "tier": "七品", "tier_rank": 7, "set_id": "肉盾", "req_turns": 55, "reflect_pct": 18, "low_hp_extra_dr_pct": 15, "price": 550},

    # ============================================================================
    # 八品（上品）  门槛 64 转  满装 +41%  穿甲 38%  减伤 26%
    # ============================================================================

    # --- 八品·物穿套 ---
    {"name": "八荒剑", "slot": "weapon", "atk_pct": 8.20, "tier": "八品", "tier_rank": 8, "set_id": "物穿", "req_turns": 64, "ignore_def_pct": 18, "crit_bonus": 15, "price": 683},
    {"name": "八荒战甲", "slot": "armor", "def_pct": 4.52, "hp_pct": 3.68, "tier": "八品", "tier_rank": 8, "set_id": "物穿", "req_turns": 64, "ignore_def_pct": 18, "crit_bonus": 15, "price": 683},
    {"name": "八荒战符", "slot": "accessory", "atk_pct": 4.12, "def_pct": 1.52, "hp_pct": 2.56, "tier": "八品", "tier_rank": 8, "set_id": "物穿", "req_turns": 64, "ignore_def_pct": 18, "crit_bonus": 15, "price": 683},
    {"name": "八荒战靴", "slot": "boots", "atk_pct": 6.03, "def_pct": 0.63, "hp_pct": 1.54, "tier": "八品", "tier_rank": 8, "set_id": "物穿", "req_turns": 64, "ignore_def_pct": 18, "crit_bonus": 15, "price": 683},
    {"name": "八荒战印", "slot": "talisman", "def_pct": 3.45, "hp_pct": 4.75, "tier": "八品", "tier_rank": 8, "set_id": "物穿", "req_turns": 64, "ignore_def_pct": 18, "crit_bonus": 15, "price": 683},

    # --- 八品·法穿套 ---
    {"name": "八荒杖", "slot": "weapon", "atk_pct": 8.20, "tier": "八品", "tier_rank": 8, "set_id": "法穿", "req_turns": 64, "magic_dmg_bonus": 20, "cd_reduce": 1, "price": 683},
    {"name": "八荒道袍", "slot": "armor", "def_pct": 4.53, "hp_pct": 3.67, "tier": "八品", "tier_rank": 8, "set_id": "法穿", "req_turns": 64, "magic_dmg_bonus": 20, "cd_reduce": 1, "price": 683},
    {"name": "八荒灵坠", "slot": "accessory", "atk_pct": 3.84, "def_pct": 1.63, "hp_pct": 2.73, "tier": "八品", "tier_rank": 8, "set_id": "法穿", "req_turns": 64, "magic_dmg_bonus": 20, "cd_reduce": 1, "price": 683},
    {"name": "八荒道履", "slot": "boots", "atk_pct": 5.81, "def_pct": 0.70, "hp_pct": 1.70, "tier": "八品", "tier_rank": 8, "set_id": "法穿", "req_turns": 64, "magic_dmg_bonus": 20, "cd_reduce": 1, "price": 683},
    {"name": "八荒灵印", "slot": "talisman", "def_pct": 3.47, "hp_pct": 4.73, "tier": "八品", "tier_rank": 8, "set_id": "法穿", "req_turns": 64, "magic_dmg_bonus": 20, "cd_reduce": 1, "price": 683},

    # --- 八品·肉盾套 ---
    {"name": "八荒重剑", "slot": "weapon", "atk_pct": 8.20, "tier": "八品", "tier_rank": 8, "set_id": "肉盾", "req_turns": 64, "reflect_pct": 18, "low_hp_extra_dr_pct": 15, "price": 683},
    {"name": "八荒重铠", "slot": "armor", "def_pct": 4.91, "hp_pct": 3.29, "tier": "八品", "tier_rank": 8, "set_id": "肉盾", "req_turns": 64, "reflect_pct": 18, "low_hp_extra_dr_pct": 15, "price": 683},
    {"name": "八荒护符", "slot": "accessory", "atk_pct": 2.24, "def_pct": 2.50, "hp_pct": 3.46, "tier": "八品", "tier_rank": 8, "set_id": "肉盾", "req_turns": 64, "reflect_pct": 18, "low_hp_extra_dr_pct": 15, "price": 683},
    {"name": "八荒重靴", "slot": "boots", "atk_pct": 4.21, "def_pct": 1.32, "hp_pct": 2.67, "tier": "八品", "tier_rank": 8, "set_id": "肉盾", "req_turns": 64, "reflect_pct": 18, "low_hp_extra_dr_pct": 15, "price": 683},
    {"name": "八荒护印", "slot": "talisman", "def_pct": 3.85, "hp_pct": 4.35, "tier": "八品", "tier_rank": 8, "set_id": "肉盾", "req_turns": 64, "reflect_pct": 18, "low_hp_extra_dr_pct": 15, "price": 683},

    # ============================================================================
    # 九品（上品）  门槛 68 转  满装 +50%  穿甲 45%  减伤 30%
    # ============================================================================

    # --- 九品·物穿套 ---
    {"name": "九霄剑", "slot": "weapon", "atk_pct": 10.00, "tier": "九品", "tier_rank": 9, "set_id": "物穿", "req_turns": 68, "ignore_def_pct": 18, "crit_bonus": 15, "price": 833},
    {"name": "九霄战甲", "slot": "armor", "def_pct": 5.51, "hp_pct": 4.49, "tier": "九品", "tier_rank": 9, "set_id": "物穿", "req_turns": 68, "ignore_def_pct": 18, "crit_bonus": 15, "price": 833},
    {"name": "九霄战符", "slot": "accessory", "atk_pct": 5.02, "def_pct": 1.85, "hp_pct": 3.13, "tier": "九品", "tier_rank": 9, "set_id": "物穿", "req_turns": 68, "ignore_def_pct": 18, "crit_bonus": 15, "price": 833},
    {"name": "九霄战靴", "slot": "boots", "atk_pct": 7.35, "def_pct": 0.77, "hp_pct": 1.88, "tier": "九品", "tier_rank": 9, "set_id": "物穿", "req_turns": 68, "ignore_def_pct": 18, "crit_bonus": 15, "price": 833},
    {"name": "九霄战印", "slot": "talisman", "def_pct": 4.21, "hp_pct": 5.79, "tier": "九品", "tier_rank": 9, "set_id": "物穿", "req_turns": 68, "ignore_def_pct": 18, "crit_bonus": 15, "price": 833},

    # --- 九品·法穿套 ---
    {"name": "九霄杖", "slot": "weapon", "atk_pct": 10.00, "tier": "九品", "tier_rank": 9, "set_id": "法穿", "req_turns": 68, "magic_dmg_bonus": 20, "cd_reduce": 1, "price": 833},
    {"name": "九霄道袍", "slot": "armor", "def_pct": 5.53, "hp_pct": 4.47, "tier": "九品", "tier_rank": 9, "set_id": "法穿", "req_turns": 68, "magic_dmg_bonus": 20, "cd_reduce": 1, "price": 833},
    {"name": "九霄灵坠", "slot": "accessory", "atk_pct": 4.68, "def_pct": 1.99, "hp_pct": 3.33, "tier": "九品", "tier_rank": 9, "set_id": "法穿", "req_turns": 68, "magic_dmg_bonus": 20, "cd_reduce": 1, "price": 833},
    {"name": "九霄道履", "slot": "boots", "atk_pct": 7.08, "def_pct": 0.85, "hp_pct": 2.07, "tier": "九品", "tier_rank": 9, "set_id": "法穿", "req_turns": 68, "magic_dmg_bonus": 20, "cd_reduce": 1, "price": 833},
    {"name": "九霄灵印", "slot": "talisman", "def_pct": 4.23, "hp_pct": 5.77, "tier": "九品", "tier_rank": 9, "set_id": "法穿", "req_turns": 68, "magic_dmg_bonus": 20, "cd_reduce": 1, "price": 833},

    # --- 九品·肉盾套 ---
    {"name": "九霄重剑", "slot": "weapon", "atk_pct": 10.00, "tier": "九品", "tier_rank": 9, "set_id": "肉盾", "req_turns": 68, "reflect_pct": 18, "low_hp_extra_dr_pct": 15, "price": 833},
    {"name": "九霄重铠", "slot": "armor", "def_pct": 5.99, "hp_pct": 4.01, "tier": "九品", "tier_rank": 9, "set_id": "肉盾", "req_turns": 68, "reflect_pct": 18, "low_hp_extra_dr_pct": 15, "price": 833},
    {"name": "九霄护符", "slot": "accessory", "atk_pct": 2.73, "def_pct": 3.05, "hp_pct": 4.22, "tier": "九品", "tier_rank": 9, "set_id": "肉盾", "req_turns": 68, "reflect_pct": 18, "low_hp_extra_dr_pct": 15, "price": 833},
    {"name": "九霄重靴", "slot": "boots", "atk_pct": 5.14, "def_pct": 1.61, "hp_pct": 3.25, "tier": "九品", "tier_rank": 9, "set_id": "肉盾", "req_turns": 68, "reflect_pct": 18, "low_hp_extra_dr_pct": 15, "price": 833},
    {"name": "九霄护印", "slot": "talisman", "def_pct": 4.70, "hp_pct": 5.30, "tier": "九品", "tier_rank": 9, "set_id": "肉盾", "req_turns": 68, "reflect_pct": 18, "low_hp_extra_dr_pct": 15, "price": 833},
]

# ========================================================================
# 特殊装备（不占 5 部位槽，独立【特殊】栏）
# ========================================================================
#
# 【为什么是"类别"而不是"一件"】
# 帝兵不是写死的唯一物，而是这个表里的**第一条记录**。
# 以后加新特殊装备（活动限定 / 天道赐福 / 老板新定制）都只是往表里加一条。
#
# 【字段说明】
#   id        唯一标识。**存档按 id 存，不按 name** —— 名字改了存档不炸。
#   name      显示名，可随时改。
#   rarity    legend（传说）/ myth（神话）
#   base_pct  基础全属性加成（%）。帝兵 = 62，对齐九品满装的量级。
#   passives  被动列表。每项 {"kind": <类型>, ...参数}
#             支持的 kind 见 SPECIAL_PASSIVE_KINDS
#   owner     专属归属。"" = 全服共享；填玩家名 = 该玩家专属。
#   source    产出途径：lottery / event / custom
#   unique    是否全服唯一
#
# 【帝兵定位（老板 2026-09-19 口述）】
#   * 无阶位限制（req_turns 不存在这个字段 → 任何转数都能装）
#   * 全服限定，由老板专属定制
#   * 基础约等于七品属性，但有**成长型被动**：每击杀一只妖兽 +1% 全属性
#   * 死亡不掉层
#   * 产出走天道抽奖等活动（框架已留，未开放）
#   * 具体成长曲线"天机不可泄露"—— 所以参数全部可配，不写死
SPECIAL_EQUIPMENTS: list[dict[str, Any]] = [
    {
        "id": "dibing_000",
        "name": "帝兵",
        "rarity": "myth",
        # 【v3.5 数值下调】62.0 -> 20.0
        #
        # 原值 62% 是**为「全服唯一」设计的** —— 一个人独享，破坏平衡无所谓，
        # 那正是唯一物的价值来源。老板后来决定「帝兵不搞唯一了，全民可获取」，
        # 于是 62% + 成长 150% = 满成长 +212%（面板 ×3.12）会变成
        # **所有玩家面板 ×3.12** —— 135 件装备、三套流派、九个品阶的梯度
        # 全部失去意义（穿不穿都一样，反正帝兵给 3 倍）。
        #
        # 这是设计硬冲突，不是调参选择：**去唯一化和原数值，二者只能留一个。**
        #
        # 新值对齐「九品满装」这把尺子（实测 5 件累加 atk_pct 22.37/21.76/17.87
        # → 整套约 ×1.22）：
        #   帝兵满成长 ×1.50  ÷  一套九品 ×1.22  ≈  1.23
        # 即「一件帝兵 ≈ 1.2 套九品装备」—— 作为 100 天签到 + 每天打秘境的
        # 长期目标，明显强于任何单件/单套，但不至于让装备体系失去意义。
        "base_pct": 20.0,
        "passives": [
            # 成长型被动：每击杀 +pct% 全属性，封顶 cap_pct
            # curve="linear" 全程同速率；将来可加 "diminish" 递减 / "log" 对数
            #
            # 【v3.5 数值下调】pct 1.0 -> 0.1，cap_pct 150 -> 30
            #
            # 原值 +1%/封顶 150% 在「唯一」时代是「大佬的勋章」（一辈子也满不了）。
            # 全民化之后，按挂机击杀速度**几天就满了** —— 那「成长型被动」
            # 这个卖点就变成一次性的了。
            # 改 0.1%/30% → 需 300 只怪，活跃玩家约 1~2 周满，是条能看见的曲线。
            {"kind": "grow_on_kill", "pct": 0.1, "cap_pct": 30.0,
             "curve": "linear", "lose_on_death": False},
        ],
        # owner 字段**保留但停用**：去唯一化后不再有「谁先兑到就归谁」的概念。
        # 结构留着无害（老存档里可能已有值），但展示层不再读它。
        "owner": "",
        # 来源：抽奖 -> 天道令兑换（100 枚）
        "source": "token_exchange",
        "unique": False,
    },
]

# 特殊装备被动类型登记表 —— 加新被动只需在这里登记 + 在 battle 里写处理器
SPECIAL_PASSIVE_KINDS: dict[str, str] = {
"grow_on_kill": "每击杀一只妖兽，永久累积全属性加成",
# 预留（未实装，先占位，加的时候不会忘）：
# "grow_on_dungeon": "每通过一层秘境，累积加成",
# "revive_once":     "战斗中首次气血归零时复活一次",
# "qi_boost":        "修炼 / 闭关收益提升",
# "tribulation_aid": "渡劫时额外抗 1 道雷",
}
# 基础名 -> 定义
EQUIPMENT_BY_NAME: dict[str, dict[str, Any]] = {e["name"]: e for e in EQUIPMENTS}

# 特殊装备索引。**按 id 查**（存档用 id），也支持按 name 查（指令里好用）。
SPECIAL_EQUIPMENT_BY_ID: dict[str, dict[str, Any]] = {
    s["id"]: s for s in SPECIAL_EQUIPMENTS
}
SPECIAL_EQUIPMENT_BY_NAME: dict[str, dict[str, Any]] = {
    s["name"]: s for s in SPECIAL_EQUIPMENTS
}


def get_special(key: str) -> dict[str, Any] | None:
    """按 id 或名字取特殊装备定义。两者都查不到返回 None。"""
    return SPECIAL_EQUIPMENT_BY_ID.get(key) or SPECIAL_EQUIPMENT_BY_NAME.get(key)


def is_special_equipment(key: str) -> bool:
    return key in SPECIAL_EQUIPMENT_BY_ID or key in SPECIAL_EQUIPMENT_BY_NAME


# ---------------------------------------------------------------------------
# 套系判定（v3.4）
# ---------------------------------------------------------------------------
#
# 套装机制「穿满 5 件同 set_id 才生效」—— 注意是**按套系 id**，
# 不是按 v3.1 的「任意 5 件」。这样三套才互不串味：
# 穿 3 件物穿 + 2 件肉盾 = 不触发任何套装机制（只有各自的百分比面板）。
#
# 设计意图：让玩家**选一套穿齐**，而不是「哪件属性高穿哪件」。
# 这是「套装」这个概念能立住的前提。

SET_IDS: tuple[str, ...] = ("物穿", "法穿", "肉盾")
SET_FULL_COUNT = 5     # 穿满几件触发套装机制


def set_id_of(item_name: str) -> str:
    """取某装备所属套系 id。非装备 / 无套系的返回空串。"""
    base, _ = split_quality(item_name)
    base = resolve_alias(base)
    eq = EQUIPMENT_BY_NAME.get(base)
    if eq:
        return eq.get("set_id", "")
    return ""


def active_set_id(equipment: dict[str, str]) -> str:
    """给定「部位 -> 装备名」映射，返回**已穿满 5 件**的套系 id。

    没穿满 / 混搭 / 空 -> 返回空串。

    为什么要「同一套穿满」而不是「凑够 5 件就触发」：
    三套各有明确的功能定位（物穿=破防、法穿=法术、肉盾=生存），
    混搭触发会让套装失去辨识度，玩家也没法通过换装调整打法。
    """
    if not equipment:
        return ""
    counts: dict[str, int] = {}
    for item_name in equipment.values():
        sid = set_id_of(item_name)
        if sid:
            counts[sid] = counts.get(sid, 0) + 1
    for sid in SET_IDS:
        if counts.get(sid, 0) >= SET_FULL_COUNT:
            return sid
    return ""


def set_mech_value(equipment: dict[str, str], key: str) -> float:
    """取当前生效套装的某个机制数值。没穿满返回 0。

    例：set_mech_value(player.equipment, "ignore_def_pct") -> 18.0
    """
    sid = active_set_id(equipment)
    if not sid:
        return 0.0
    for name in equipment.values():
        base, _ = split_quality(name)
        base = resolve_alias(base)
        eq = EQUIPMENT_BY_NAME.get(base)
        if eq and eq.get("set_id") == sid and key in eq:
            return float(eq[key])
    return 0.0


def tier_rank_of(item_name: str) -> int:
    """取装备的品阶序号（1~9）。非装备返回 0。"""
    base, _ = split_quality(item_name)
    base = resolve_alias(base)
    eq = EQUIPMENT_BY_NAME.get(base)
    if eq:
        return int(eq.get("tier_rank", 0))
    return 0


def req_turns_of(item_name: str) -> int:
    """取装备的穿戴门槛（转数）。没有门槛（一品）返回 1。"""
    base, _ = split_quality(item_name)
    base = resolve_alias(base)
    eq = EQUIPMENT_BY_NAME.get(base)
    if eq:
        return int(eq.get("req_turns", 1))
    return 0


# ---------------------------------------------------------------------------
# 套装数值定稿表（v3.4）
# ---------------------------------------------------------------------------
#
# 【为什么单独列表，而不是只从装备字段读】
# 装备表里也挂了这些值，但那是"方便展示"。真正计算时从这里查 ——
# 这样即使将来装备表漏填某一件，数值也不会莫名变 0，
# 而且调平衡时只改一张表，不用动 135 条记录。
#
# 数值来源：《十品装备体系_定稿.md》第二节

# 按品阶序号（1~9）查套装穿甲（物穿套）
SET_PENETRATION_BY_RANK: dict[int, float] = {
    1: 0.10, 2: 0.12, 3: 0.15,
    4: 0.19, 5: 0.23, 6: 0.28,
    7: 0.33, 8: 0.38, 9: 0.45,
}
# 法穿套的穿甲倍率。
#
# 【2026-09-19 v3.5：1.15 -> 1.0】
# 原值 1.15 的理由是「魔法本来就偏穿透」—— 这是个没有依据的特权，
# 导致法穿套穿甲 51.7% vs 物穿 45%，且穿甲在伤害公式里是
# `eff_def = def × (1 - pen)`（减绝对值），会把面板增益一起放大。
#
# 实测（合成口径，九品极差）：1.15 -> 10.6pp（配合折率 0.1）；
# 归 1.0 后三套穿甲统一 45%，规则更好解释，极差降到 7.3pp。
SET_FPEN_MULT = 1.0

# 按品阶序号查肉盾套的受伤减免
SET_DR_BY_RANK: dict[int, float] = {
    1: 0.06, 2: 0.07, 3: 0.09,
    4: 0.12, 5: 0.15, 6: 0.18,
    7: 0.22, 8: 0.26, 9: 0.30,
}


# ---------------------------------------------------------------------------
# 套系展示名（v3.5）
# ---------------------------------------------------------------------------
#
# 内部 id（"物穿"/"法穿"/"肉盾"）是**功能名**，直接暴露机制，没有修仙味。
# 这里只做**展示层**映射 —— `set_id` 一个字都不能改，
# 因为存档里装备是按 set_id 匹配套系的（`Player.active_set()`），
# 改了 = 线上所有玩家的套装当场失效。
#
# 【为什么不用「剑修 / 法修 / 体修」】
# 老板定的：那三个是**职业名**，留给「创建角色时选择职业」用。
# 套系是**装备流派**，跟职业是两回事：
#     职业 = 玩家是谁        （未来功能，创建角色时选）
#     套系 = 这套装备走什么路数（现在就有）
# 所以套系另起一组，同样有修仙味，但语义落在「装备的风格」上：
#
#     破锋 —— 破敌之锋    穿甲 / 破防 / 暴击
#     通玄 —— 通达玄妙    术法增伤 / 冷却缩减
#     镇岳 —— 镇压山岳    减伤 / 反伤 / 厚实
#
# 三个都是「动词+名词」同构，玩家一眼能猜出定位；
# 且不会和装备名（青锋剑、斩婴杖……那些是器物名）混淆。
SET_DISPLAY_NAMES: dict[str, str] = {
    "物穿": "破锋",
    "法穿": "通玄",
    "肉盾": "镇岳",
}


def set_display_name(set_id: str) -> str:
    """内部套系 id 翻成给玩家看的名字。未知 id 原样返回。"""
    if not set_id:
        return ""
    return SET_DISPLAY_NAMES.get(set_id, set_id)


# ---------------------------------------------------------------------------
# 品阶穿戴门槛
# ---------------------------------------------------------------------------

def can_equip(item_name: str, turns: int) -> bool:
    """判断某转数的玩家能否穿戴这件装备（v3.4 新增）。

    规则：玩家总转数 >= 装备的 req_turns。
    特殊装备（帝兵）没有 req_turns 字段 -> 任何转数都能装（老板要求「无阶位限制」）。
    """
    base, _ = split_quality(item_name)
    base = resolve_alias(base)

    # 特殊装备：无门槛
    if is_special_equipment(base):
        return True

    req = req_turns_of(item_name)
    if req <= 0:
        return True
    return int(turns) >= req


def equip_deny_reason(item_name: str, turns: int) -> str:
    """返回不能穿戴的原因（能穿则返回空串）。给指令提示用。"""
    if can_equip(item_name, turns):
        return ""
    req = req_turns_of(item_name)
    return f"需要 {req} 转才能祭炼（你当前 {turns} 转）"


# ---------------------------------------------------------------------------
# 消耗品表（v3 修仙化：丹药）
# ---------------------------------------------------------------------------
# effect: heal 回气血 / exp 加修为 / buff 战斗增益 / revive 免休整复活
#         tribulation 渡劫专属（渡劫时额外扛雷）
# value:  效果数值
# price:  出售价
#
# 命名映射（v2 -> v3），旧名保留为别名，老存档不受影响：
#   小型生命药水 -> 小回气丹
#   中型生命药水 -> 中回气丹
#   大型生命药水 -> 大回气丹
#   经验卷轴     -> 悟道石
#   复活符       -> 还魂丹
#   体力药剂     -> 培元丹
# ---------------------------------------------------------------------------

CONSUMABLES: list[dict[str, Any]] = [
    # 回气血
    {"name": "史莱姆凝胶", "effect": "heal", "value": 15, "price": 5},
    {"name": "小回气丹", "effect": "heal", "value": 40, "price": 10},
    {"name": "中回气丹", "effect": "heal", "value": 120, "price": 30},
    {"name": "大回气丹", "effect": "heal", "value": 300, "price": 80},
    {"name": "培元丹", "effect": "heal", "value": 200, "price": 55},
    # 增益（下场战斗生效）
    {"name": "力量药剂", "effect": "buff_atk", "value": 20, "price": 45},
    {"name": "铁壁药剂", "effect": "buff_def", "value": 30, "price": 45},
    {"name": "迅捷药剂", "effect": "buff_speed", "value": 1, "price": 60},
    {"name": "幸运符", "effect": "buff_luck", "value": 100, "price": 90},
    # 特殊
    {"name": "解毒剂", "effect": "cure", "value": 1, "price": 25},
    {"name": "悟道石", "effect": "exp", "value": 100, "price": 120},
    {"name": "还魂丹", "effect": "revive", "value": 1, "price": 150},
    # 渡劫专属：渡天劫时每一颗能多扛一道雷
    {"name": "渡劫丹", "effect": "tribulation", "value": 1, "price": 400},
    # 天道令（v3.5）：帝兵兑换凭证，攒 100 枚换一件极品帝兵。
    #
    # 产出只有两条路：每日 /签到 +1、秘境「整趟有首通」+1。
    # （VIP 赞助者另有每日额外加成，见 vip.py）
    #
    # effect="token" 是刻意的，不是随便起的名字 —— 它带来两条硬约束：
    #   1. **不能被 /使用 服用**（否则玩家会以为它是丹药）
    #   2. **不能被 /出售 换灵石**（100 枚 = 一件帝兵，误卖一枚就是白攒一天）
    # price=0 是双保险：就算拦截漏了，卖掉也是 0 灵石，玩家会立刻发现不对。
    {"name": "天道令", "effect": "token", "value": 1, "price": 0},
]

CONSUMABLE_BY_NAME: dict[str, dict[str, Any]] = {c["name"]: c for c in CONSUMABLES}

# ---------------------------------------------------------------------------
# 秘典残页（v3 新增）
# ---------------------------------------------------------------------------
# 残页是「修行大道」的消耗品：/修行 火之秘典 会扣 火之残页 × N。
# 定义成消耗品是为了复用背包、出售、掉落这一整套现成机制，
# 但 effect 用 "relic" 标记，避免被 /使用 误用。
# ---------------------------------------------------------------------------

RELIC_PRICE = 200  # 残页的出售价

for _relic in ("火之残页", "水之残页", "金之残页", "土之残页", "风之残页"):
    _entry = {
        "name": _relic,
        "effect": "relic",
        "value": 1,
        "price": RELIC_PRICE,
    }
    CONSUMABLES.append(_entry)
    CONSUMABLE_BY_NAME[_relic] = _entry

# ---------------------------------------------------------------------------
# 旧名 -> 新名。老存档背包里的旧物品名读进来时自动映射到新定义，
# 玩家的东西不会因为改名而消失。
# ---------------------------------------------------------------------------
ITEM_ALIASES: dict[str, str] = {
    "小型生命药水": "小回气丹",
    "中型生命药水": "中回气丹",
    "大型生命药水": "大回气丹",
    "体力药剂": "培元丹",
    "经验卷轴": "悟道石",
    "复活符": "还魂丹",

    # ---------------- 旧装备名 -> 十品体系（v3.4）----------------
    #
    # 【为什么要做这个映射】
    # v3.4 把 42 件旧装备整体换成了「十品 × 3 套 × 5 部位 = 135 件」。
    # 旧名不映射的话会有两个后果：
    #   1. 老存档背包 / 已装备栏里的旧装备**查不到定义** → 显示为空、属性丢失；
    #   2. 自测里 30 处引用旧名（多为「随便拿件低阶装备测穿戴逻辑」）会大面积失败。
    #
    # 【映射原则】
    # 按「旧装备的强度」排出它在新体系里的位置，**保持部位不变**：
    #   旧武器 -> 新武器，旧护甲 -> 新护甲，等等。
    # 这样老玩家穿在身上的东西，改名后仍是同一个部位、同一档强度。
    #
    # 新手档（木剑/布甲/草鞋/桃木符）→ 一品
    # 中档（铁剑/皮甲/疾行靴/铜镜）  → 二品
    # 高档（龙牙巨剑/龙鳞甲等）      → 四~六品
    #
    # 老玩家不会因为这次重做而变弱或变强 —— 只是名字换了。
    "木剑": "青锋剑",
    "铁剑": "寒铁剑",
    "藤蔓长鞭": "碧血剑",
    "蝎尾毒针": "斩婴剑",
    "骨制长剑": "化血剑",
    "暗影匕首": "炼虚剑",
    "深渊獠牙": "七杀剑",
    "奥术之杖": "八荒杖",
    "龙牙巨剑": "九霄剑",
    "混沌之刃": "九霄剑",

    "布甲": "青锋战甲",
    "狼皮护甲": "寒铁战甲",
    "皮甲": "寒铁战甲",
    "铁质胸甲": "碧血战甲",
    "藤蔓护甲": "斩婴战甲",
    "暗影披风": "化血战甲",
    "熔岩护腕": "炼虚战甲",
    "魔像重甲": "七杀重铠",
    "法师长袍": "八荒道袍",
    "霜冻重铠": "九霄重铠",
    "龙鳞甲": "九霄重铠",
    "神魔战甲": "九霄重铠",

    "狼牙": "青锋战符",
    "铜戒": "寒铁战符",
    "石之心": "碧血战符",
    "生命晶石": "斩婴战符",
    "迅捷之靴": "化血战靴",
    "巨龙之心": "炼虚战符",
    "永恒之心": "七杀战符",
    "混沌护符": "九霄护符",

    "草鞋": "青锋战靴",
    "疾行靴": "寒铁战靴",
    "踏云履": "碧血道履",
    "追风战靴": "斩婴战靴",
    "凌云踏虚靴": "化血战靴",
    "九霄流光靴": "九霄战靴",

    "桃木符": "青锋战印",
    "铜镜": "寒铁战印",
    "玉髓法印": "碧血灵印",
    "镇魂玉印": "斩婴战印",
    "太虚玄印": "化血战印",
    "混沌道印": "九霄护印",
}


def resolve_alias(name: str) -> str:
    """把旧物品名映射到新名。未知名字原样返回。"""
    return ITEM_ALIASES.get(name, name)

# 回气血类物品名（死亡时可以用它们立即恢复，按价格从低到高尝试）
HEAL_ITEMS: list[str] = [
    c["name"] for c in CONSUMABLES if c["effect"] == "heal"
]

# 渡劫时能派上用场的丹药
TRIBULATION_ITEMS: list[str] = [
    c["name"] for c in CONSUMABLES if c["effect"] == "tribulation"
]

# 效果 -> 中文说明，供 /使用 提示用
EFFECT_LABELS = {
    "heal": "恢复气血",
    "exp": "获得修为",
    "buff_atk": "下场战斗攻击提升",
    "buff_def": "下场战斗防御提升",
    "buff_speed": "下场战斗先手",
    "buff_luck": "下场战斗掉落翻倍",
    "cure": "解除异常",
    "revive": "立即恢复",
    "tribulation": "渡劫时额外扛一道雷",
}

# ---------------------------------------------------------------------------
# 探索产出池
# ---------------------------------------------------------------------------
# 探索挂机时用得上的筛选。放在这里是因为「什么能掉」属于数据层的事，
# 不该散落在 battle.py 的逻辑里。

# 探索能出的丹药（排除渡劫丹这种稀有品，它只在历练高级怪掉落）
EXPLORE_CONSUMABLE_EXCLUDE = ("渡劫丹",)


def explore_consumables() -> list[str]:
    """探索能掉落的丹药名列表。"""
    out = []
    for c in CONSUMABLES:
        if c["name"] in EXPLORE_CONSUMABLE_EXCLUDE:
            continue
        out.append(c["name"])
    return out


def explore_equipment_pool(turns: int) -> list[str]:
    """按转数给出探索能掉落的法宝池。

    v3.4：筛选依据从「攻击绝对值」改为「品阶门槛 req_turns」。

    旧规则是 `atk <= 转数 * 3 + 10` —— 在绝对值体系下能用，
    但 v3.4 装备已没有 atk 字段（改成了 atk_pct），这条规则会全线失效
    （所有 `e.get("atk", 0)` 都是 0，池子退化成"全部装备"）。

    新规则：装备的穿戴门槛 <= 玩家转数，就是能掉的。
    这比旧的数值筛选更准 —— 因为它直接对齐「玩家能穿得上」这个语义，
    不会出现「掉了一件穿不上的装备」。
    """
    try:
        t = int(turns)
    except (TypeError, ValueError):
        t = 1
    pool = [e["name"] for e in EQUIPMENTS if int(e.get("req_turns", 1)) <= t]
    # 池子为空时至少给最低品阶的几件
    if not pool:
        lowest = min(EQUIPMENTS, key=lambda e: int(e.get("tier_rank", 1)))
        rank = lowest.get("tier_rank", 1)
        pool = [e["name"] for e in EQUIPMENTS if e.get("tier_rank", 1) == rank]
    return pool


# ---------------------------------------------------------------------------
# 合并索引：任何物品名都能查到定义
# ---------------------------------------------------------------------------

def get_item(name: str) -> dict[str, Any] | None:
    """按物品名查定义。支持带品质后缀的名字，如「寒铁剑[稀]」。

    装备优先于丹药（同名时以装备为准）。
    装备的 atk_pct/def_pct/hp_pct 会**乘上品质倍率**再返回，
    调用方拿到的就是最终数值，不用自己算。

    v3：先过一次旧名映射，让老存档里的「小型生命药水」
    照样能查到「小回气丹」的定义。
    v3.4：品质倍率的作用对象从 atk/def/hp（绝对值）
    改为 atk_pct/def_pct/hp_pct（百分比）。语义不变 ——
    「品质好的同款装备更强」，只是现在强在百分比上。
    """
    base, quality = split_quality(name)
    base = resolve_alias(base)

    if base in EQUIPMENT_BY_NAME:
        item = dict(EQUIPMENT_BY_NAME[base])
        mult = quality_mult(quality)
        for key in ("atk_pct", "def_pct", "hp_pct"):
            if key in item:
                item[key] = round(item[key] * mult, 4)
        item["kind"] = "equipment"
        item["quality"] = quality
        item["base_name"] = base
        item["quality_tag"] = quality_tag(quality)
        # 法宝带品质后价值也提升，出售价同步放大
        item["price"] = int(round(item.get("price", 0) * mult))
        return item

    if base in SPECIAL_EQUIPMENT_BY_ID:
        item = dict(SPECIAL_EQUIPMENT_BY_ID[base])
        item["kind"] = "special"
        item["quality"] = QUALITY_COMMON
        item["base_name"] = base
        item["quality_tag"] = ""
        return item

    if base in CONSUMABLE_BY_NAME:
        item = dict(CONSUMABLE_BY_NAME[base])
        item["kind"] = "consumable"
        item["quality"] = QUALITY_COMMON
        item["base_name"] = base
        item["quality_tag"] = ""
        return item

    return None


def is_equipment(name: str) -> bool:
    base, _ = split_quality(name)
    return resolve_alias(base) in EQUIPMENT_BY_NAME


# ---------------------------------------------------------------------------
# 商店
# ---------------------------------------------------------------------------

# 买入价 = 出售价 × 这个倍率。
# 定 3 倍是为了让"买了再卖"必定亏本，防止刷金币套利。
BUY_PRICE_MULTIPLIER = 3

# 商店常驻商品（无限量）
SHOP_CONSUMABLES: list[str] = [
    "小回气丹",
    "中回气丹",
    "大回气丹",
    "力量药剂",
    "铁壁药剂",
    "还魂丹",
    "渡劫丹",
]


def sell_price(name: str) -> int:
    """物品的出售价（已计入品质）。"""
    item = get_item(name)
    return int(item.get("price", 0)) if item else 0


def buy_price(name: str) -> int:
    """物品的买入价。"""
    return sell_price(name) * BUY_PRICE_MULTIPLIER


# 坊市在售的最高品阶：七品以上必须靠打，不能买。
# （七/八/九品的产出出口：秘境 30/35/40 层首通里程碑 + 探索掉落 + 妖王掉落）
SHOP_MAX_TIER_RANK = 6


def shop_stock(level: int) -> list[str]:
    """按玩家转数返回可购买的法宝清单。

    【v3.5 重写】原实现有个**静默 bug**：

        cap = t * 2 + 6
        if int(eq.get("atk", 0)) <= cap:   # ← 恒为真

    v3.4 把装备的 `atk`（绝对值）改成 `atk_pct`（百分比）之后，
    `eq.get("atk", 0)` 永远返回 0，`0 <= cap` 永远成立 ——
    坊市退化成「135 件全都能买」，**连九品都能直接买**。
    所以「九品也能买」不是设计，是个没人发现的 bug。

    新规则两条：

    1. **七品以上下架坊市** —— 顶级装备必须是「打出来的」，
       否则秘境里程碑和帝兵的稀缺感都会被架空。
    2. **买不了穿不上的** —— 用 `req_turns` 过滤而不是攻击值，
       它直接对齐「玩家能穿得上」这个语义，不会出现
       「买回来发现装不了」。（和 `explore_equipment_pool` 同一口径。）
    """
    try:
        t = int(level)
    except (TypeError, ValueError):
        t = 1
    stock = []
    for eq in EQUIPMENTS:
        if int(eq.get("tier_rank", 1)) > SHOP_MAX_TIER_RANK:
            continue
        if int(eq.get("req_turns", 1)) > t:
            continue
        stock.append(eq["name"])
    return stock


def all_item_names() -> list[str]:
    """全部基础物品名，用于出售/使用指令的候选校验。"""
    names = list(EQUIPMENT_BY_NAME) + list(CONSUMABLE_BY_NAME)
    # 秘典残页也可以被出售，加进来
    try:
        from .scriptures import RELIC_NAMES

        names.extend(RELIC_NAMES)
    except Exception:  # noqa: BLE001 - 循环导入保护
        pass
    return names
