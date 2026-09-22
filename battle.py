"""战斗引擎与成长结算。

这里只做纯计算，不碰 AstrBot 的 API，也不做 IO ——
所有函数输入玩家对象和怪物定义，返回结算结果。
好处是逻辑可以脱离机器人单独跑测试。

v3.0 的两个关键改造：

1. **效果注册表（可扩展性的核心）**
   秘典效果在 scriptures.py 里只做「声明」，真正怎么打由本模块的
   EFFECT_HANDLERS 决定。战斗主循环只查表分派，不写 if-else 链。
   加新秘典 -> 复用现成 kind -> 引擎一行不用改。

2. **修为取代经验**
   战斗奖励从 exp 改为 qi，升级逻辑走 realm 的转数曲线。

回合制战斗的核心约束：
    必须能在有限回合内结束。高防玩家配低攻怪物、或反之，
    都可能让双方互相打不动。因此有两条保护：
      1. roll_damage 有伤害下限（MIN_DAMAGE）
      2. 战斗有回合上限（MAX_BATTLE_TURNS），超出判定为平局
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import realm as realm_mod
from .scriptures import (
    active_tiers,
    all_passives,
    make_battle_state,
    passive_tiers,
    ready_actives,
    start_cooldown,
    tick_cooldowns,
)
from .player import (
    Player,
    MAX_BATTLE_TURNS,
    FLEE_SUCCESS_RATE,
    TRAIN_EXP_PER_HOUR,
    TRAIN_MAX_HOURS,
    CRUSH_HP_RATIO,
    NARROW_HP_RATIO,
    OUTCOME_EXP_MULT,
    NARROW_DROP_BONUS,
    QI_DEVIATION_PENALTY,
    EXPLORE_MIN_SECONDS,
    EXPLORE_MAX_SECONDS,
    deviation_chance,
    exp_needed,
    roll_damage,
    roll_damage_tuned,
    roll_drop,
    tribulation_bolts,
    base_atk,
    base_def,
    base_hp,
)


# 战斗结果常量。把「赢」细分成三档，让战斗有层次感。
OUTCOME_CRUSH = "crush"      # 碾压：满血获胜
OUTCOME_WIN = "win"          # 常规胜利
OUTCOME_NARROW = "narrow"    # 惨胜：残血获胜
OUTCOME_DRAW = "draw"        # 平局
OUTCOME_LOSE = "lose"        # 败北

# ---------------------------------------------------------------------------
# 暴击（v3.4）
# ---------------------------------------------------------------------------
# 【为什么要有基础暴击】
# 物穿套上品给的是 "+15% 暴击率"——如果基础暴击率是 0，
# 那这个 15% 就是「从无到有」，穿上等于凭空多一个机制。
# 给它一个 5% 的底，让人感觉是「这套把暴击率翻了三倍」，
# 而不是「这套才让你会暴击」。差别在于成长感。
CRIT_BASE_RATE = 0.05        # 基础暴击率
CRIT_MULT = 1.5              # 暴击伤害倍率（×1.5）

# 肉盾套「低血线额外减免」的触发血线
LOW_HP_DR_THRESHOLD = 0.3

# 「无视防御」触发时的追加伤害 = p_atk × 本系数。
# 【为什么不是 0（真的把怪防砍到 0）】见 _am_ignore_def 的详细说明：
# 高穿甲已经把怪防削得所剩无几，再砍掉剩下的等于白给（期望仅 +0.65%）。
# 改成固定追加伤害后与怪防脱钩，期望变成中品 +3.2% / 上品 +5.8%。
IGNORE_DEF_ATK_RATIO = 0.3

# 法穿套的「术法增伤」有多少能作用到普攻上（详见 _am_magic_amp）。
#
# 【2026-09-19 v3.5：0.3 -> 0.0】
# 原设计取 0.3 是为了「秘典吃全额、普攻吃三折」，避免没秘典的玩家空转。
# 但实测（_balance/lab/balance_probe.py，合成口径 = 输出增益 + 生存增益）
# 发现这 0.3 折正是三套失衡的元凶：法穿 magic_dmg_bonus=28% × 0.3
# = 普攻 +8.4%，叠在穿甲放大过的面板上，让法穿凭空多一截输出。
#
# 九品合成极差（物穿 / 法穿 / 肉盾）实测：
#   折率 0.30 -> 19.9pp ✗        折率 0.15 -> 14.5pp ✗
#   折率 0.10 -> 12.2pp ✗        折率 0.05 -> 10.1pp ✗（临界）
#   折率 0.00 ->  7.3pp ✓  <- 采用
#
# 【取舍，改之前务必知道】折率归零后，没装秘典的玩家穿法穿套会偏弱
# （术法增伤只对秘典技能生效）。这是刻意的：法穿套的定位就是「秘典协同」，
# 纯普攻流应当选物穿套。若将来要补偿，给法穿套加 CD 缩减或破防概率即可，
# **不要动这个折率** —— 实测它是三套平衡的主旋钮。
MAGIC_AMP_TO_ATTACK = 0.0

# 结果 -> 展示文案
OUTCOME_LABELS = {
    OUTCOME_CRUSH: "碾压",
    OUTCOME_WIN: "胜利",
    OUTCOME_NARROW: "险胜",
    OUTCOME_DRAW: "势均力敌",
    OUTCOME_LOSE: "道消",
}


# ---------------------------------------------------------------------------
# 装备机制注册表（v3.4）
# ---------------------------------------------------------------------------
# 【为什么再开一张表，而不是继续往主循环里塞 if】
# 老板的原话是「套装效果不要一味乘算，后面中上品可以加入高级乘区」——
# 这句话的隐含要求是：**以后还要往套装里加新机制**。
# 如果每加一个机制就在主循环里插一段 if，主循环会变成一个几百行的
# 面条，以后谁都不敢动。所以和 EFFECT_HANDLERS 一样，机制也走注册表：
#
#   机制只声明「我要怎么改这一击的伤害」，主循环只负责问一遍。
#
# 每个攻击修正器的签名：
#     fn(ctx: dict) -> dict
# 返回：
#     {"dmg_mult": 1.5}        伤害乘区（多个修正器相乘）
#     {"dmg_flat": 800}        固定追加伤害（多个相加）
#     {"tag": "破防"}          触发标签（进了日志文案）
#
# ctx 里有：p_atk / p_def / target_def / penetration / current_dmg / rng
# ---------------------------------------------------------------------------

ATTACK_MODIFIERS: dict[str, Callable[[dict], dict]] = {}
DEFENSE_MODIFIERS: dict[str, Callable[[dict], dict]] = {}


def register_attack_modifier(key: str):
    """注册一个「出手时」的装备机制修正器。"""

    def deco(fn: Callable[[dict], dict]):
        ATTACK_MODIFIERS[key] = fn
        return fn

    return deco


def register_defense_modifier(key: str):
    """注册一个「受击时」的装备机制修正器。"""

    def deco(fn: Callable[[dict], dict]):
        DEFENSE_MODIFIERS[key] = fn
        return fn

    return deco


@register_attack_modifier("ignore_def")
def _am_ignore_def(ctx: dict) -> dict:
    """物穿套：概率破防。

    【v3.4 实测修正 —— 这条改动的来龙去脉值得记下来】

    最初实现是「触发时把怪防按 0 算」。跑真实战斗后发现它几乎是白给：

        t=28  穿甲45% 伤害 484，破防后 517  → 只有 +6.4%
        t=46  穿甲45% 伤害 1178，破防后 1261 → 只有 +6.8%
        t=68  穿甲45% 伤害 2561，破防后 2748 → 只有 +7.0%

    再乘上触发概率（中品 10% / 上品 18%），期望收益只有 **+0.65% ~ +1.17%**。

    根因：**穿甲已经在前面把怪防削掉了**。九品穿甲 45% 之后，
    怪防对伤害的贡献只剩 12.5%，再砍掉这 12.5% 当然没感觉。
    换句话说，「无视防御」和「高穿甲」是互相稀释的两个机制 ——
    同一套装备上叠这两个，等于第二个白送。

    【修正】改成**追加一段固定伤害**，与怪防彻底脱钩：
        破防触发 -> 额外打 p_atk × IGNORE_DEF_ATK_RATIO

    这样收益就不再依赖怪有多硬，而是稳定地跟玩家自己的攻击力挂钩。
    实测（触发时）：
        K=0.3 -> 单次 +32%  期望 中品 +3.2% / 上品 +5.8%
        K=0.5 -> 单次 +54%  期望 中品 +5.4% / 上品 +9.7%

    取 K=0.3：中品 +3.2% 的期望，已经和「穿甲从 0 到 45%」的
    +6.1% 在同一个量级里，玩家能明确感到「偶尔会爆一下」。
    """
    pct = float(ctx.get("avoid_def_pct", 0.0) or 0.0)
    if pct <= 0:
        return {}
    if ctx["rng"].random() * 100.0 >= pct:
        return {}
    return {
        "dmg_flat": int(ctx["p_atk"] * IGNORE_DEF_ATK_RATIO),
        "tag": "破防",
        "count": "avoid_def",
    }


@register_attack_modifier("magic_amp")
def _am_magic_amp(ctx: dict) -> dict:
    """法穿套：术法增伤（中品 +8% / 上品 +20%）。

    【实测发现的设计漏洞 —— 这条补丁的来龙去脉】

    定稿文档给法穿套的机制是「术法伤害 +8%/+20%」+「秘典冷却 -1 回合」。
    跑真实战斗后发现一个尴尬的事实：

        **没装秘典的玩家穿满九品法穿套，收益只有穿甲。**
        术法增伤只在秘典魔法技能上生效，普攻一点都吃不到；
        冷却缩减在没秘典时也无处可减。

    用真实数据验证：九品法穿满装 + 空秘典 -> mech 计数里
    除了基础暴击 1 次，机制全 0；而同样条件穿物穿套，
    破防和暴击都在跳。

    这违背了三套「各有机制」的设计意图 —— 法穿套变成了
    「只有秘典玩家的套装」，而秘典本身是另一个成长系统，
    不该成为装备生效的前提。

    【修正】给法穿套补一条普攻也能吃到的机制：
    术法增伤同时给普攻一个**较小的**加成（打三折）。
    这样：
        - 有秘典：秘典吃到全额 +8%/+20%，普攻吃到打折的，套装更强
        - 没秘典：普攻至少有加成，套装不空转
    打折系数用 0.3 —— 保证「术法」这个定位还在（秘典才是主收益），
    但不再是 0。
    """
    pct = float(ctx.get("magic_dmg_bonus", 0.0) or 0.0)
    if pct <= 0:
        return {}
    effective = pct * MAGIC_AMP_TO_ATTACK
    if effective <= 0:
        return {}
    return {"dmg_mult": 1.0 + effective, "count": "magic_amp"}


@register_attack_modifier("crit")
def _am_crit(ctx: dict) -> dict:
    """全局暴击。基础 5%，物穿套上品再 +15%。

    为什么给一个基础值：如果基础是 0，那「+15% 暴击」就是
    「从无到有」，穿上像凭空多一个机制；有了 5% 的底，
    感觉是「这套把暴击率翻了三倍」—— 成长感不一样。
    """
    rate = float(ctx.get("crit_rate", 0.0) or 0.0)
    if rate <= 0:
        return {}
    if ctx["rng"].random() >= rate:
        return {}
    return {"dmg_mult": CRIT_MULT, "tag": "暴击", "count": "crit"}


@register_defense_modifier("damage_reduce")
def _dm_reduce(ctx: dict) -> dict:
    """肉盾套：常驻减伤（一品 6% ~ 九品 30%）。"""
    pct = float(ctx.get("dr_pct", 0.0) or 0.0)
    if pct <= 0:
        return {}
    return {"dmg_mult": 1.0 - pct, "count": "dr"}


@register_defense_modifier("low_hp_extra_dr")
def _dm_low_hp(ctx: dict) -> dict:
    """肉盾套上品：气血 <30% 时再免一层（乘法叠加）。

    【为什么不和常驻减伤合并成一个数】
    常驻减伤是「稳定收益」，低血减免是「救命收益」。
    分开之后，血线跌破 30% 的那个瞬间玩家能感知到
    （日志会单独打一行提示），而不是被平均进每一回合里看不见。
    """
    pct = float(ctx.get("low_hp_dr_pct", 0.0) or 0.0)
    if pct <= 0:
        return {}
    hp, max_hp = ctx["p_hp"], ctx["p_max_hp"]
    if max_hp <= 0 or hp / max_hp >= LOW_HP_DR_THRESHOLD:
        return {}
    return {"dmg_mult": 1.0 - pct, "tag": "护体", "count": "dr"}


@register_defense_modifier("reflect")
def _dm_reflect(ctx: dict) -> dict:
    """肉盾套：反伤。

    【诚实说明】实测下来反伤是**锦上添花，不是输出通道**。
    九品肉盾套一场反伤 ≈ 3.9 点/场（怪有 11205 气血），贡献 0.035%。
    原因是反伤按「怪打我的伤害」(711) 算，而战斗只打约 5 回合：
        711 × 18% × 5 ≈ 640，对比怪 11205 血就是洒洒水。

    之所以不改数值：老板定的「反伤 8%/18%」是他自己拍的，
    这里只负责把它跑通、把它诚实记录下来，平衡要调的时候有依据。
    """
    pct = float(ctx.get("reflect_pct", 0.0) or 0.0)
    if pct <= 0:
        return {}
    return {"reflect": pct, "count": "reflect"}


# ---------------------------------------------------------------------------
# 效果注册表 —— 可扩展性的核心
# ---------------------------------------------------------------------------
# 每个处理函数签名固定：
#     fn(ctx: dict, effect: dict) -> dict
#
# ctx 是本场战斗的共享上下文，包含：
#     player / item_db / p_atk / p_max_hp / target_hp / target_max_hp / log
# 返回值用来告知主循环「这个效果做了什么」：
#     {"damage": N}      造成 N 点伤害
#     {"heal": N}        回复 N 点气血
#     {"text": "..."}    追加到战斗日志
# ---------------------------------------------------------------------------

EFFECT_HANDLERS: dict[str, Callable[[dict, dict], dict]] = {}


def register_effect(kind: str):
    """注册一个效果类型。加新效果类型只需写个小函数挂上。"""

    def deco(fn: Callable[[dict, dict], dict]):
        EFFECT_HANDLERS[kind] = fn
        return fn

    return deco


@register_effect("magic_damage")
def _eff_magic_damage(ctx: dict, effect: dict) -> dict:
    """按攻击力百分比的魔法伤害，无视一半防御。

    参数：mult（攻击力倍数）、element（元素名，只用于文案）

    v3.1：额外吃装备穿甲 —— 魔法本来就是"穿透"定位，
    再叠穿甲后，怪的防御几乎不构成减免。
    """
    mult = float(effect.get("mult", 1.0))
    base = ctx["p_atk"] * mult
    # 魔法伤害只吃 30% 防御减免，比物理更穿透
    pen = float(ctx.get("penetration", 0.0) or 0.0)
    target_def = ctx.get("target_def", 0) * 0.3 * (1.0 - pen)
    dmg = max(1, int(base - target_def * 0.5))
    elem = effect.get("element", "")
    label = {"fire": "火焰", "water": "水", "earth": "土", "wind": "风",
             "metal": "金"}.get(elem, "术法")
    return {"damage": dmg, "text": f"{label}魔法造成 {dmg} 点伤害"}


@register_effect("heal")
def _eff_heal(ctx: dict, effect: dict) -> dict:
    """回复气血。参数：ratio（占气血上限比例）或 value（固定值）。"""
    ratio = effect.get("ratio")
    if ratio is not None:
        amount = int(ctx["p_max_hp"] * float(ratio))
    else:
        amount = int(effect.get("value", 0))
    amount = max(1, amount)
    # 不能超过上限
    missing = max(0, ctx["p_max_hp"] - ctx["player"].hp)
    healed = min(amount, missing) if missing > 0 else 0
    return {"heal": healed, "text": f"回复 {healed} 点气血"}


@register_effect("buff")
def _eff_buff(ctx: dict, effect: dict) -> dict:
    """属性增益 / 状态。

    参数：stat（属性名）、value（数值）、turns（持续回合）、scope
    特殊 stat：
        "extra_attack" 本回合额外攻击次数
        "cleanse"      净化负面数量
    """
    stat = effect.get("stat", "")
    value = effect.get("value", 0)
    if stat == "extra_attack":
        return {"extra_attack": int(value), "text": f"本回合可额外攻击 {int(value)} 次"}
    if stat == "cleanse":
        return {"cleanse": int(value), "text": "净化了负面状态"}
    label = {"atk": "攻击", "defense": "防御", "hp": "气血"}.get(stat, stat)
    return {"stat": stat, "value": value, "text": f"{label} {'+' if value >= 0 else ''}{value}%"}


@register_effect("resist")
def _eff_resist(ctx: dict, effect: dict) -> dict:
    """元素抗性（被动，在受击时查询，这里只返回状态）。"""
    return {"resist": effect.get("element", ""), "value": float(effect.get("value", 0))}


@register_effect("add_damage")
def _eff_add_damage(ctx: dict, effect: dict) -> dict:
    """附加伤害（跟随主伤害追加）。参数：mult（攻击力倍数）。"""
    mult = float(effect.get("mult", 0.0))
    extra = max(1, int(ctx["p_atk"] * mult))
    return {"damage": extra, "text": f"附加 {extra} 点伤害"}


@register_effect("counter")
def _eff_counter(ctx: dict, effect: dict) -> dict:
    """受击反伤（被动）。参数：ratio（反弹比例）。"""
    return {"counter": float(effect.get("ratio", 0.0))}


@register_effect("dodge")
def _eff_dodge(ctx: dict, effect: dict) -> dict:
    """闪避（被动）。参数：chance。"""
    return {"dodge": float(effect.get("chance", 0.0))}


@register_effect("dot")
def _eff_dot(ctx: dict, effect: dict) -> dict:
    """持续流失（被动，每回合末生效）。参数：ratio。"""
    return {"dot": float(effect.get("ratio", 0.0))}


# ---------------------------------------------------------------------------
# 被动效果汇总
# ---------------------------------------------------------------------------

class PassiveBag:
    """把玩家装配的秘典被动收集成一个查询对象。

    好处：战斗循环里只问 bag.counter_ratio() / bag.dodge_chance()，
    不用管这些数是从哪条秘典来的。加新被动只改这个类。
    """

    def __init__(self, player: Player):
        self.resist: dict[str, float] = {}
        self.counter_ratio: float = 0.0
        self.dodge_chance: float = 0.0
        self.dot_ratio: float = 0.0
        self.atk_pct: float = 0.0
        self.def_pct: float = 0.0
        self.hp_pct: float = 0.0
        self.regen_ratio: float = 0.0
        self.extra_attack: int = 0
        self.atk_stack_pct: float = 0.0
        self.atk_stack_max: float = 0.0
        self.cleanse: int = 0
        self.def_low_hp_pct: float = 0.0

        equipped = getattr(player, "equipped_scriptures", []) or []
        scriptures = getattr(player, "scriptures", {}) or {}
        for _name, _tier, info in all_passives(scriptures, equipped):
            eff = info.get("effect", {})
            self._absorb(eff)

    def _absorb(self, eff: dict) -> None:
        kind = eff.get("kind")
        if kind == "resist":
            elem = eff.get("element", "")
            self.resist[elem] = self.resist.get(elem, 0.0) + float(eff.get("value", 0))
        elif kind == "counter":
            self.counter_ratio += float(eff.get("ratio", 0.0))
        elif kind == "dodge":
            self.dodge_chance += float(eff.get("chance", 0.0))
        elif kind == "dot":
            self.dot_ratio += float(eff.get("ratio", 0.0))
        elif kind == "add_damage":
            # 附加伤害在这里折算成攻击力百分比增益
            self.atk_pct += float(eff.get("mult", 0.0)) * 100
        elif kind == "heal":
            if eff.get("scope") == "turn":
                self.regen_ratio += float(eff.get("ratio", 0.0))
        elif kind == "buff":
            stat = eff.get("stat", "")
            value = float(eff.get("value", 0))
            if stat == "atk":
                if eff.get("stack"):
                    self.atk_stack_pct = value
                    self.atk_stack_max = float(eff.get("max", value * 6))
                else:
                    self.atk_pct += value
            elif stat == "defense":
                if eff.get("condition") == "hp_below_30":
                    self.def_low_hp_pct += value
                else:
                    self.def_pct += value
            elif stat == "hp":
                self.hp_pct += value
            elif stat == "extra_attack":
                self.extra_attack += int(value)
            elif stat == "cleanse":
                self.cleanse += int(value)

    def resist_for(self, element: str) -> float:
        return min(0.8, self.resist.get(element, 0.0))


# ---------------------------------------------------------------------------
# 结算结果结构
# ---------------------------------------------------------------------------

@dataclass
class BattleResult:
    """一场战斗的结算结果，供上层拼消息用。"""

    outcome: str                      # crush / win / narrow / draw / lose
    monster_name: str
    turns: int = 0
    exp_gain: int = 0                 # 修为（字段名保留兼容，语义已是 qi）
    gold_gain: int = 0                # 灵石
    drop: str | None = None
    drop_lost: bool = False           # 掉落了但背包满，没拿到
    leveled_up: bool = False
    levels_gained: int = 0            # 本次精进了几转
    hp_after: int = 0
    # 战斗过程日志，逐回合的文字描述
    log: list[str] = field(default_factory=list)
    # v3：本场战斗用过的秘典技能（用于文案展示）
    skills_used: list[str] = field(default_factory=list)
    # v3.4：本场战斗触发的套装机制次数（用于文案展示，让套装「看得见」）
    mech_hits: dict[str, int] = field(default_factory=dict)
    # v3.4：本场战斗生效的套系名（物穿/法穿/肉盾，空串表示没穿满）
    active_set: str = ""

    @property
    def label(self) -> str:
        """结果的中文标签。"""
        return OUTCOME_LABELS.get(self.outcome, self.outcome)

    @property
    def won(self) -> bool:
        return self.outcome in (OUTCOME_CRUSH, OUTCOME_WIN, OUTCOME_NARROW)

    def mech_summary(self) -> str:
        """把本场触发的套装机制拼成一句短文案，没触发就返回空串。

        例：「破锋套 · 破防 2 次 / 暴击 3 次」

        【v3.5】套系名走 `items.set_display_name()` 映射 ——
        `active_set` 存的是内部 id（物穿/法穿/肉盾），**永不改名**
        （存档里装备按它匹配套系，改了 = 线上玩家套装当场失效）。
        给玩家看的是展示名（破锋/通玄/镇岳），两层分开。

        用**延迟导入**：`items` 反过来要读 `battle` 的常量
        （破防系数、暴击率等），顶层 import 会成环。
        """
        if not self.active_set:
            return ""
        try:
            from .items import set_display_name
            _set_name = set_display_name(self.active_set)
        except Exception:  # noqa: BLE001 - 循环导入/独立运行兜底
            _set_name = self.active_set
        labels = {
            "avoid_def": "破防",
            "crit": "暴击",
            "reflect": "反伤",
            "dr": "减伤",
            "magic_amp": "术法",
        }
        parts = []
        for key, label in labels.items():
            n = self.mech_hits.get(key, 0)
            if n > 0:
                parts.append(f"{label} {n} 次")
        if not parts:
            return f"{_set_name}套"
        return f"{_set_name}套 · " + " / ".join(parts)


@dataclass
class TrainResult:
    """闭关结算结果（原「离线修炼」）。"""

    hours: float = 0.0
    exp_gain: int = 0                 # 修为
    leveled_up: bool = False
    levels_gained: int = 0
    capped: bool = False              # 是否触达时长上限
    deviation: bool = False           # 是否走火入魔


@dataclass
class ExploreResult:
    """探索挂机结算结果。"""

    hours: float = 0.0
    qi_gain: int = 0
    gold_gain: int = 0
    items: list[str] = field(default_factory=list)      # 真实物品（可进储物袋）
    relics: list[str] = field(default_factory=list)     # 秘典残页
    events: list[str] = field(default_factory=list)     # 特殊事件文案（非物品）
    capped: bool = False


@dataclass
class TribulationResult:
    """天劫结算结果。"""

    survived: int = 0                 # 抗住几道雷
    total: int = 0                    # 一共几道
    passed: bool = False
    damages: list[int] = field(default_factory=list)
    turns_lost: int = 0
    rest_seconds: float = 0.0
    ascended: bool = False


# ---------------------------------------------------------------------------
# 抽怪
# ---------------------------------------------------------------------------

def spawn_monster(level: int, ascensions: int = 0) -> dict[str, Any]:
    """按玩家转数抽一只妖兽 —— 境界分池（v3.2）。

    【为什么改掉旧规则】
    旧规则是 `level <= 玩家转数 + slack`。它有两个病：

      1. **池子只增不减**。转数越高，符合条件的怪越多，但下限恒为 1。
         72 转玩家会在 72 只怪里抽 —— 有概率抽到 Lv1 的史莱姆。
      2. **妖兽 Lv 与转数不同量纲**。旧数据 Lv 只到 28，玩家能到 72 转，
         比例 1:2.57，越到后期越只能抽「不及格」的怪。

    【新规则：境界分池】
    把 72 只妖兽按境界分成 8 池（每境 9 只，Lv 与该境转数一一对应），
    然后按「本境 70% / 低一境 20% / 高一境 10%」加权抽：

        - 池子恒为 27 只左右、跨 3 个境界，规模稳定；
        - 不会抽到送分怪（最低只跌一境），也不会抽到打不过的怪（最高只升一境）；
        - 相邻境界有 30% 的交叉，打怪时偶尔遇到「越境强敌」，有惊喜感。

    第一境（炼气期）没有「低一境」，权重补给本境；
    最后一境（大乘期）没有「高一境」，同样补给本境。

    注意：抽到之后，实际战斗数值由 monster_stats 按玩家转数**动态缩放**，
    所以这里只负责挑选「哪只妖兽」（风味/掉落），
    不再决定难度 —— 难度统一交给战力系统。
    """
    from .items import MONSTERS

    try:
        lv = int(level)
    except (TypeError, ValueError):
        lv = 1
    lv = realm_mod.clamp_turns(lv)

    cur_idx = realm_mod.realm_index_of(lv)

    # 按境界切池。每境 9 只（Lv 与该境转数区间一一对应）。
    pools: list[list[dict[str, Any]]] = []
    for _name, start, _qi, _mult in realm_mod.REALMS:
        lo, hi = start, start + realm_mod.TURNS_PER_REALM - 1
        pools.append([m for m in MONSTERS if lo <= m["level"] <= hi])

    # 本境 70% / 低一境 20% / 高一境 10%
    weighted: list[tuple[float, list[dict[str, Any]]]] = []
    weighted.append((0.70, pools[cur_idx]))
    below = cur_idx - 1
    above = cur_idx + 1
    if below >= 0:
        weighted.append((0.20, pools[below]))
    else:
        weighted[0] = (weighted[0][0] + 0.20, weighted[0][1])
    if above < len(pools):
        weighted.append((0.10, pools[above]))
    else:
        weighted[0] = (weighted[0][0] + 0.10, weighted[0][1])

    # 过滤空池（理论上不会有，防御性写法）
    weighted = [(w, p) for w, p in weighted if p]
    if not weighted:
        return min(MONSTERS, key=lambda m: m["level"])

    total = sum(w for w, _ in weighted)
    pick = random.random() * total
    acc = 0.0
    chosen = None
    for w, pool in weighted:
        acc += w
        if pick <= acc:
            chosen = random.choice(pool)
            break
    if chosen is None:
        chosen = random.choice(weighted[-1][1])

    # ---- 新手保护：怪 Lv 不超过玩家转数 + 3 ----
    #
    # 【为什么需要】
    # 妖兽属性按「玩家在该 Lv 的面板」反推，所以 Lv2 的怪是按 Lv2 玩家
    # （atk 28）设计的。但 1 转玩家只有 atk 18 —— 开局抽到 Lv2 怪就要 8 回合。
    # 而玩家成长是线性的（每转 +10 atk），妖兽也是线性的，
    # 两条线性线在小数值区间的相对差距比大数区间更刺眼。
    #
    # 只对「低转段」兜底，不影响中后期的越境强敌设计：
    # 转数 >= 10 后，3 级的差距在缩放器 ±0 微调下已经无关紧要。
    if lv <= 9:
        cap_lv = lv + 3
        pool = [m for m in MONSTERS if m["level"] <= cap_lv]
        if pool and chosen["level"] > cap_lv:
            same_or_lower = [m for m in pool if abs(m["level"] - lv) <= 3]
            if same_or_lower:
                chosen = random.choice(same_or_lower)
    return chosen


def roll_elite(chance: float = 0.08) -> bool:
    """按概率判定这场战斗是否刷出妖王。"""
    try:
        c = float(chance)
    except (TypeError, ValueError):
        c = 0.08
    return random.random() < max(0.0, min(1.0, c))


# ---------------------------------------------------------------------------
# 战力系统（v3.1 新增）
# ---------------------------------------------------------------------------
#
# 为什么要有战力：
#   玩家的属性是「基础 × 乘区」，乘区在 72 转里涨到 4 倍，
#   而妖兽数据是手填的、封顶在 lv28 —— 结果 5 转以后每战必胜，
#   70 转时玩家攻击是同级的 18 倍，战斗彻底失去意义。
#   战力把「攻/防/血」揉成一个标量，让玩家一眼判断能不能打，
#   也让妖兽可以用同一个标量反向缩放。
#
# 公式设计依据（2026-09-19 实测反推）：
#   用战斗主循环的真实伤害公式跑 150 场/档，统计胜率与属性的关系，
#   得到「战力比 ≥ 1.5 倍必胜、≤ 0.6 倍必败」的清晰分界。
#   权重取 攻击 2 / 防御 1.5 / 气血 0.5：
#     攻击 2.0 —— 伤害直接乘这个，主导胜负
#     防御 1.5 —— 减伤在伤害公式里是相减，边际收益高
#     气血 0.5 —— 是「血条长度」，不该和输出等价，权重压低
#   这个组合下裸装战力 1 转 78 → 大乘九转 16,356（209 倍），
#   与转数成长同步，量级也好读。

POWER_ATK_WEIGHT = 2.0
POWER_DEF_WEIGHT = 1.5
POWER_HP_WEIGHT = 0.5


def power_of(atk: int, defense: int, hp: int) -> int:
    """把三项属性揉成一个战力标量。"""
    return int(atk * POWER_ATK_WEIGHT + defense * POWER_DEF_WEIGHT + hp * POWER_HP_WEIGHT)


def player_power(player: Player, item_db: dict[str, dict] | None = None) -> int:
    """玩家当前战力（含乘区与装备）。

    装备算进来是刻意的：否则玩家换了法宝战力不变，就没人愿意换装了。
    """
    db = item_db or {}
    return power_of(player.atk(db), player.defense(db), player.max_hp(db))


# ---------------------------------------------------------------------------
# 妖兽动态缩放（v3.1 新增）
# ---------------------------------------------------------------------------
#
# 目标：不管玩家多少转，妖兽都应该「打得过但不轻松」。
#
# 做法：妖兽战力 = 玩家战力 × 目标比例，再按妖兽自身等级做微调，
#       最后反解出 atk / def / hp 三项。
#
# 反解思路（把战力拆回三项）：
#   先定结构比例 —— 妖兽的 攻:防:血 沿用原数据的风格，
#   然后按 战力 = atk*2 + def*1.5 + hp*0.5 整体等比放大。
#   等比放大会保持 攻/防/血 的相对关系，怪物的"手感"不变，
#   只是数值整体挪到玩家同档位 —— 这正是我们要的。

# 普通妖兽占玩家战力的比例（能赢，但有消耗）
#
# 【2026-09-19 v3.5：0.62 -> 0.72】
# 实测安全边际（_balance/lab/final_check.py，样本 700/档）：
#   0.62 -> 裸装 4.15 / 4.95 / 4.70 / 4.76（1/19/46/68 转）  碾压级，几乎输不了
#   0.72 -> 裸装 2.90 / 3.23 / 3.06 / 3.07                   轻松，但输了不冤 <- 采用
#   0.80 -> 裸装 2.10 / 2.38 / 2.28 / 2.27                   仍偏轻松
#
# 注意两件事：
#   1. 满装玩家在 0.72 下边际仍有 5.97 —— 「装备碾压」是设计意图，不是 bug。
#   2. 真正的「威胁」应当放在**秘境深层**（怪攻击超线性），
#      不要靠提这个常量制造 —— 挂机游戏里打怪变危险，玩家就不敢打了。
MONSTER_POWER_RATIO = 0.72
# 妖王（精英）占玩家战力的比例（险胜区间）
ELITE_POWER_RATIO = 0.92

# ---------------------------------------------------------------------------
# 妖兽等级偏差修正（v3.2 起置零，保留机制）
# ---------------------------------------------------------------------------
#
# 【为什么现在置零】
# 这个微调是 v3.1 为**旧数据**打的补丁：当时 MONSTERS 只有 19 只、
# Lv 封顶 28，玩家 46 转只能抽到 Lv28 的怪 —— 原始属性跟不上玩家，
# 所以需要按等级差把怪削弱（`adj=0.045`，最多 ±4 级 = ±18%）。
#
# v3.2 做了两件事，这个补丁就变成了**双重惩罚**：
#   1. MONSTERS 补满 Lv1~72，每只的属性都由「玩家在该 Lv 的面板」反推，
#      本身就已经是「同档位」的强度；
#   2. spawn_monster 改境界分池，怪 Lv 与玩家转数最多差 9，
#      且 30% 概率抽到邻境 —— 等级差是设计意图，不该再惩罚一次。
#
# 【实测数据】（真实引擎，150 场/档）
#   adj=0.045（旧）: 平均 4.00 回合，碾压率 80%
#   adj=0.0  （新）: 平均 4.61 回合，碾压率 10%     <- 符合方案目标（5 回合）
#
# 【为什么保留机制而不是删掉】
# 「越境强敌更强、低级怪更弱」这个语义本身是对的，
# 将来若再出现「数据覆盖不足」的场景（比如新增副本怪），
# 把 adj 调回一个很小的值即可，不必重写逻辑。
MONSTER_LEVEL_ADJUST = 0.0
# 等级偏差最多按 ±GAP_CAP 级计算。
# v3.2 放宽到 9：分池后最大等级差就是 9（跨 3 境各 9 个 Lv 位），
# 9 恰好是「本境 + 邻境」的完整跨度，再大就没有实际意义了。
MONSTER_LEVEL_GAP_CAP = 9

# ---------------------------------------------------------------------------
# 历练（普通打怪）的修为折扣
# ---------------------------------------------------------------------------
#
# v3.1 定位调整：修为主来源是「修炼 / 闭关 / 探索」三条挂机线，
# 打怪降级为**物品与灵石来源**，修为只给零头。
#
# 为什么还要给：如果打怪完全不给修为，玩家会觉得「打赢了没反馈」，
# 而且新人第一条线就是打怪，全砍掉会让开局失去正反馈。
#
# ⚠️ v3.6 修正（线上实测事故）：
#   这个常量是 **monster.exp 的倍数**，不是「修炼收益的百分比」。
#   monster.exp 本身就很小 —— 1 转怪 exp=7，于是
#       int(7 × 1.0(win) × 0.15) = **1 点**
#   而一次 /修炼 是 50 点。打怪只有修炼的 **0.16%**，升级要打 784 场。
#   旧注释写的「15% 的量级」是**比例**，实际绝对值差了 7.5 倍 ——
#   新人第一条线（打怪）完全没有正反馈。
#
#   实测结论：monster.exp 与 cultivate_gain 本来就是同一条曲线缩放的，
#   所以**一个全局乘数就能让「打怪/修炼」的比值在所有转数上恒定**。
#   取 1.5 时该比值稳定在 **21%**（1 / 10 / 20 / 40 / 64 转全部一致），
#   升级约 75 场（1 转）~ 864 场（64 转）。
#
#   打怪依旧不是升级主路：修炼约 600 点/小时，打怪约 126 点/小时。
#   定位仍是「灵石与物品来源」，只是零头终于看得见了。
#
# 注意这个折扣**只作用于历练/寻敌**（main.py 里显式传），
# 秘境有自己的结算（dungeon.py 的 dungeon_qi_reward），不走这里。
EXP_SCALE_FIELD = 1.5


def monster_stats(
    monster: dict[str, Any],
    player_turns: int | None = None,
    ascensions: int = 0,
    elite: bool = False,
) -> dict[str, int]:
    """把妖兽定义换算成实际战斗数值。

    player_turns 的三种传法：
      - None      -> 完全不缩放，返回静态数值（图鉴展示、老测试）
      - 正整数    -> 按该转数动态缩放（历练/挑战走这条）
      - 0 或负数  -> 视为「已经标定好的数值」，原样返回
                     （副本按层数自己算好属性后走这条，
                      避免被二次缩放。见 dungeon.py）

    缩放不改变妖兽之间的相对强弱 —— 低级的田鼠缩放后仍比史莱姆弱，
    只是整体挪到玩家当前档位。

    另外，带 `prescaled=True` 标记的怪（副本守卫）永远原样返回，
    因为副本已经按层数把属性算好了，不能再被按转数缩一次。
    """
    lv = monster["level"]

    # prescaled 的语义是「原样返回」，一个字段都不改 ——
    # 所以必须在算 base（含 hp + lv*15 的等级加成）**之前**返回。
    # 放在之后虽然当前副本怪 lv=0 恰好躲过，但那是巧合不是设计：
    # 只要副本以后给怪配个非零 level，血量就会凭空多 lv*15 而无人察觉。
    if monster.get("prescaled"):
        return {
            "hp": max(1, int(monster["hp"])),
            "atk": int(monster["atk"]),
            "defense": int(monster["defense"]),
        }

    # 实际生命 = hp + level × 15（见 items.py 顶部说明）。
    # 这是 v2 就有的等级加成机制，图鉴展示与战斗都走这条。
    base = {
        "hp": monster["hp"] + lv * 15,
        "atk": monster["atk"],
        "defense": monster["defense"],
    }
    if player_turns is None:
        return base
    if int(player_turns) <= 0:
        # 已标定，原样返回（副本等场景自己控制难度）
        return base

    turns = realm_mod.clamp_turns(player_turns)

    # ---- 1. 算出玩家战力（裸装，只跟转数有关） ----
    # 刻意不含装备：如果妖兽跟着装备一起涨，换装就毫无意义了。
    # 装备带来的战力优势，应该实实在在体现在「更容易打赢」上。
    asc = max(0, int(ascensions or 0))
    sm = realm_mod.stat_mult(turns, asc, 0)
    p_atk = int(base_atk(turns) * sm)
    p_def = int(base_def(turns) * sm)
    p_hp = int(base_hp(turns) * sm)
    pp = power_of(p_atk, p_def, p_hp)

    # ---- 2. 定目标战力 ----
    ratio = ELITE_POWER_RATIO if elite else MONSTER_POWER_RATIO
    # 等级偏差做微调，但**封顶**（见 MONSTER_LEVEL_GAP_CAP 注释）
    gap = max(-MONSTER_LEVEL_GAP_CAP, min(MONSTER_LEVEL_GAP_CAP, lv - turns))
    ratio *= 1.0 + gap * MONSTER_LEVEL_ADJUST
    ratio = max(0.15, min(1.6, ratio))
    target_power = pp * ratio

    # ---- 3. 反解：按原始结构等比放大 ----
    base_power = power_of(base["atk"], base["defense"], base["hp"])
    if base_power <= 0:
        scale = 1.0
    else:
        scale = target_power / base_power

    # 血量的缩放稍保守一点：血太厚会拖长战斗回合数
    hp_scale = 1.0 + (scale - 1.0) * 0.8

    return {
        "hp": max(1, int(base["hp"] * hp_scale)),
        "atk": max(1, int(base["atk"] * scale)),
        "defense": max(0, int(base["defense"] * scale)),
    }


def _apply_level_up(player: Player, item_db: dict[str, dict]) -> int:
    """循环消耗修为精进转数，返回精进了几转。

    必须用 while 而不是 if：一次战斗可能拿到足够连转好几转的修为。
    转数上限是 realm.MAX_TURNS（72）。
    """
    gained = 0
    # 上限保护，防止公式异常导致死循环
    while player.level < realm_mod.MAX_TURNS:
        need = exp_needed(player.level)
        if need <= 0 or player.exp < need:
            break
        player.exp -= need
        player.level += 1
        gained += 1

    if gained:
        # 精进后气血补满（含装备加成）
        player.hp = player.max_hp(item_db)
    return gained


# ---------------------------------------------------------------------------
# 战斗主循环
# ---------------------------------------------------------------------------

def fight(
    player: Player,
    monster: dict[str, Any],
    item_db: dict[str, dict],
    inventory_limit: int | None = None,
    elite: bool = False,
    count_death: bool = True,
    exp_scale: float = 1.0,
) -> BattleResult:
    """跑一场回合制战斗，并直接修改传入的 player 对象。

    v3 变更：
      - 奖励从经验改为修为。
      - 装配的秘典会在战斗中自动释放（主动技按冷却，被动全程生效）。
      - 妖兽可以带元素属性，与秘典抗性互动。

    v3.1 变更：
      - 妖兽数值改为**按玩家转数动态缩放**（见 monster_stats）。
        这是为了修「lv28 封顶导致 5 转后每战必胜」的问题。
      - count_death=False 时，败北不计入 total_deaths。
        秘境（试炼）用它实现「失败不计死亡」。
      - 新增 exp_scale：普通历练的修为主来源已改为「修炼 / 闭关 / 探索」，
        打怪降级为**物品与灵石来源**，修为只给零头（见 EXP_SCALE_FIELD）。
        秘境奖励不走这里（它自己在 run_dungeon 里结算），不受影响。
    """
    # 缩放后的数值。elite 的加成已经并进 monster_stats 的 ratio 里，
    # 不再在这里乘 1.5（否则会放大两次）。
    stats = monster_stats(monster, player.turns, player.ascensions, elite)

    m_name = f"妖王 · {monster['name']}" if elite else monster["name"]
    m_hp = stats["hp"]
    m_def = stats["defense"]

    # ---- 被动效果汇总 ----
    bag = PassiveBag(player)

    # ---- 玩家面板（含乘区） ----
    p_atk = player.atk(item_db)
    if bag.atk_pct:
        p_atk = int(p_atk * (1 + bag.atk_pct / 100))
    p_def = player.defense(item_db)
    if bag.def_pct:
        p_def = int(p_def * (1 + bag.def_pct / 100))
    p_max_hp = player.max_hp(item_db)
    if bag.hp_pct:
        p_max_hp = int(p_max_hp * (1 + bag.hp_pct / 100))

    m_atk = stats["atk"]
    monster_elem = monster.get("element", "")

    # v3.1：装备带来的穿甲与套装增伤（只作用于玩家打出去的伤害）
    penetration = player.penetration()
    damage_bonus = player.damage_bonus()

    # ---- v3.4：十品装备的三套机制 ----
    # 【设计原则】三套各有自己的收益通道，互不重叠：
    #   物穿套 → 穿甲（数值）+ 概率破防 + 暴击
    #   法穿套 → 穿甲 ×1.15 + 术法增伤 + 冷却缩减
    #   肉盾套 → 减伤 + 反伤 + 低血额外减免
    #
    # 这里一次性取出，循环里只查变量。每回合重新调方法在
    # 30 回合上限下会有不必要的开销。
    avoid_def_pct = player.avoid_def_pct()            # 破防概率（%）
    crit_rate = min(1.0, CRIT_BASE_RATE + player.crit_bonus_pct() / 100.0)
    reflect_pct = player.reflect_pct() / 100.0        # 反伤比例（小数）
    dr_pct = player.damage_reduce_pct() / 100.0       # 常驻减伤（小数）
    low_hp_dr_pct = player.low_hp_extra_dr_pct() / 100.0   # 低血额外减免（小数）
    cd_reduce = player.cd_reduce()                    # 秘典冷却缩减（回合）
    magic_amp = player.set_mech("magic_dmg_bonus") / 100.0   # 术法增伤（小数）
    active_set = player.active_set()

    # 本场战斗的机制触发计数（让套装「看得见」）
    mech_hits = {"avoid_def": 0, "crit": 0, "reflect": 0, "dr": 0, "magic_amp": 0}

    result = BattleResult(outcome=OUTCOME_DRAW, monster_name=m_name)
    result.log.append(f"遭遇 {m_name}（Lv.{monster['level']}）")

    # ---- 秘典战斗状态 ----
    scriptures = getattr(player, "scriptures", {}) or {}
    equipped = getattr(player, "equipped_scriptures", []) or []
    sc_state = make_battle_state(scriptures, equipped)
    result.skills_used = []

    for turn in range(1, MAX_BATTLE_TURNS + 1):
        # ---- 低血线防御加成（金之秘典三阶「不坏」） ----
        turn_def = p_def
        if bag.def_low_hp_pct and p_max_hp > 0:
            if player.hp / p_max_hp < 0.3:
                turn_def = int(p_def * (1 + bag.def_low_hp_pct / 100))

        # ---- 玩家出手：主动技优先，否则普攻 ----
        acts = ready_actives(sc_state)
        acted = False
        for name, tier, info in acts:
            eff = info.get("effect", {})
            handler = EFFECT_HANDLERS.get(eff.get("kind", ""))
            if handler is None:
                continue
            ctx = {
                "player": player,
                "item_db": item_db,
                "p_atk": p_atk,
                "p_max_hp": p_max_hp,
                "target_hp": m_hp,
                "target_max_hp": stats["hp"],
                "target_def": m_def,
                # v3.1：让秘典技能也能吃到装备的穿甲与套装增伤。
                # 处理器可以自己用（比如魔法伤害想按穿甲后的防御重算），
                # 也可以用下面的兜底逻辑统一乘增伤。
                "penetration": penetration,
                "damage_bonus": damage_bonus,
            }
            out = handler(ctx, eff)
            if out.get("damage"):
                dmg_out = int(out["damage"])
                # 套装增伤兜底：处理器没自己算增伤的，这里统一乘一次。
                # 用 handler 声明的 "bonus_applied" 标记避免重复乘。
                if damage_bonus > 0 and not out.get("bonus_applied"):
                    dmg_out = int(dmg_out * (1.0 + damage_bonus))
                m_hp -= dmg_out
            if out.get("heal"):
                player.hp = min(p_max_hp, player.hp + out["heal"])
            if out.get("stat") == "defense":
                # 铁壁：本回合防御提升
                turn_def = int(turn_def * (1 + abs(out["value"]) / 100))
            start_cooldown(sc_state, name, tier)
            skill_label = f"{name}·{info.get('name', '')}"
            if skill_label not in result.skills_used:
                result.skills_used.append(skill_label)
            result.log.append(f"第 {turn} 回合：{out.get('text', '')}")
            acted = True
            break  # 一回合只放一个主动技

        if not acted:
            # v3.1：玩家普攻吃「穿甲」与「套装增伤」（法穿套专属）
            # v3.4：装备机制统一走 ATTACK_MODIFIERS 注册表
            #
            # 注册表的设计意图：老板说「以后中上品可以加入高级乘区」，
            # 意味着这个位置还会长出新机制。走注册表的写法下，
            # 加新机制只需写个 @register_attack_modifier 函数，
            # 主循环这三行不用动。
            dmg = roll_damage_tuned(
                p_atk,
                m_def,
                penetration=penetration,
                damage_bonus=damage_bonus,
            )

            # 问一遍所有装备机制：要不要改这一击
            tags: list[str] = []
            mult = 1.0
            flat = 0
            am_ctx = {
                "p_atk": p_atk, "p_def": p_def,
                "target_def": m_def, "target_hp": m_hp,
                "penetration": penetration,
                "current_dmg": dmg,
                "avoid_def_pct": avoid_def_pct,
                "crit_rate": crit_rate,
                "magic_dmg_bonus": magic_amp,
                "rng": random,
            }
            for mod in ATTACK_MODIFIERS.values():
                out = mod(am_ctx)
                if not out:
                    continue
                if out.get("dmg_mult"):
                    mult *= float(out["dmg_mult"])
                if out.get("dmg_flat"):
                    flat += int(out["dmg_flat"])
                if out.get("tag"):
                    tags.append(str(out["tag"]))
                if out.get("count"):
                    mech_hits[out["count"]] = mech_hits.get(out["count"], 0) + 1

            if mult != 1.0:
                dmg = int(dmg * mult)
            if flat:
                dmg += flat
            dmg = max(1, dmg)
            m_hp -= dmg

            if tags:
                result.log.append(
                    f"第 {turn} 回合：{'·'.join(tags)}！造成 {dmg} 点伤害"
                )
            else:
                result.log.append(f"第 {turn} 回合：你造成 {dmg} 点伤害")

            # 追风：命中叠攻速
            if bag.atk_stack_pct:
                cur = sc_state["stacks"].setdefault("__atk__", {}).get("v", 0.0)
                cur = min(bag.atk_stack_max, cur + bag.atk_stack_pct)
                sc_state["stacks"]["__atk__"]["v"] = cur
                p_atk = int(player.atk(item_db) * (1 + (bag.atk_pct + cur) / 100))

        # ---- 附加伤害（燎原） ----
        if bag.atk_pct and any(
            info.get("effect", {}).get("kind") == "add_damage"
            for _, _, info in all_passives(scriptures, equipped)
        ):
            extra = max(1, int(player.atk(item_db) * 0.2))
            m_hp -= extra

        if m_hp <= 0:
            # 按剩余气血分档：满血赢是碾压，残血赢是险胜
            ratio = player.hp / p_max_hp if p_max_hp > 0 else 1.0
            if ratio >= CRUSH_HP_RATIO:
                result.outcome = OUTCOME_CRUSH
            elif ratio < NARROW_HP_RATIO:
                result.outcome = OUTCOME_NARROW
            else:
                result.outcome = OUTCOME_WIN
            result.turns = turn
            break

        # ---- 怪物反击 ----
        # 闪避判定（风之秘典二阶「轻身」）
        if bag.dodge_chance and random.random() < bag.dodge_chance:
            result.log.append("　　　　你身形一闪，避开了攻击")
        else:
            incoming = roll_damage(m_atk, turn_def)
            # 元素抗性（火之秘典二阶「控火」）
            if monster_elem:
                res = bag.resist_for(monster_elem)
                if res > 0:
                    incoming = max(1, int(incoming * (1 - res)))

            # ---- v3.4：肉盾套的减伤（走 DEFENSE_MODIFIERS 注册表）----
            # 常驻减伤 + 低血额外减免，两者是**乘法叠加**：
            #   受击 × (1 - 常驻) × (1 - 低血)
            # 九品满配：×(1-0.30)×(1-0.15) = ×0.595，即残血时只受 59.5% 伤害。
            dr_tags: list[str] = []
            dr_mult = 1.0
            dm_ctx = {
                "p_atk": p_atk, "p_def": p_def, "p_max_hp": p_max_hp,
                "p_hp": player.hp, "incoming": incoming,
                "dr_pct": dr_pct,
                "low_hp_dr_pct": low_hp_dr_pct,
                "reflect_pct": reflect_pct,
                "rng": random,
            }
            reflect_ratio = 0.0
            for mod in DEFENSE_MODIFIERS.values():
                out = mod(dm_ctx)
                if not out:
                    continue
                if out.get("dmg_mult") is not None:
                    dr_mult *= float(out["dmg_mult"])
                if out.get("reflect"):
                    reflect_ratio = float(out["reflect"])
                if out.get("tag"):
                    dr_tags.append(str(out["tag"]))
                if out.get("count"):
                    mech_hits[out["count"]] = mech_hits.get(out["count"], 0) + 1

            if dr_mult != 1.0:
                incoming = max(1, int(incoming * dr_mult))

            player.hp -= incoming
            if dr_tags:
                result.log.append(
                    f"　　　　{m_name} 反击 {incoming} 点（{'·'.join(dr_tags)}）"
                )
            elif dr_mult != 1.0:
                result.log.append(f"　　　　{m_name} 反击 {incoming} 点（护体减伤）")
            else:
                result.log.append(f"　　　　{m_name} 反击 {incoming} 点")

            # 反伤（金之秘典「锐金」 + v3.4 肉盾套）
            # 两路反伤**相加**而不是取大：秘典走修为体系，装备走品阶体系，
            # 两个系统各自独立成长，能叠加才符合直觉。
            total_reflect = bag.counter_ratio + reflect_ratio
            if total_reflect > 0:
                back = max(1, int(incoming * total_reflect))
                m_hp -= back
                if bag.counter_ratio > 0 and reflect_ratio > 0:
                    result.log.append(f"　　　　锐金与护体共鸣，反伤 {back} 点")
                else:
                    result.log.append(f"　　　　反伤 {back} 点")
                if m_hp <= 0:
                    result.outcome = OUTCOME_WIN
                    result.turns = turn
                    break

        # ---- 回合末：持续流失（土之秘典三阶「陷阵」） ----
        if bag.dot_ratio > 0:
            dot = max(1, int(stats["hp"] * bag.dot_ratio))
            m_hp -= dot
            result.log.append(f"　　　　陷阵使 {m_name} 流失 {dot} 点气血")
            if m_hp <= 0:
                result.outcome = OUTCOME_WIN
                result.turns = turn
                break

        # ---- 回合末：回复（水之秘典二阶「润泽」） ----
        if bag.regen_ratio > 0:
            heal = max(1, int(p_max_hp * bag.regen_ratio))
            player.hp = min(p_max_hp, player.hp + heal)

        if player.hp <= 0:
            player.hp = 0
            result.outcome = OUTCOME_LOSE
            result.turns = turn
            break

        # 秘典冷却推进
        # v3.4：法穿套上品额外推进 cd_reduce 回合（相当于冷却缩减 1~2 回合）。
        # 用「多推几次 tick」而不是改 start_cooldown —— tick 是幂等的，
        # 玩家卸下装备后立即恢复原状，不会留下脏状态。
        tick_cooldowns(sc_state)
        for _ in range(cd_reduce):
            tick_cooldowns(sc_state)
    else:
        # for 正常跑完 = 打满回合上限，判平局
        result.outcome = OUTCOME_DRAW
        result.turns = MAX_BATTLE_TURNS
        result.log.append(f"双方鏖战 {MAX_BATTLE_TURNS} 回合未分胜负")

    # ------------------------------------------------------------------
    # 结算
    # ------------------------------------------------------------------
    if result.won:
        player.total_kills += 1

        # 奖励基数：妖王 ×2
        base_exp = monster.get("exp", 0) * (2 if elite else 1)
        lo, hi = monster.get("gold", (0, 0))
        if elite:
            lo, hi = lo * 2, hi * 2
        base_gold = random.randint(lo, hi) if hi >= lo else 0

        # 修为按结果分档打折/加成
        mult = OUTCOME_EXP_MULT.get(result.outcome, 1.0)
        result.exp_gain = int(base_exp * mult * exp_scale)
        result.gold_gain = base_gold

        player.exp += result.exp_gain
        player.gold += result.gold_gain

        # 险胜额外增加掉落概率
        drops = monster.get("drops", [])
        if result.outcome == OUTCOME_NARROW and drops:
            drops = [
                {**d, "rate": min(1.0, float(d["rate"]) + NARROW_DROP_BONUS)}
                for d in drops
            ]
        if elite and drops:
            drops = [{**d, "rate": min(1.0, float(d["rate"]) * 2)} for d in drops]

        dropped = roll_drop(drops)
        if dropped:
            if player.add_item(dropped, limit=inventory_limit):
                result.drop = dropped
            else:
                result.drop_lost = True

        levels = _apply_level_up(player, item_db)
        if levels:
            result.leveled_up = True
            result.levels_gained = levels

    elif result.outcome == OUTCOME_DRAW:
        # 平局给安慰奖修为
        mult = OUTCOME_EXP_MULT.get(OUTCOME_DRAW, 0.0)
        result.exp_gain = int(monster.get("exp", 0) * mult * exp_scale)
        player.exp += result.exp_gain
        levels = _apply_level_up(player, item_db)
        if levels:
            result.leveled_up = True
            result.levels_gained = levels

    elif result.outcome == OUTCOME_LOSE:
        # v3.1：秘境（试炼）失败不计死亡。
        # count_death=False 时只跳过统计，不改其他任何行为。
        if count_death:
            player.total_deaths += 1
        # 取消一切死亡惩罚。
        # 不掉灵石、不掉修为、不掉装备 —— 死亡只产生「休整时间」这一项成本。
        # 休整的启动交给调用方（因为时长来自配置）。

    result.hp_after = player.hp
    result.mech_hits = mech_hits
    result.active_set = active_set
    return result


def try_flee(player: Player, item_db: dict[str, dict]) -> tuple[bool, int]:
    """尝试逃跑（战斗已全自动，保留此函数仅为兼容）。

    返回 (是否成功, 失败时掉的血)。
    """
    if random.random() < FLEE_SUCCESS_RATE:
        return True, 0

    dmg = max(1, int(player.defense(item_db) * 0.5))
    player.hp = max(0, player.hp - dmg)
    return False, dmg


# ---------------------------------------------------------------------------
# 修炼（主动一次性）
# ---------------------------------------------------------------------------

def cultivate(player: Player, item_db: dict[str, dict],
              gain_mult: float = 1.0) -> tuple[int, int]:
    """主动修炼：立刻加修为。返回 (获得修为, 精进转数)。

    v3 修正：这才是「修炼」—— 主动的一次性收益。
    挂机收益在 settle_seclusion()（闭关）。
    """
    gain = int(realm_mod.cultivate_gain(player.turns) * max(0.0, gain_mult))
    player.exp += gain
    levels = _apply_level_up(player, item_db)
    return gain, levels


# ---------------------------------------------------------------------------
# 闭关（挂机结算）
# ---------------------------------------------------------------------------

def settle_training(player: Player, item_db: dict[str, dict]) -> TrainResult:
    """结算闭关收益，并**退出闭关**（函数名保留兼容，语义 = 闭关）。

    v3.6 起闭关是状态型挂机，和探索对称：
      - 时长从 player.train_start_ts 算起（不再是 last_train_ts）；
      - **结算即出关**：train_start_ts 归零，玩家立刻可以做别的事；
      - 上限 TRAIN_MAX_HOURS 小时，超出部分丢弃。

    对照旧实现的坑：以前只有一个 last_train_ts 兼任「开始计时 / 结算基线 /
    冷却起点」，导致进入闭关后 1 小时内结算不了、且第二次起进不去闭关分支。
    详见 player.Player.train_start_ts 的注释。

    v3 新增：走火入魔 —— 时长越长概率越高，触发后收益减半。
    """
    now = time.time()
    result = TrainResult()

    start = float(player.train_start_ts or 0.0)
    if start <= 0:
        # 不在闭关，没什么可结算的
        return result

    # 先退出闭关：无论后面结算成功与否，这次调用都代表「出关」。
    # 放在最前面，保证任何提前 return 的路径都不会把玩家永久卡在闭关里。
    player.train_start_ts = 0.0

    elapsed_hours = (now - start) / 3600.0
    if elapsed_hours <= 0:
        return result

    capped = elapsed_hours > TRAIN_MAX_HOURS
    effective = min(elapsed_hours, TRAIN_MAX_HOURS)

    # 不足 6 分钟不给收益，避免刷指令
    if effective < 0.1:
        return result

    result.hours = round(effective, 1)
    result.capped = capped

    base = realm_mod.seclusion_qi_per_hour(player.turns) * effective

    # 走火入魔判定
    chance = deviation_chance(effective)
    if chance > 0 and random.random() < chance:
        result.deviation = True
        base *= QI_DEVIATION_PENALTY

    result.exp_gain = int(base)
    player.exp += result.exp_gain

    # last_train_ts 降级为「上次闭关结算时间」的纯记录字段，
    # 不再参与时长计算 —— 那是 train_start_ts 的职责。
    player.last_train_ts = now

    levels = _apply_level_up(player, item_db)
    if levels:
        result.leveled_up = True
        result.levels_gained = levels

    return result


# ---------------------------------------------------------------------------
# 探索（挂机，产出秘典）
# ---------------------------------------------------------------------------

# 探索产出表：(类别, 权重)。类别 -> 实际物品的映射在 _roll_explore_drop 里。
EXPLORE_TABLE = (
    ("qi", 35),
    ("consumable", 30),
    ("equipment", 17),
    # relic 8 -> 14（2026-09-19 数值平衡）：
    #   秘典满阶原需 417 小时，占满了整个探索线还有余。
    #   权重提到 14 后降为 161 小时 —— 这仍在「长期目标」量级，
    #   但不再是玩家永远看不到头的一堵墙。
    #   注意 total 会从 100 涨到 106，所以各权重实际是相对值。
    ("relic", 14),
    ("gold", 8),
    ("special", 2),
)

# 探索每小时触发几次拾取判定
#
# 1.5 -> 2.0（2026-09-19 数值平衡）：与 relic 权重一起，
# 把秘典满阶耗时从 417h 压到 161h。见 EXPLORE_TABLE 上方注释。
EXPLORE_PICKS_PER_HOUR = 2.0

# 特殊事件（占位，后续扩展）
EXPLORE_SPECIALS = ("偶遇前辈指点，修为大进", "拾得无主储物袋", "参悟天象，心境澄明")


def _roll_explore_kind() -> str:
    kinds = [k for k, _w in EXPLORE_TABLE]
    weights = [w for _k, w in EXPLORE_TABLE]
    return random.choices(kinds, weights=weights, k=1)[0]


def _roll_explore_drop(player: Player, item_db: dict[str, dict],
                       kind: str) -> tuple[str, str | None]:
    """按类别产出一件东西。返回 (类别, 物品名或 None)。"""
    from .items import (
        CONSUMABLES,
        EQUIPMENTS,
        decorate,
        explore_consumables,
        explore_equipment_pool,
        roll_quality,
    )
    from .scriptures import SCRIPTURE_NAMES, relic_name

    if kind == "consumable":
        pool = explore_consumables()
        if pool:
            return kind, random.choice(pool)
        return kind, None

    if kind == "equipment":
        pool = explore_equipment_pool(player.turns)
        if pool:
            return kind, decorate(random.choice(pool), roll_quality())
        return kind, None

    if kind == "relic":
        # 秘典残页：随机一条大道
        return kind, relic_name(random.choice(SCRIPTURE_NAMES))

    return kind, None


def settle_explore(player: Player, item_db: dict[str, dict],
                   inventory_limit: int | None = None) -> ExploreResult:
    """结算一次探索挂机。

    由调用方保证玩家确实在 explore_start_ts 起进入了探索。
    低于最小时长（30 分钟）给 0 收益。
    """
    result = ExploreResult()
    elapsed = player.explore_elapsed()
    player.explore_start_ts = 0.0
    if elapsed < EXPLORE_MIN_SECONDS:
        player.explore_start_ts = 0.0
        return result

    capped = elapsed > EXPLORE_MAX_SECONDS
    effective = min(elapsed, EXPLORE_MAX_SECONDS)
    hours = effective / 3600.0
    result.hours = round(hours, 1)
    result.capped = capped

    # 修为：探索给的是「少量」，约为闭关的 1/3
    qi = int(realm_mod.seclusion_qi_per_hour(player.turns) * hours / 3)
    result.qi_gain = max(0, qi)

    # 拾取次数
    picks = int(hours * EXPLORE_PICKS_PER_HOUR)
    # 至少 1 次，最多 12 次，避免极端值
    picks = max(1, min(12, picks))

    for _ in range(picks):
        kind = _roll_explore_kind()
        if kind == "qi":
            result.qi_gain += realm_mod.cultivate_gain(player.turns) // 2
        elif kind == "gold":
            amount = 10 + player.turns * 4
            result.gold_gain += random.randint(amount // 2, amount)
        elif kind == "special":
            result.qi_gain += realm_mod.cultivate_gain(player.turns)
            # 特殊事件只是文案，不是物品 —— 放独立字段，
            # 不能塞进 items，否则会被显示成「所得：参悟天象，心境澄明」，
            # 且会让下游把文案当物品名去查表。
            result.events.append(random.choice(EXPLORE_SPECIALS))
        else:
            _kind, item_name = _roll_explore_drop(player, item_db, kind)
            if item_name:
                if kind == "relic":
                    result.relics.append(item_name)
                else:
                    result.items.append(item_name)

    # 写入玩家
    player.exp += result.qi_gain
    player.gold += result.gold_gain

    for name in result.items + result.relics:
        player.add_item(name, limit=inventory_limit)

    _apply_level_up(player, item_db)
    player.last_explore_ts = time.time()
    return result


# ---------------------------------------------------------------------------
# 天劫
# ---------------------------------------------------------------------------

def tribulate(player: Player, item_db: dict[str, dict]) -> TribulationResult:
    """渡天劫：抗住 9 道雷。

    v3 设计（老板拍板）：**不是打赢，而是抗住**。
    你的气血就是护盾 —— 准备不足（气血不够、没带渡劫丹）的扛不住。

    9 道全抗住 -> 飞升（转生）
    5~8 道     -> 渡劫失败，掉 2 转，休整 10 分钟
    <5 道      -> 渡劫失败，掉 3 转，休整 30 分钟
    """
    from .player import (
        TRIBULATION_BOLTS,
        TRIBULATION_PASS_BOLTS,
        TRIBULATION_FAIL_HIGH,
        TRIBULATION_FAIL_LOW,
    )

    max_hp = player.max_hp(item_db)
    # 渡劫时气血按当前值算，逼玩家满血来渡
    survived, damages = tribulation_bolts(player.hp, max_hp)

    result = TribulationResult(
        survived=survived,
        total=TRIBULATION_BOLTS,
        damages=damages,
    )

    if survived >= TRIBULATION_PASS_BOLTS:
        result.passed = True
        result.ascended = True
        # 飞升的实际数据变更交给调用方（要做转生处理）
        return result

    if survived >= 5:
        lost, rest = TRIBULATION_FAIL_HIGH
    else:
        lost, rest = TRIBULATION_FAIL_LOW

    result.turns_lost = lost
    result.rest_seconds = rest

    # 掉转（不低于 1）
    player.level = max(1, player.turns - lost)
    player.hp = 0
    player.start_rest(rest, item_db=item_db)

    return result


def ascend(player: Player, item_db: dict[str, dict]) -> None:
    """飞升转生：境界归 1，保留积累，转生次数 +1。

    保留：灵石、储物袋、法宝、秘典、突破次数、统计
    重置：转数归 1、修为清零、气血回满
    获得：ascensions +1（基础属性 ×1.05）
    """
    player.ascensions += 1
    player.level = 1
    player.exp = 0
    player.full_heal(item_db)
    player.rest_until = 0.0
