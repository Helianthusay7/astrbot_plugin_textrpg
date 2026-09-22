"""境界体系：八境九转，飞升转生。

本模块只做「转数 ↔ 境界」的映射与数值缩放，**不碰玩家对象**，
方便单测，也让平衡调整集中在一处。

设计约定（很重要，改代码时不要违反）：
    1. 转数（total_turns）是 1~72 的整数，对应游戏内的「境界 + 转」。
       1~9 炼气期、10~18 筑基期 …… 64~72 大乘期。
    2. 转数在存档里复用 v2 的 `level` 字段（兼容老存档），
       但语义已经变了：不再是 200 级的「等级」。
    3. 境界倍率、转生倍率、突破倍率三者相乘 = 总乘区（stat_mult）。
       最终属性 = 基础属性 × stat_mult + 装备加成。
       加任何新系统（丹药 buff / 活动加成 / 秘典被动），
       都是往 stat_mult 里插一项乘区，**不改战斗代码**。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 境界表
# ---------------------------------------------------------------------------

MAX_TURNS = 72
TURNS_PER_REALM = 9

# (境界名, 起始转数, 起始累计修为门槛, 境界倍率)
#
# 注意第 3 个字段「起始累计修为门槛」：这里填的是**占位值**，
# 模块加载到最后会被 `_sync_realm_thresholds()` 用真实曲线覆盖。
# 保留它是因为大量代码/文档按位置索引 `REALMS[i][2]`，
# 不留字段会连锁改动，得不偿失。
REALMS: list[tuple[str, int, int, float]] = [
    ("炼气期", 1, 0, 1.0),
    ("筑基期", 10, 2_000, 1.25),
    ("金丹期", 19, 8_000, 1.5),
    ("元婴期", 28, 25_000, 1.8),
    ("化神期", 37, 70_000, 2.2),
    ("炼虚期", 46, 180_000, 2.7),
    ("合体期", 55, 450_000, 3.3),
    ("大乘期", 64, 1_100_000, 4.0),
]

# 飞升所需（大乘九转圆满 + 这个累计修为）
ASCENSION_QI = 2_500_000

REALM_NAMES: list[str] = [r[0] for r in REALMS]

# 转数区间 -> 境界下标，加速查找
_REALM_INDEX: list[int] = []
for _idx, (_name, _start, _qi, _mult) in enumerate(REALMS):
    # 每个境界占 TURNS_PER_REALM 转
    _REALM_INDEX.extend([_idx] * TURNS_PER_REALM)


def clamp_turns(turns: int) -> int:
    """把转数钳制到合法区间 1~72。

    老存档的 level 可能远超 72（v2 上限是 200），
    读进来直接按大乘九转算，不让它崩。
    """
    try:
        t = int(turns)
    except (TypeError, ValueError):
        return 1
    return max(1, min(MAX_TURNS, t))


def realm_index_of(turns: int) -> int:
    """转数 -> 境界下标（0~7）。"""
    t = clamp_turns(turns)
    return _REALM_INDEX[t - 1]


def realm_name_of(turns: int) -> str:
    """转数 -> 境界名，如「金丹期」。"""
    return REALMS[realm_index_of(turns)][0]


def turn_in_realm(turns: int) -> int:
    """转数在本境界内的第几转（1~9）。"""
    t = clamp_turns(turns)
    idx = realm_index_of(t)
    start = REALMS[idx][1]
    return t - start + 1


def realm_mult(turns: int) -> float:
    """境界倍率（乘区 A）。"""
    return REALMS[realm_index_of(turns)][3]


def realm_label(turns: int) -> str:
    """完整展示，如「金丹期 3 转」。"""
    return f"{realm_name_of(turns)} {turn_in_realm(turns)} 转"


# ---------------------------------------------------------------------------
# 修为曲线（2026-09-19 重做）
# ---------------------------------------------------------------------------
#
# 【为什么推倒重来】
# 旧实现把「境界门槛」（REALMS 第 3 个字段）直接当每转需求用，
# 于是需求跨境按 ×4 ×4 ×3.1 ×2.8 ×2.6 ×2.5 ×2.4 增长，
# 而产出跨境恒定 ×2 —— 缺口逐境累积，
# 到大乘九转单转要 242 万修为（纯闭关 126 小时），成了一堵墙。
#
# 【新模型】
# 关键洞察：玩家的每小时产出 **不是** 单条线，而是三条线并行：
#     /修炼  5 分钟冷却 -> 每小时最多 12 次，每次 cultivate_gain
#     /闭关  1 小时冷却 -> 每小时等效 seclusion_qi_per_hour = cultivate_gain × 3
#     /探索  30 分钟冷却 -> 每小时等效 cultivate_gain × 1
#     合计 = cultivate_gain × 16
# 三条线冷却互相独立（last_cultivate_ts / last_train_ts / last_explore_ts），
# 所以确实能并行，修炼是大头（占 75%）。
#
# 耗时公式：  境界耗时 = 该境 9 转总需求 / 该境每小时产出
# 要让耗时呈「温和递增」，必须让 需求增速/r 略大于 产出增速/g。
# 取 g = 2.0（产出跨境翻倍，好读好调）、q = r/g = 1.422：
#     炼气 11h → 筑基 16h → 金丹 23h → 元婴 32h
#       → 化神 46h → 炼虚 65h → 合体 93h → 大乘 110h
#     合计 396 小时 ≈ 16.5 天
# 首末比 10x —— 后期慢，但不是墙。
#
# 【境内步进】
# 每转只涨 5.5%（旧版 15% 太陡，境内就爬得吃力）。
# 第 9 转取「下一境基准的 0.675 倍」，等价于两端算术均值，
# 保留 ~1.4x 的跳变 —— 这是「突破」的仪式感，不是 bug。
#
# 【改这张表时要连带做的事】
#   1. QI_BASE 和 CULT_BASE 必须成对调，只动一个会破坏边界
#   2. 改完必须重跑 selftest.py 的「数值健康度」分节
#   3. 产出线权重若变（如改冷却），上面的 ×16 要跟着变
# ---------------------------------------------------------------------------

# 每个境界的「基准需求」（该境界第 1 转需要的修为）
# 跨境递增 r = 2.844
QI_BASE: list[int] = [784, 2_229, 6_340, 18_030, 51_276, 145_830, 414_741, 1_179_524]

# 每转在基准上的步进（境内）
TURN_QI_STEP = 0.055

# 第 9 转（突破前的最后一转）相对「下一境基准」的系数
# 取值约等于两端算术均值 / 下一境基准 ≈ 0.675，制造突破前的积累感
_BREAKTHROUGH_NEED_RATIO = 0.675


def realm_base_qi(turns: int) -> int:
    """当前境界的起始门槛（展示用，如「筑基期门槛 2000」）。"""
    return REALMS[realm_index_of(turns)][2]


def qi_needed(turns: int) -> int:
    """从当前转精进到下一转，需要多少修为。

    已经到了 72 转返回 0（满级，不再需要修为，直接去渡劫）。
    """
    t = clamp_turns(turns)
    if t >= MAX_TURNS:
        return 0
    idx = realm_index_of(t)
    n = turn_in_realm(t)
    # 第 9 转：攒够就能突破，取下一境基准的一个比例
    if n == TURNS_PER_REALM:
        next_base = QI_BASE[min(idx + 1, len(QI_BASE) - 1)]
        return int(next_base * _BREAKTHROUGH_NEED_RATIO)
    return int(QI_BASE[idx] * (1 + (n - 1) * TURN_QI_STEP))


def total_qi_to(turns: int) -> int:
    """从 1 转累积到指定转数所需的总修为（用于展示「距离飞升」）。"""
    target = clamp_turns(turns)
    total = 0
    for t in range(1, target):
        total += qi_needed(t)
    return total


# ---------------------------------------------------------------------------
# 乘区 B / C：转生与突破
# ---------------------------------------------------------------------------

# 每次转生（飞升）后，基础全属性 ×1.05
ASCENSION_BONUS = 0.05
# 每次大境界突破，基础全属性 ×1.20
BREAKTHROUGH_BONUS = 0.20


def ascension_mult(ascensions: int) -> float:
    """转生乘区：1.05 的转生次数次方。"""
    try:
        n = max(0, int(ascensions))
    except (TypeError, ValueError):
        return 1.0
    return (1.0 + ASCENSION_BONUS) ** n


def breakthrough_mult(breakthroughs: int) -> float:
    """突破乘区：1.20 的突破次数次方。"""
    try:
        n = max(0, int(breakthroughs))
    except (TypeError, ValueError):
        return 1.0
    return (1.0 + BREAKTHROUGH_BONUS) ** n


def stat_mult(turns: int, ascensions: int = 0, breakthroughs: int = 0) -> float:
    """总乘区 = 境界 × 转生 × 突破。

    **这是属性计算的唯一入口**。战斗、面板、排行榜都从这里取，
    保证「面板值 = 基础属性 × stat_mult + 装备加成」这条规则不被绕过。
    """
    return realm_mult(turns) * ascension_mult(ascensions) * breakthrough_mult(
        breakthroughs
    )


# ---------------------------------------------------------------------------
# 突破与飞升判定
# ---------------------------------------------------------------------------

def can_breakthrough(turns: int, qi: int) -> bool:
    """是否可以突破大境界。

    条件：当前是本境界的第 9 转，且修为够 qi_needed(9转) 那一档。

    注意：这里**统一走 qi_needed**，不再另查 REALMS 的门槛表。
    2026-09-19 之前是两套数字（qi_needed 用 REALMS 门槛、can_breakthrough
    也读 REALMS 门槛），改曲线时极易对不上 —— 现在
    qi_needed 是唯一真相源。
    """
    t = clamp_turns(turns)
    if t >= MAX_TURNS:
        return False
    # 只在「九转」时才能突破到下一境界
    if turn_in_realm(t) != TURNS_PER_REALM:
        return False
    return int(qi) >= qi_needed(t)


def is_max_turn(turns: int) -> bool:
    """是否已达大乘九转圆满（可以渡天劫了）。"""
    return clamp_turns(turns) >= MAX_TURNS


def can_tribulation(turns: int, qi: int) -> bool:
    """是否可以渡天劫（飞升的最后一步）。"""
    return is_max_turn(turns) and int(qi) >= ASCENSION_QI


# ---------------------------------------------------------------------------
# 修为收益缩放
# ---------------------------------------------------------------------------
#
# 产出也做成显式表：跨境 ×2（与旧版一致，方便对照）。
# 用表而不用 50*2^idx，是因为调平衡时直接改数字最快、最不容易算错。
#
# 三条产出线的权重（2026-09-19 定稿）：
#     修炼 = cultivate_gain      （5 分钟冷却，每小时最多 12 次）
#     闭关 = cultivate_gain × 3  （每小时等效）
#     探索 = cultivate_gain × 1  （每小时等效）
# 合计 cultivate_gain × 16 = 玩家每小时的有效修为产出。
# 这个 ×16 是整套平衡的基准，改冷却就要改它。

CULT_BASE: list[int] = [50, 100, 200, 400, 800, 1_600, 3_200, 6_400]

# 每小时有效产出系数 = 三条线之和（见上方注释）
HOURLY_EFFICIENCY_FACTOR = 16

# 闭关相对修炼的倍率
SECLUSION_MULT = 3
# 探索相对修炼的倍率（约为闭关的 1/3）
EXPLORE_MULT = 1


def cultivate_gain(turns: int) -> int:
    """一次 /修炼 能拿多少修为。

    按境界查表：炼气期 50，每上一个境界 ×2。
    """
    return CULT_BASE[realm_index_of(turns)]


def seclusion_qi_per_hour(turns: int) -> int:
    """闭关每小时修为 = 修炼收益 × 3。"""
    return cultivate_gain(turns) * SECLUSION_MULT


def explore_qi_per_hour(turns: int) -> int:
    """探索每小时修为 = 修炼收益 × 1（约为闭关的 1/3）。

    探索的定位不是产修为，而是产物品与秘典残页。
    """
    return cultivate_gain(turns) * EXPLORE_MULT


def hourly_efficiency(turns: int) -> int:
    """该转数下，玩家每小时的有效修为产出（三线合计）。

    用于平衡核算与自测；游戏逻辑不要用它直接发修为。
    """
    return cultivate_gain(turns) * HOURLY_EFFICIENCY_FACTOR


def hours_per_turn(turns: int) -> float:
    """在当前转数下，精进到下一转大约需要多少小时（三线并行的估算）。

    纯展示/平衡用，不参与游戏逻辑。
    """
    need = qi_needed(turns)
    if need <= 0:
        return 0.0
    eff = hourly_efficiency(turns)
    if eff <= 0:
        return 0.0
    return need / eff


def total_hours_to(turns: int) -> float:
    """从 1 转到指定转数的估算总耗时（小时）。"""
    target = clamp_turns(turns)
    return sum(hours_per_turn(t) for t in range(1, target))


def qi_progress(turns: int, qi: int) -> tuple[int, int]:
    """返回 (当前转内已积累修为, 本转需求)，用于面板进度条。"""
    need = qi_needed(turns)
    if need <= 0:
        return int(qi), int(qi)
    return min(int(qi), need), need


# ---------------------------------------------------------------------------
# REALMS 门槛自动派生（必须在文件末尾执行）
# ---------------------------------------------------------------------------


def _compute_realm_thresholds() -> list[int]:
    """按真实曲线累加出每个境界的「起始累计修为门槛」。

    为什么要有这个：
        曲线的唯一真相源是 QI_BASE / TURN_QI_STEP / _BREAKTHROUGH_NEED_RATIO。
        REALMS 里的门槛只是**展示与派生量**。
        手工维护两套数字，改曲线时必然对不上（2026-09-19 就踩过：
        qi_needed(9)=1504 但 can_breakthrough 要求 2000，玩家被卡住）。
        所以这里一律算出来，不手填。
    """
    thresholds = [0]
    cum = 0
    for idx in range(len(REALMS) - 1):
        for n in range(1, TURNS_PER_REALM + 1):
            t = idx * TURNS_PER_REALM + n
            if t >= MAX_TURNS:
                break
            if n == TURNS_PER_REALM:
                cum += int(QI_BASE[min(idx + 1, len(QI_BASE) - 1)] * _BREAKTHROUGH_NEED_RATIO)
            else:
                cum += int(QI_BASE[idx] * (1 + (n - 1) * TURN_QI_STEP))
        thresholds.append(cum)
    return thresholds


def _sync_realm_thresholds() -> None:
    """把派生出来的门槛写回 REALMS（就地替换元组）。"""
    thresholds = _compute_realm_thresholds()
    for i, th in enumerate(thresholds):
        name, start, _old, mult = REALMS[i]
        REALMS[i] = (name, start, th, mult)


_sync_realm_thresholds()


def _sync_realm_index() -> None:
    """REALMS 被改写后重建转数 -> 境界下标的查找表。"""
    global _REALM_INDEX
    _REALM_INDEX = []
    for idx in range(len(REALMS)):
        _REALM_INDEX.extend([idx] * TURNS_PER_REALM)


_sync_realm_index()
