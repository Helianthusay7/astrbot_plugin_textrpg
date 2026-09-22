"""大道秘典：三千大道的声明层。

本模块**只有数据，没有逻辑**。所有战斗效果的解释权在 battle.py 的
效果注册表里（`EFFECT_HANDLERS`）。

加一条新大道的完整步骤：
    1. 往 SCRIPTURES 里加一项（照抄火之秘典的结构）
    2. 如果用的 effect kind 已经存在 -> 完事，一行引擎代码都不用改
    3. 如果是全新的 kind -> 在 battle.py 注册一个处理函数（约 5 行）

首期五条大道：火 / 水 / 金 / 土 / 风，覆盖
输出、续航、防御、控制、速度 五个方向。
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 效果类型说明（供实现参考，也是可扩展性的边界）
# ---------------------------------------------------------------------------
# 五条大道共用 8 种 kind，新增第六条大道八成能复用现成的：
#
#   magic_damage  按攻击力百分比的魔法伤害      params: mult, element
#   heal          回复气血                     params: ratio 或 value
#   buff          属性增益                     params: stat, value, turns, scope
#   resist        受到某元素伤害减免            params: element, value
#   add_damage    附加伤害（跟随主伤害）        params: mult, element
#   counter       受击反伤                     params: ratio
#   dodge         闪避概率                     params: chance
#   dot           持续伤害 / 流失               params: ratio, turns
#
# `scope` 用来区分 buff 的生效范围：
#   "battle"  仅本场战斗
#   "turn"    仅本回合（如铁壁）
# ---------------------------------------------------------------------------

EFFECT_KINDS = (
    "magic_damage",
    "heal",
    "buff",
    "resist",
    "add_damage",
    "counter",
    "dodge",
    "dot",
)


# ---------------------------------------------------------------------------
# 秘典表
# ---------------------------------------------------------------------------

SCRIPTURES: dict[str, dict[str, Any]] = {
    # ---------------- 火：爆发输出 ----------------
    "火之秘典": {
        "dao": "火",
        "element": "fire",
        "desc": "掌控世间之火，以灼热焚尽万物。",
        "flavor": "火起于微末，可燎原。",
        "tiers": {
            1: {
                "name": "火焰",
                "desc": "对敌方造成 200% 攻击力的火焰魔法伤害",
                "type": "active",
                "cooldown": 1,
                "effect": {"kind": "magic_damage", "mult": 2.0, "element": "fire"},
            },
            2: {
                "name": "控火",
                "desc": "你受到的火焰伤害 -25%",
                "type": "passive",
                "effect": {"kind": "resist", "element": "fire", "value": 0.25},
            },
            3: {
                "name": "燎原",
                "desc": "你所造成的所有伤害，附加 20% 攻击力的火焰魔法伤害",
                "type": "passive",
                "effect": {"kind": "add_damage", "mult": 0.2, "element": "fire"},
            },
        },
    },

    # ---------------- 水：续航回复 ----------------
    "水之秘典": {
        "dao": "水",
        "element": "water",
        "desc": "上善若水，润物无声。",
        "flavor": "水无形，故能胜万物。",
        "tiers": {
            1: {
                "name": "回春",
                "desc": "回复自身 25% 气血上限",
                "type": "active",
                "cooldown": 2,
                "effect": {"kind": "heal", "ratio": 0.25},
            },
            2: {
                "name": "润泽",
                "desc": "每回合结束时回复 3% 气血上限",
                "type": "passive",
                "effect": {"kind": "heal", "ratio": 0.03, "scope": "turn"},
            },
            3: {
                "name": "潮涌",
                "desc": "每次回复气血时，额外净化 1 个负面状态",
                "type": "passive",
                "effect": {"kind": "buff", "stat": "cleanse", "value": 1,
                           "scope": "battle"},
            },
        },
    },

    # ---------------- 金：防御反伤 ----------------
    "金之秘典": {
        "dao": "金",
        "element": "metal",
        "desc": "金者坚刚，千锤不折。",
        "flavor": "刀剑加身，其锋自损。",
        "tiers": {
            1: {
                "name": "铁壁",
                "desc": "本回合受到的伤害减少 50%",
                "type": "active",
                "cooldown": 2,
                "effect": {"kind": "buff", "stat": "defense", "value": 50,
                           "turns": 1, "scope": "turn"},
            },
            2: {
                "name": "锐金",
                "desc": "每次受到攻击，反弹 10% 伤害",
                "type": "passive",
                "effect": {"kind": "counter", "ratio": 0.10},
            },
            3: {
                "name": "不坏",
                "desc": "气血低于 30% 时，防御力 +50%",
                "type": "passive",
                "effect": {"kind": "buff", "stat": "defense", "value": 50,
                           "condition": "hp_below_30"},
            },
        },
    },

    # ---------------- 土：控制削弱 ----------------
    "土之秘典": {
        "dao": "土",
        "element": "earth",
        "desc": "厚德载物，镇岳不移。",
        "flavor": "大地无言，而万钧自重。",
        "tiers": {
            1: {
                "name": "落石",
                "desc": "造成 150% 攻击力伤害，并使敌方攻击力 -20%（3 回合）",
                "type": "active",
                "cooldown": 2,
                "effect": {"kind": "magic_damage", "mult": 1.5, "element": "earth",
                           "debuff": {"stat": "atk", "value": -20, "turns": 3}},
            },
            2: {
                "name": "厚重",
                "desc": "气血上限 +15%",
                "type": "passive",
                "effect": {"kind": "buff", "stat": "hp", "value": 15},
            },
            3: {
                "name": "陷阵",
                "desc": "敌方每回合流失 5% 气血",
                "type": "passive",
                "effect": {"kind": "dot", "ratio": 0.05},
            },
        },
    },

    # ---------------- 风：速度连击 ----------------
    "风之秘典": {
        "dao": "风",
        "element": "wind",
        "desc": "风无常形，来去无踪。",
        "flavor": "疾风知劲草。",
        "tiers": {
            1: {
                "name": "疾风",
                "desc": "本回合额外攻击一次",
                "type": "active",
                "cooldown": 2,
                "effect": {"kind": "buff", "stat": "extra_attack", "value": 1,
                           "turns": 1, "scope": "turn"},
            },
            2: {
                "name": "轻身",
                "desc": "25% 概率闪避敌方攻击",
                "type": "passive",
                "effect": {"kind": "dodge", "chance": 0.25},
            },
            3: {
                "name": "追风",
                "desc": "每次命中叠加 5% 攻速，最多 30%",
                "type": "passive",
                "effect": {"kind": "buff", "stat": "atk", "value": 5,
                           "max": 30, "stack": True},
            },
        },
    },
}

# 秘典名列表（顺序固定，用于展示与随机掉落）
SCRIPTURE_NAMES: list[str] = list(SCRIPTURES)

# 每条大道的阶数上限
MAX_TIER = 3


# ---------------------------------------------------------------------------
# 查询辅助
# ---------------------------------------------------------------------------

def get_scripture(name: str) -> dict[str, Any] | None:
    """按名字取秘典定义。"""
    return SCRIPTURES.get(name)


def has_scripture(name: str) -> bool:
    return name in SCRIPTURES


def tier_info(name: str, tier: int) -> dict[str, Any] | None:
    """取某条秘典某一阶的定义。"""
    sc = SCRIPTURES.get(name)
    if sc is None:
        return None
    return sc["tiers"].get(int(tier))


def tier_effect(name: str, tier: int) -> dict[str, Any] | None:
    """取某阶的效果声明。"""
    info = tier_info(name, tier)
    if info is None:
        return None
    return info.get("effect")


def active_tiers(name: str, learned_tier: int) -> list[tuple[int, dict[str, Any]]]:
    """取出「已习得阶数」内所有主动技。

    返回 [(阶数, 定义), ...]，按阶数升序。
    """
    sc = SCRIPTURES.get(name)
    if sc is None:
        return []
    out = []
    for tier in range(1, int(learned_tier) + 1):
        info = sc["tiers"].get(tier)
        if info and info.get("type") == "active":
            out.append((tier, info))
    return out


def passive_tiers(name: str, learned_tier: int) -> list[tuple[int, dict[str, Any]]]:
    """取出已习得阶数内所有被动。"""
    sc = SCRIPTURES.get(name)
    if sc is None:
        return []
    out = []
    for tier in range(1, int(learned_tier) + 1):
        info = sc["tiers"].get(tier)
        if info and info.get("type") == "passive":
            out.append((tier, info))
    return out


def relic_name(scripture_name: str) -> str:
    """该秘典对应的「残页」物品名，如 火之秘典 -> 火之残页。"""
    sc = SCRIPTURES.get(scripture_name)
    if sc is None:
        return f"{scripture_name}残页"
    return f"{sc['dao']}之残页"


# 全部残页名，用于掉落表与背包校验
RELIC_NAMES: list[str] = [relic_name(n) for n in SCRIPTURE_NAMES]

# 残页名 -> 秘典名，反查
RELIC_TO_SCRIPTURE: dict[str, str] = {
    relic_name(n): n for n in SCRIPTURE_NAMES
}


# ---------------------------------------------------------------------------
# 装配位
# ---------------------------------------------------------------------------

# 装配位 = 1 + 每次大境界突破 +1，最多 8（大乘期）
BASE_EQUIP_SLOTS = 1
MAX_EQUIP_SLOTS = 8


def equip_slots(breakthroughs: int) -> int:
    """可同时装配的秘典数量。"""
    try:
        n = max(0, int(breakthroughs))
    except (TypeError, ValueError):
        n = 0
    return min(MAX_EQUIP_SLOTS, BASE_EQUIP_SLOTS + n)


# ---------------------------------------------------------------------------
# 修行消耗
# ---------------------------------------------------------------------------

def relic_cost(name: str, target_tier: int) -> int:
    """把某条秘典从 (target_tier-1) 阶提升到 target_tier 阶，
    需要多少张残页。

    三阶 6 -> 5（2026-09-19 数值平衡）：与探索残页权重、拾取次数的调整
    配套，把「单条秘典满阶」的总需求从 1+3+6=10 张降到 1+3+5=9 张。
    """
    tier = max(1, int(target_tier))
    return {1: 1, 2: 3, 3: 5}.get(tier, tier * 3)


def qi_cost(name: str, target_tier: int) -> int:
    """提升到某阶需要的修为。

    一阶是「学会」，只收残页（修为 0），
    二三阶才要修为，且随阶数陡增。
    """
    tier = max(1, int(target_tier))
    return {1: 0, 2: 2_000, 3: 8_000}.get(tier, tier * 5_000)


# ---------------------------------------------------------------------------
# 战斗中的秘典状态
# ---------------------------------------------------------------------------

def make_battle_state(scriptures: dict[str, int],
                      equipped: list[str]) -> dict[str, Any]:
    """构造一场战斗的秘典初始状态。

    scriptures: {"火之秘典": 2} —— 已习得阶数
    equipped:   ["火之秘典"]     —— 本次战斗装配的

    返回结构（战斗引擎用它记录冷却与层数）：
        {
          "equipped": {秘典名: 已习得阶数},
          "cooldowns": {秘典名: {阶数: 剩余回合}},
          "stacks":   {秘典名: {stat: 当前层数}},
        }
    """
    equipped_map: dict[str, int] = {}
    for name in equipped:
        tier = int(scriptures.get(name, 0) or 0)
        if tier > 0 and name in SCRIPTURES:
            equipped_map[name] = tier

    cooldowns: dict[str, dict[int, int]] = {}
    for name, learned in equipped_map.items():
        cooldowns[name] = {}
        for tier, _info in active_tiers(name, learned):
            cooldowns[name][tier] = 0

    return {
        "equipped": equipped_map,
        "cooldowns": cooldowns,
        "stacks": {name: {} for name in equipped_map},
    }


def tick_cooldowns(state: dict[str, Any]) -> None:
    """每回合结束，所有主动技冷却 -1。"""
    for name, per_tier in state.get("cooldowns", {}).items():
        for tier in list(per_tier):
            if per_tier[tier] > 0:
                per_tier[tier] -= 1


def ready_actives(state: dict[str, Any]) -> list[tuple[str, int, dict[str, Any]]]:
    """取出本回合可用（冷却为 0）的主动技。

    返回 [(秘典名, 阶数, 定义), ...]，按阶数降序（高阶优先放）。
    """
    out = []
    cds = state.get("cooldowns", {})
    for name, learned in state.get("equipped", {}).items():
        for tier, info in active_tiers(name, learned):
            if cds.get(name, {}).get(tier, 0) <= 0:
                out.append((name, tier, info))
    out.sort(key=lambda x: -x[1])
    return out


def start_cooldown(state: dict[str, Any], name: str, tier: int) -> None:
    """放完技能后开始计算冷却。"""
    info = tier_info(name, tier)
    cd = int(info.get("cooldown", 1)) if info else 1
    state.setdefault("cooldowns", {}).setdefault(name, {})[tier] = max(1, cd)


def all_passives(scriptures: dict[str, int],
                 equipped: list[str]) -> list[tuple[str, int, dict[str, Any]]]:
    """收集装配中的秘典的全部被动效果。

    返回 [(秘典名, 阶数, 定义), ...]。
    """
    out = []
    for name in equipped:
        learned = int(scriptures.get(name, 0) or 0)
        for tier, info in passive_tiers(name, learned):
            out.append((name, tier, info))
    return out
