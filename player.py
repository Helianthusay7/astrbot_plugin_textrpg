"""玩家数据模型与数值公式。

设计原则：
- 所有可调数值集中在文件顶部的 CONFIG 区，改平衡只动这里。
- 伤害、经验、掉落等公式用纯函数实现，方便单元测试。
- 玩家对象只负责"数据"和"简单存取"，跨玩家的比较逻辑放在外面。

v3.0 修仙化的**核心约定**（改代码前务必读一遍）：
    面板值 = 基础属性 × 总乘区 + 装备加成

    基础属性（base_atk / base_def / base_hp）只跟「转数」有关，
    是**唯一计算源头**。总乘区由 realm.stat_mult() 统一给出
    （境界倍率 × 转生倍率 × 突破倍率）。

    加任何新系统（丹药 buff / 活动加成 / 秘典被动），
    都往乘区里插一项，**不改战斗代码**。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field, asdict
from typing import Any

from . import realm as realm_mod

# items 只在 from_dict 的迁移逻辑里用到。用函数内延迟导入（见下面的
# _resolve_item_alias），避免 player <-> items 将来出现循环依赖时炸掉。
_ITEM_ALIAS_RESOLVER = None


def _resolve_item_alias(name: str) -> str:
    """把 v2 的旧物品名映射成 v3 的新名。未知名字原样返回。"""
    global _ITEM_ALIAS_RESOLVER
    if _ITEM_ALIAS_RESOLVER is None:
        from . import items as _items
        _ITEM_ALIAS_RESOLVER = _items.resolve_alias
    return _ITEM_ALIAS_RESOLVER(name)


_QUALITY_RANKER = None


def _quality_rank(quality: str) -> float:
    """品质 key -> 倍率（`common` -> 1.0，`legend` -> 3.0）。

    用于「同名不同品质时挑哪件」的排序。懒加载 items 避免循环依赖；
    取不到时返回 1.0（等价于「不区分品质」），保证不会崩。
    """
    global _QUALITY_RANKER
    if _QUALITY_RANKER is None:
        try:
            from . import items as _items
            _QUALITY_RANKER = _items.quality_mult
        except ImportError:  # pragma: no cover - 独立运行兜底
            return 1.0
    try:
        return float(_QUALITY_RANKER(quality))
    except Exception:  # pragma: no cover - 未知品质兜底
        return 1.0


_QUALITY_SPLITTER = None


def _split_quality_name(item_name: str) -> tuple[str, str]:
    """拆出「基础名 + 品质后缀」，如「寒铁剑[稀]」-> ("寒铁剑", "稀")。

    同样用懒加载规避 player <-> items 的循环依赖。
    拆不开时返回 (原名, "")。
    """
    global _QUALITY_SPLITTER
    if _QUALITY_SPLITTER is None:
        from . import items as _items
        _QUALITY_SPLITTER = _items.split_quality
    return _QUALITY_SPLITTER(item_name)


_DECORATOR = None


def _rebuild_item_name(base: str, quality: str) -> str:
    """用「基础名 + 品质 key」重新拼出完整物品名。

    【为什么需要这个函数 —— 这里踩过一个很贵的坑】

    `split_quality("寒铁剑[稀]")` 返回的是 ("寒铁剑", "rare") ——
    注意第二个值是**内部 key**（`rare`），不是显示用的中文标记（`稀`）。

    我最初在迁移代码里直接拿这个 key 拼名字：
        new_name = base + f"[{qual}]"
    结果老存档迁移后变成了 `寒铁剑[rare]` 这种名字。
    而 `split_quality` 是按中文标记反查的，认不出 `rare`
    -> 迁移完的物品**立刻变成未知道具**，比不迁移更糟。

    更麻烦的是迁移不幂等：
        "小回气丹" -> "小回气丹[common]" -> 再迁一次 ->
        "小回气丹[common][common]"（因为 "[common]" 没被识别为品质后缀）

    修法就是统一走 items.decorate() —— 它内部会做 key -> 中文标记的转换。
    凡是「拼完整物品名」的地方都必须用它，不要手写 `f"[{...}]"`。
    """
    if not quality:
        return base
    global _DECORATOR
    if _DECORATOR is None:
        from . import items as _items
        _DECORATOR = _items.decorate
    return _DECORATOR(base, quality)

# ---------------------------------------------------------------------------
# 可调数值区 —— 改平衡改这里就够了
# ---------------------------------------------------------------------------

# 初始属性（炼气期 1 转）
INIT_LEVEL = 1
INIT_HP = 120
INIT_GOLD = 50
INIT_ATK = 18
INIT_DEF = 5
#
# INIT_ATK 为什么从 10 提到 18（2026-09-19 数值平衡）：
#   旧值 10 < 玩家自身防御 5 的"输出效率"，
#   1 转打最弱的史莱姆要 9 回合 —— 新手第一场战斗就是拖沓的。
#   改 18 后，配合妖兽按玩家面板反推（见 items.py MONSTERS 注释），
#   炼气期变成「5 回合击杀、可扛 20 回合」，与其他七个境界手感统一。
# INIT_HP 100->120 是给新手的容错余量。
#
# ⚠️ 改动这两个常量会**连带影响秘境**：dungeon.anchor_stats() 锚定在
#    base_atk(1) / base_hp(1) 上，改初始值会等比拉高秘境怪的三围。
#    2026-09-19 实测：INIT_ATK 10->18 后秘境极限层整体下移约 2~3 层
#    （72 转从 46 层降到 44 层），属可接受范围。

# 属性成长公式的系数（按转数成长，不含任何乘区）
ATK_GROWTH = 10  # 每转增加攻击力
DEF_GROWTH = 6   # 每转增加防御力
HP_GROWTH = 55   # 每转增加气血上限
#
# 系数为什么比 v2 大：v2 是 200 级，v3 只有 72 转。
# 要让满转的裸属性接近 v2 的 200 级水平，每转的增量必须放大。

# 修为（对应 v2 的「经验」）。转数 1~72，即 level 字段复用。
EXP_BASE = 300
EXP_POWER = 1.35
# 注意：实际转数所需修为由 realm.qi_needed() 给出，
# 这里的常量只作为旧接口 exp_needed() 的兜底（老测试用）。

# 离线闭关：每小时获得的修为基准（实际由 realm.seclusion_qi_per_hour 缩放），
# 以及最长结算时长（小时）
TRAIN_EXP_PER_HOUR = 30
TRAIN_MAX_HOURS = 8

# 战斗回合上限，防止双方高防导致死循环
#
# v3.3：10 -> 30（2026-09-19 端到端验收后调整）
#
# 【为什么必须改】
# v3.2 的妖兽属性是按「普通怪 5 回合击杀、玩家可扛 19 回合」反推的，
# 但回合上限只有 10 —— 设计目标与实现直接冲突：
#   * 妖王 hp ×3.2，裸装要磨 15 回合才打得完；
#     上限 10 一到就判 draw，玩家还剩 47~74% 气血。
#     这不是「打不过」，是「打不完」，但结算上看起来一模一样。
#   * 「可扛 19 回合」这个指标在上限 10 下永远无法被观测/达成。
#
# 提到 30 之后：
#   普通档 5~7 回合（不变）／精英 8~9 回合／妖王 15 回合（干净的磨血战）。
#   「高消耗战」的手感才真正落地 —— 难度来自「血厚要磨」，
#   而不是来自「打不完被判平」。
#
# 【为什么是 30 而不是无限】
# 30 ≈ 妖王 15 回合的 2 倍余量，给秘典/装备成长留足空间；
# 同时仍然兜住「双方高防互相打不动」的死循环。
MAX_BATTLE_TURNS = 30

# 伤害浮动区间（乘数）
DAMAGE_RANGE = (0.9, 1.1)
# 伤害下限：再怎么低也至少打出这个值，避免战斗卡死
MIN_DAMAGE = 1

# 逃跑成功率（v2 起战斗全自动，此常量仅保留兼容）
FLEE_SUCCESS_RATE = 0.6

# 背包容量上限（默认值；实际值可由插件配置 max_inventory 覆盖，
# 覆盖通过 Player.add_item 的 limit 参数传入）
MAX_INVENTORY = 50

# ---------------------------------------------------------------------------
# 冷却与休整（v2 引入，v3 调整语义）
# ---------------------------------------------------------------------------

# 历练（原打怪）冷却（秒）
HUNT_COOLDOWN = 300.0

# 修炼冷却（秒）。v3 修正语义：修炼 = 主动一次性加修为，冷却 5 分钟。
CULTIVATE_COOLDOWN = 300.0

# 闭关冷却（秒）。
#
# v3.6 改为 0：闭关与探索统一改成「状态型挂机」——
#   /闭关 进入状态 → /结束闭关 结算并退出 → 可以立刻再闭关。
# 防刷靠的是时长下限（闭关 6 分钟 / 探索 30 分钟），不是冷却。
# 配置项（train_cooldown / explore_cooldown）保留，想恢复冷却改配置即可。
#
# （v2 里这个常量叫 TRAIN_COOLDOWN，语义是「挂机结算」，v3 正名为闭关）
SECLUSION_COOLDOWN = 0.0

# 探索冷却（秒）。同上，v3.6 改为 0。
EXPLORE_COOLDOWN = 0.0

# 探索时长上下限（秒）
EXPLORE_MIN_SECONDS = 1800.0
EXPLORE_MAX_SECONDS = 28800.0

# 死亡后原地休整时长（秒）。取消死亡惩罚，用时间成本替代资源惩罚。
DEATH_REST_SECONDS = 600.0

# 花钱立刻恢复的价格
DEATH_REVIVE_GOLD = 30

# 战斗结果分级：碾压 / 惨胜的血线
CRUSH_HP_RATIO = 0.9     # HP >= 90% 判碾压
NARROW_HP_RATIO = 0.3    # HP < 30% 判惨胜

# 结果对应的修为倍率
OUTCOME_EXP_MULT = {
    "crush": 1.2,
    "win": 1.0,
    "narrow": 1.0,
    "draw": 0.3,
    "lose": 0.0,
}

# 惨胜的额外掉落加成（加在掉落概率上）
NARROW_DROP_BONUS = 0.10

# ---------------------------------------------------------------------------
# 走火入魔（闭关专属风险）
# ---------------------------------------------------------------------------
# (起始小时, 概率)。按闭关时长落档，取满足条件的最高档。
QI_DEVIATION_TABLE = (
    (2.0, 0.0),
    (4.0, 0.05),
    (8.0, 0.12),
    (float("inf"), 0.20),
)
# 入魔后收益打折系数（不扣任何资源，只是收益减半）
QI_DEVIATION_PENALTY = 0.5

# ---------------------------------------------------------------------------
# 天劫（抗住 9 道雷）
# ---------------------------------------------------------------------------
# 数值由来（2026-09-19 修正）：
#   9 道雷累计伤害 = 最大气血 × 99.9%（复利 8%），即
#   **满血刚好能扛住 9 道**，剩 0.1% 血皮飞升。
#   气血 85% -> 抗 8 道；70% -> 7 道；50% -> 5 道。
#   这样「准备充分才过得去」成立，而不是无论怎么准备都必死。
#   最初写的 12% 递增会让累计伤害达到 118%，满血也必死 —— 那是设计 bug。
TRIBULATION_BOLTS = 9            # 一共几道雷
TRIBULATION_BASE_RATIO = 0.08    # 第 1 道雷 = 最大气血的 8%
TRIBULATION_GROWTH = 0.08        # 每道递增 8%（累计约 100% 满血）
# 抗住几道以上算通过
TRIBULATION_PASS_BOLTS = 9
# 失败惩罚：掉几转 + 休整多少秒
TRIBULATION_FAIL_HIGH = (5, 600.0)    # 抗住 5~8 道
TRIBULATION_FAIL_LOW = (3, 1800.0)    # 抗住 <5 道


# ---------------------------------------------------------------------------
# 公式
# ---------------------------------------------------------------------------

def exp_needed(level: int) -> int:
    """升到下一转所需的修为。

    优先走 realm.qi_needed()（v3 的真实曲线）；
    转数超出 1~72 范围时退回指数公式，兼容老测试的调用方式。
    """
    try:
        t = int(level)
    except (TypeError, ValueError):
        return int(EXP_BASE)
    need = realm_mod.qi_needed(t)
    if need > 0:
        return need
    # 已满转（或超出），退回兜底公式，保证老断言 exp_needed(1) 有值
    return int(EXP_BASE * (max(1, t) ** EXP_POWER))


def base_atk(turns: int) -> int:
    """基础攻击力（不含任何乘区与装备）。"""
    return INIT_ATK + (max(1, int(turns)) - 1) * ATK_GROWTH


def base_def(turns: int) -> int:
    """基础防御力（不含任何乘区与装备）。"""
    return INIT_DEF + (max(1, int(turns)) - 1) * DEF_GROWTH


def base_hp(turns: int) -> int:
    """基础气血上限（不含任何乘区与装备）。"""
    return INIT_HP + (max(1, int(turns)) - 1) * HP_GROWTH


# ---- 兼容层：v2 的 calc_* 接口保留，语义 = 基础属性（不带乘区） ----

def calc_atk(level: int) -> int:
    """基础攻击力。v3 语义：等价于 base_atk。"""
    return base_atk(level)


def calc_def(level: int) -> int:
    """基础防御力。v3 语义：等价于 base_def。"""
    return base_def(level)


def calc_max_hp(level: int) -> int:
    """基础气血上限。v3 语义：等价于 base_hp。"""
    return base_hp(level)


def roll_damage(atk: int, target_def: int) -> int:
    """计算一次伤害，带随机浮动与下限保护。"""
    base = atk * random.uniform(*DAMAGE_RANGE)
    return max(MIN_DAMAGE, int(base - target_def * 0.5))


def roll_damage_tuned(
    atk: int,
    target_def: int,
    penetration: float = 0.0,
    damage_bonus: float = 0.0,
) -> int:
    """带穿甲与增伤的伤害计算（v3.1）。

    与 roll_damage 的区别只有两点，且**只该用在玩家打怪物这一侧**：

      penetration  穿甲比例。怪物防御按 (1 - penetration) 生效。
                   传 0.03 = 怪防只算 97%。
      damage_bonus 增伤比例。最终伤害 ×(1 + damage_bonus)。
                   5 件套生效时传 0.05。

    为什么不改 roll_damage 本身：怪物反击玩家不该吃玩家的穿甲/增伤，
    保持两个入口，语义清晰、也不会误伤。
    """
    eff_def = target_def * (1.0 - max(0.0, min(1.0, penetration)))
    base = atk * random.uniform(*DAMAGE_RANGE)
    raw = max(MIN_DAMAGE, int(base - eff_def * 0.5))
    if damage_bonus > 0:
        raw = int(raw * (1.0 + damage_bonus))
    return max(MIN_DAMAGE, raw)


def deviation_chance(hours: float) -> float:
    """按闭关时长取走火入魔概率。"""
    try:
        h = float(hours)
    except (TypeError, ValueError):
        return 0.0
    for threshold, chance in QI_DEVIATION_TABLE:
        if h < threshold:
            return chance
    return QI_DEVIATION_TABLE[-1][1]


def tribulation_bolts(hp: int, max_hp: int) -> tuple[int, list[int]]:
    """模拟天劫：返回 (抗住几道, 每道伤害列表)。

    这是纯数值模拟，不消耗玩家状态 —— 调用方决定成功后做什么。

    规则：
        第 1 道雷伤害 = 最大气血 × TRIBULATION_BASE_RATIO
        每道递增 TRIBULATION_GROWTH（复利）
        气血 >= 0 才能继续抗，抗不住就停下
    """
    mhp = max(1, int(max_hp))
    cur = max(0, int(hp))
    dmg = mhp * TRIBULATION_BASE_RATIO
    damages: list[int] = []
    survived = 0

    for _ in range(TRIBULATION_BOLTS):
        hit = max(1, int(dmg))
        damages.append(hit)
        if cur - hit < 0:
            cur = 0
            break
        cur -= hit
        survived += 1
        dmg *= 1.0 + TRIBULATION_GROWTH

    return survived, damages


# ---------------------------------------------------------------------------
# 物品相关
# ---------------------------------------------------------------------------

# 部位名 -> 中文标签
#
# v3.1：从 3 个部位扩到 5 个（灵靴 / 法印），凑满 5 件触发「五行俱全」套装。
# 顺序即面板展示顺序，改动这里会同时影响面板和 /卸下 的提示。
SLOTS: tuple[str, ...] = ("weapon", "armor", "accessory", "boots", "talisman")

SLOT_LABELS = {
    "weapon": "法宝",
    "armor": "护身宝衣",
    "accessory": "灵饰",
    "boots": "灵靴",
    "talisman": "法印",
}

# 顺便保留英文 key 的中文名映射（老存档、老指令用 "武器" 也能对上）
SLOT_ALIASES = {
    "武器": "weapon",
    "护甲": "armor",
    "饰品": "accessory",
    "法宝": "weapon",
    "护身宝衣": "armor",
    "灵饰": "accessory",
    "鞋": "boots",
    "灵靴": "boots",
    "靴子": "boots",
    "法印": "talisman",
    "符": "talisman",
}


# ---------------------------------------------------------------------------
# 装备套装与穿甲
# ---------------------------------------------------------------------------
#
# v3.1 初版（老板需求）：
#   ①「装备连成 5 件套时 +5% 增伤」
#   ②「每件装备给玩家 1% 穿甲，穿甲在面板显示」
#
# v3.4 重做（十品体系，2026-09-19）：
#   ✗ 旧规则：「任意 5 件就 +5% 增伤 + 最多 5% 穿甲」
#   ✓ 新规则：**同一套系穿满 5 件**才触发，且收益按套系分流：
#         物穿套 -> 套装穿甲（10%~45%）+ 概率无视防御 + 暴击
#         法穿套 -> 套装穿甲（更高，×1.15）+ 术法增伤 + 冷却缩减
#         肉盾套 -> 受伤减免 + 反伤 + 低血额外减免
#
# 【为什么必须放穿甲上限】
# 老板定的「五件一阶物穿套 +10 穿甲」在旧上限 5% 下装不下。
# 放宽后穿甲成了套装的核心卖点，也符合「物穿套」这个定位。
# 但**不能到 100%** —— 满 100% 时怪防归零，数值会坍塌。
# 九品 45%（法穿套 52%）是设计上限，留给怪防一线生路。
#
# 【为什么保留 PENETRATION_PER_ITEM / CAP】
# 它们服务于「没穿满套装」的过渡场景（比如只凑了 3 件）。
# 没穿满时退回旧规则给 1%/件，让玩家散装也有一点点收益，
# 不至于"没穿齐就等于没穿装备"。
SET_BONUS_SLOTS = 5          # 触发套装需要的部位数
SET_DAMAGE_BONUS = 0.05      # 旧的通用增伤（v3.4 起仅供兼容，不再使用）
PENETRATION_PER_ITEM = 0.01  # 未穿满套装时：每件 +1% 穿甲
PENETRATION_CAP = 0.05       # 未穿满套装时的穿甲上限

# ---------------------------------------------------------------------------
# 特殊装备槽（v3.4 新增）
# ---------------------------------------------------------------------------
#
# 老板需求（2026-09-19）：
#   「帝兵不占普通装备槽，放置在【特殊】栏，无帝兵时不显示【特殊】栏」
#
# 它与 5 个普通部位**完全解耦**：
#   - 不参与套系判定（穿帝兵不等于凑了一套）
#   - 加成**叠乘**在普通装备之上（额外一层乘区）
#   - 存档单独一个字段（self.special），存的是特殊装备的 **id**（不是名字）
#
# 为什么存 id 不存名字：名字可改（帝兵可能改叫别的），id 稳定。
SPECIAL_SLOT = "special"
SPECIAL_SLOT_LABEL = "特殊"

# 成长型被动的默认参数（可被特殊装备记录里的同名字段覆盖）
#
# 老板说"具体问题具体分析，天机不可泄露" —— 所以这里只放**兜底默认值**，
# 真正的参数由 SPECIAL_EQUIPMENTS 里每条记录自己的 passives 决定。
# 将来调曲线（linear / diminish / log）只改数据，不动逻辑。
SPECIAL_GROWTH_DEFAULT = {
    "pct": 1.0,           # 每击杀 +1% 全属性
    "cap_pct": 150.0,     # 封顶 +150%
    "curve": "linear",    # 曲线：linear（线性）/ diminish（递减）/ log（对数）
    "lose_on_death": False,   # 死亡不掉层（老板定的）
}


def roll_drop(drop_table: list[dict[str, Any]]) -> str | None:
    """按掉落表随机抽一件物品，返回 None 表示没掉落。

    返回的物品名带品质后缀（如「铁剑[稀]」）。
    消耗品不带品质（统一普通），装备才 roll 品质。
    这样背包里可以同时存在不同品质的同名装备。
    """
    from .items import decorate, is_equipment, roll_quality

    for entry in drop_table:
        if random.random() < entry["rate"]:
            base = entry["item"]
            if is_equipment(base):
                return decorate(base, roll_quality())
            return base
    return None


# ---------------------------------------------------------------------------
# 玩家对象
# ---------------------------------------------------------------------------

@dataclass
class Player:
    """单个玩家在单个群里的存档。

    关于 group_id：
        同一个人可能同时在多个群玩，各群的进度应该独立 ——
        在 A 群是大佬，在 B 群不该直接满级带人。
        因此存档键是 (group_id, qq) 组合，group_id 为空字符串
        表示私聊场景。

        旧存档没有 group_id 字段，from_dict 默认为空字符串，
        等价于"私聊存档池"，不会崩溃。
    """

    qq: str
    group_id: str = ""
    name: str = ""
    # level 在 v3 语义 = 总转数（1~72）。字段名保留是为了老存档兼容。
    level: int = INIT_LEVEL
    exp: int = 0
    hp: int = INIT_HP
    gold: int = INIT_GOLD

    # 背包：{物品名: 数量}
    inventory: dict[str, int] = field(default_factory=dict)
    # 已装备：{部位: 物品名}
    equipment: dict[str, str] = field(default_factory=dict)

    # ---------------- 特殊装备（v3.4 新增） ----------------
    # 存的是特殊装备的 **id**（不是名字 —— 名字可改，id 稳定）。
    # 空串 = 没有特殊装备。**不占 equipment 的 5 个部位**，
    # 加成叠乘在普通装备之上（见 special_pct）。
    special: str = ""
    # 成长型被动的累积加成（%）。帝兵每击杀一只妖兽 +0.1%，这里累积。
    # 老板定的「具体问题具体分析」—— 所以累积值存存档，
    # 但「怎么涨、涨到哪、死不死」全在 SPECIAL_EQUIPMENTS 的 passives 里配。
    special_growth_pct: float = 0.0
    # 【v3.5】曾经兑换过的特殊装备 id 列表（**只增不减**）。
    #
    # 为什么不能只靠 special 字段判断「是否已拥有」：
    # `/特殊 卸下` 会把 special 清空，而帝兵是花 100 枚天道令换来的。
    # 若兑换时只看 special，玩家「卸下 -> 再兑换」就会被**白扣 100 枚**。
    # 所以这里留一份永久流水，专供兑换去重（见 special.owns_special）。
    special_owned: list[str] = field(default_factory=list)

    # 统计
    total_kills: int = 0
    total_deaths: int = 0

    # 上次结算闭关的时间戳（秒）；0 表示从未结算
    last_train_ts: float = 0.0

    # v3.6：本次闭关的开始时间戳（秒）；0 表示当前不在闭关。
    #
    # 【为什么必须和 last_train_ts 拆成两个字段】
    # 旧实现只有一个 last_train_ts，同时兼任三件事：
    #   ① 闭关开始计时 ② 结算的时间基线 ③ 冷却起点。
    # 于是：
    #   - 刚进入闭关时 last_train_ts = now，冷却检查把它当成「刚出关」，
    #     1 小时内再发 /闭关 会被拒绝 —— 想结算也结算不了；
    #   - 结算过一次之后 last_train_ts 恒为正，
    #     `if last_train_ts <= 0` 的「进入闭关」分支永远进不去，
    #     闭关退化成「每小时领一次收益」的按钮，不再是状态。
    # 对照探索：它一开始就是 explore_start_ts（状态）+ last_explore_ts（冷却）
    # 两个字段，所以从来没有这个问题。状态和冷却必须是两个字段。
    train_start_ts: float = 0.0

    # ---------------- v2.0 新增字段 ----------------
    # 全部带默认值，老存档 from_dict 后自动补上，不会崩。

    # 角色形象图相对路径（相对 data_dir），空字符串表示用默认形象
    avatar: str = ""
    # 建号时间戳，0 表示老存档（没有记录）
    created_at: float = 0.0
    # 上次历练时间戳（冷却存在存档里，重启不再清零）
    last_hunt_ts: float = 0.0
    # 休整结束时间戳；0 或小于当前时间表示不在休整中
    rest_until: float = 0.0

    # ---------------- v3.0 修仙化新增字段 ----------------

    # 已习得的秘典：{"火之秘典": 2} 表示火之秘典已修到二阶
    scriptures: dict[str, int] = field(default_factory=dict)
    # 本次装配的秘典名列表（战斗生效），上限由境界决定
    equipped_scriptures: list[str] = field(default_factory=list)
    # 转生次数（每飞升一次 +1，基础属性 ×1.05^n）
    ascensions: int = 0
    # 大境界突破次数（基础属性 ×1.20^n）
    breakthroughs: int = 0
    # 上次修炼时间戳（主动修炼，冷却 5 分钟）
    last_cultivate_ts: float = 0.0
    # 上次探索时间戳 + 探索开始时间（探索是挂机，需要记录起点）
    last_explore_ts: float = 0.0
    explore_start_ts: float = 0.0

    # ---------------- v3.1 秘境（副本）新增字段 ----------------

    # 秘境历史最高通关层数，0 表示还没闯过
    dungeon_best: int = 0
    # 上次闯秘境的本地日期字符串（YYYY-MM-DD），用于"每日一次"判定
    # 用日期字符串而不是时间戳：玩家要的是"每天一次"，
    # 而不是"距上次满 24 小时"，跨零点就该重置。
    dungeon_date: str = ""

    # ---------------- v3.5 天道令（签到 / 兑换）新增字段 ----------------

    # 上次签到的本地日期字符串（YYYY-MM-DD），空串表示从未签到。
    # 和 dungeon_date 同一个思路：跨零点重置，不是「距上次满 24 小时」。
    # 23:59 签完，00:01 就该能再签一次 —— 用时间戳会变成玩家觉得被坑。
    sign_in_date: str = ""

    @property
    def key(self) -> str:
        """存档键：群号:QQ号。"""
        return f"{self.group_id}:{self.qq}"

    # ---------------- v3 境界视图 ----------------

    @property
    def turns(self) -> int:
        """总转数（1~72）。它就是 level，只是换了个名字。"""
        return realm_mod.clamp_turns(self.level)

    @property
    def realm_name(self) -> str:
        return realm_mod.realm_name_of(self.turns)

    @property
    def realm_label(self) -> str:
        """如「金丹期 3 转」。"""
        return realm_mod.realm_label(self.turns)

    @property
    def realm_mult(self) -> float:
        return realm_mod.realm_mult(self.turns)

    def stat_mult(self) -> float:
        """总乘区 = 境界 × 转生 × 突破。

        面板与实际战斗**只能**从这里取乘区，
        这是「基础属性 × 乘区 + 装备」规则的唯一入口。
        """
        return realm_mod.stat_mult(self.turns, self.ascensions, self.breakthroughs)

    @property
    def equip_slots(self) -> int:
        """可装配的秘典数量。"""
        from .scriptures import equip_slots

        return equip_slots(self.breakthroughs)

    # ---------------- 冷却与休整 ----------------

    def cooldown_remain(
        self, field: str, seconds: float, now: float | None = None
    ) -> float:
        """通用冷却剩余秒数。0 表示可用。

        field 取 "last_hunt_ts" / "last_cultivate_ts" /
        "last_train_ts"（闭关）/ "last_explore_ts"。
        """
        now = now or time.time()
        last = float(getattr(self, field, 0.0) or 0.0)
        if last <= 0:
            return 0.0
        return max(0.0, seconds - (now - last))

    def hunt_remain(self, seconds: float = HUNT_COOLDOWN, now: float | None = None) -> float:
        return self.cooldown_remain("last_hunt_ts", seconds, now)

    def cultivate_remain(
        self, seconds: float = CULTIVATE_COOLDOWN, now: float | None = None
    ) -> float:
        """修炼（主动一次性）的冷却剩余。"""
        return self.cooldown_remain("last_cultivate_ts", seconds, now)

    def seclusion_remain(
        self, seconds: float = SECLUSION_COOLDOWN, now: float | None = None
    ) -> float:
        """闭关（挂机结算）的冷却剩余。"""
        return self.cooldown_remain("last_train_ts", seconds, now)

    def explore_remain(
        self, seconds: float = EXPLORE_COOLDOWN, now: float | None = None
    ) -> float:
        """探索的冷却剩余。"""
        return self.cooldown_remain("last_explore_ts", seconds, now)

    # 兼容 v2 命名：老测试用 train_remain
    def train_remain(self, seconds: float = SECLUSION_COOLDOWN, now: float | None = None) -> float:
        return self.seclusion_remain(seconds, now)

    def is_resting(self, now: float | None = None) -> bool:
        """是否处于死亡后的休整期。"""
        now = now or time.time()
        return float(self.rest_until or 0.0) > now

    def rest_remain(self, now: float | None = None) -> float:
        """休整剩余秒数。0 表示不在休整。"""
        now = now or time.time()
        return max(0.0, float(self.rest_until or 0.0) - now)

    def is_exploring(self, now: float | None = None) -> bool:
        """是否正在探索挂机中。"""
        return float(self.explore_start_ts or 0.0) > 0

    def explore_elapsed(self, now: float | None = None) -> float:
        """本次探索已经过了多少秒。未在探索返回 0。"""
        start = float(self.explore_start_ts or 0.0)
        if start <= 0:
            return 0.0
        now = now or time.time()
        return max(0.0, now - start)

    def is_in_seclusion(self) -> bool:
        """是否正在闭关挂机中（v3.6）。"""
        return float(self.train_start_ts or 0.0) > 0

    def seclusion_elapsed(self, now: float | None = None) -> float:
        """本次闭关已经过了多少秒。未在闭关返回 0。"""
        start = float(self.train_start_ts or 0.0)
        if start <= 0:
            return 0.0
        now = now or time.time()
        return max(0.0, now - start)

    def is_idle(self) -> bool:
        """是否正在挂机（闭关或游历）。

        v3.6：两种挂机状态**互斥**，且挂机期间不能做别的玩法
        （修炼 / 历练 / 寻敌 / 秘境）。
        """
        return self.is_in_seclusion() or self.is_exploring()

    def idle_name(self) -> str:
        """当前挂机状态的中文名；没挂机返回空串。"""
        if self.is_in_seclusion():
            return "闭关"
        if self.is_exploring():
            return "游历"
        return ""

    def start_rest(
        self,
        seconds: float = DEATH_REST_SECONDS,
        now: float | None = None,
        item_db: dict[str, dict] | None = None,
    ) -> None:
        """进入休整，并立刻回满血。

        回血在这里做（而不是等休整结束）是为了简化逻辑：
        休整期间反正不能打怪，血满不满无所谓；
        等玩家休整结束发 /历练 时，血已经是满的了。
        """
        now = now or time.time()
        self.rest_until = now + max(0.0, seconds)
        self.full_heal(item_db)

    def revive_now(self, item_db: dict[str, dict] | None = None) -> None:
        """立刻恢复（花钱或用道具），清掉休整状态并回满血。"""
        self.rest_until = 0.0
        self.full_heal(item_db)

    def full_heal(self, item_db: dict[str, dict] | None = None) -> None:
        """回满气血（含装备加成）。"""
        if item_db is not None:
            self.hp = self.max_hp(item_db)
        else:
            self.hp = int(base_hp(self.turns) * self.stat_mult())

    # ---------------- 属性计算（唯一的属性出口） ----------------

    def _lookup_equip(self, item_name: str, item_db: dict[str, dict]) -> dict | None:
        """查一件已装备法宝的定义，**数值口径与 items.get_item 完全一致**。

        【为什么不能直接 item_db.get(item_name) —— 这里崩过一次】

        item_db 的 key 是**基础装备名**（`寒铁剑`），而玩家身上穿的、
        储物袋里存的名字都是**带品质后缀的完整名**（`寒铁剑[普]`）。
        直接 get 的后果：

            item_db.get("寒铁剑[普]")  ->  None  ->  加成静默归零

        而且就算查到了基础名，拿到的也是**未乘品质倍率**的裸值，
        与掉落/面板/坊市走 get_item 得到的已乘倍率值对不上 ——
        同一件装备，展示一个数、生效另一个数。

        正确做法是把「解析名字 + 乘品质倍率」这件事**只交给
        items.get_item 一个地方做**（它内部会 split_quality ->
        resolve_alias -> 查表 -> 乘 quality_mult）。这里只是调用它。

        兜底：items 导入失败时（独立运行脚本等）退回裸查 item_db，
        保证不会因为环境问题直接抛异常。
        """
        try:
            from . import items as _items
        except ImportError:  # pragma: no cover - 独立运行兜底
            _items = None

        if _items is not None:
            item = _items.get_item(item_name)
            if item is not None and item.get("kind") == "equipment":
                return item
            # get_item 里的装备表来自模块级 EQUIPMENT_BY_NAME，
            # 与调用方传进来的 item_db 可能是不同快照（测试会传临时表）。
            # 这里再按「裸名 + 品质倍率」从传入的 item_db 复核一次。
            base, quality = _items.split_quality(item_name)
            base = _items.resolve_alias(base)
            raw = item_db.get(base)
            if raw is None:
                return None
            item = dict(raw)
            mult = _items.quality_mult(quality)
            for key in ("atk_pct", "def_pct", "hp_pct"):
                if key in item:
                    item[key] = round(item[key] * mult, 4)
            return item

        # 极少数情况下 items 不可用，退回裸查（可能拿到 None）
        return item_db.get(item_name)

    def equip_bonus(self, key: str, item_db: dict[str, dict]) -> int:
        """累加已装备法宝在某个属性上的加成（绝对值）。

        ⚠️ v3.4 起这个方法**只服务于旧接口 / 秘典等仍用绝对值的场景**。
        玩家三围面板已经改走 `equip_pct`（百分比），见 atk/defense/max_hp。
        """
        total = 0
        for item_name in self.equipment.values():
            item = self._lookup_equip(item_name, item_db)
            if item:
                total += int(item.get(key, 0))
        return total

    # ---------------- 装备百分比加成（v3.4 核心变更） ----------------

    def equip_pct(self, key: str, item_db: dict[str, dict]) -> float:
        """累加已装备法宝的百分比加成，返回**小数**（0.06 表示 +6%）。

        key 取 "atk_pct" / "def_pct" / "hp_pct"。

        【为什么从绝对值改成百分比】
        旧公式 `面板 = 基础 × 乘区 + 装备绝对值` 有个结构性缺陷：
        乘区跨境翻倍（×2），装备却是固定值 —— 结果顶级 5 件装备
        在炼气期给 +722%，到大乘期只剩 +5%，**越后期越没用**。

        新公式 `面板 = 基础 × 乘区 × (1 + 装备百分比)` 让装备跟着面板涨，
        任何境界都保持同一份「相对增益」—— 这才是"装备"该有的样子。

        【数值口径】
        表里 atk_pct 存的是**百分数**（1.6 = +1.6%），且**不含品质倍率**；
        品质倍率由 _lookup_equip -> items.get_item 统一施加。
        所以同款装备「普/精/稀/史/传」拿到的 pct 依次是 1.0/1.3/1.7/2.2/3.0 倍。
        """
        total = 0.0
        for item_name in self.equipment.values():
            item = self._lookup_equip(item_name, item_db)
            if item:
                total += float(item.get(key, 0.0) or 0.0)
        return total / 100.0    # 表里存的是「百分数」，这里换算成小数

    def special_pct(self) -> float:
        """特殊装备（【特殊】栏）提供的全属性百分比加成，返回小数。

        帝兵这类特殊装备不占 5 个普通部位，单独放在 special 栏，
        加成**叠乘**在普通装备之上 —— 所以它是"额外一层乘区"。
        """
        sid = getattr(self, "special", "") or ""
        if not sid:
            return 0.0
        try:
            from . import items as _items
        except ImportError:  # pragma: no cover - 独立运行兜底
            return 0.0
        sp = _items.get_special(sid)
        if not sp:
            return 0.0
        base = float(sp.get("base_pct", 0.0) or 0.0)
        # 成长被动的累积加成（存在存档里，见 special_kill_stack）
        grown = float(getattr(self, "special_growth_pct", 0.0) or 0.0)
        return (base + grown) / 100.0

    # ---------------- 套装与穿甲（v3.1，v3.4 扩展） ----------------

    def equipped_count(self) -> int:
        """已装备的部位数（去重后，用于套装判定）。

        只统计 SLOTS 里认得的部位，且每部位最多算 1 件 ——
        这样"塞两个同部位"也无法骗过套装判定。

        ⚠️ v3.4 起：「凑够 5 件」不再直接触发套装 ——
        要**同一套系穿满 5 件**才生效（见 items.active_set_id）。
        这个方法保留，因为「穿了几件」仍有用（穿甲按件给）。
        """
        return sum(1 for s in SLOTS if self.equipment.get(s))

    def has_full_set(self) -> bool:
        """5 个部位是否穿满（v3.1 的「五行俱全」判定）。

        v3.4：改为「任一完整套系穿满」，不再只是「凑够 5 件」。
        """
        try:
            from . import items as _items
            return bool(_items.active_set_id(self.equipment))
        except ImportError:  # pragma: no cover
            return self.equipped_count() >= SET_BONUS_SLOTS

    def active_set(self) -> str:
        """当前生效的套系 id（物穿 / 法穿 / 肉盾），没有则空串。"""
        try:
            from . import items as _items
            return _items.active_set_id(self.equipment)
        except ImportError:  # pragma: no cover
            return ""

    def set_mech(self, key: str) -> float:
        """取当前生效套装的某个机制数值（没穿满返回 0.0）。"""
        try:
            from . import items as _items
            return _items.set_mech_value(self.equipment, key)
        except ImportError:  # pragma: no cover
            return 0.0

    def penetration(self) -> float:
        """穿甲比例。

        v3.4 变更：从「每件 +1%、上限 5%」改为**按套系给**：
          * 穿满某套 -> 取该品阶的套装穿甲值（一品 10% ~ 九品 45%）
          * 没穿满   -> 退回旧规则（每件 +1%，上限 5%）

        为什么必须改：老板定的「五件一阶物穿套 +10 穿甲」在旧上限 5% 下
        根本装不下。改成按套给之后，穿甲成了**套装的核心卖点**，
        也符合「物穿套」这个定位。
        """
        sid = self.active_set()
        if sid:
            try:
                from . import items as _items
            except ImportError:  # pragma: no cover
                return 0.0
            if sid == "肉盾":
                # 肉盾套不给穿甲（它的定位是减伤 + 反伤）
                return 0.0
            # 法穿套的穿甲比物穿套略高（魔法本来就偏穿透）
            key = "pen_pct" if False else None
            # 直接从该套装的部件里取「套装穿甲」——
            # 生成器把穿甲值挂在每个部件上，值来自品阶表
            pen_pct = self._set_tier_pen(sid, _items)
            return pen_pct
        n = self.equipped_count()
        return min(PENETRATION_CAP, n * PENETRATION_PER_ITEM)

    def _set_tier_pen(self, sid: str, items_mod) -> float:
        """查该套系当前品阶对应的套装穿甲（返回小数）。

        穿甲值按品阶查定稿表（一品 10% ~ 九品 45%，法穿套 ×1.15）。
        这里不依赖装备上挂的字段，而是**按品阶重算** ——
        这样即使将来装备表漏填某件，穿甲也不会莫名变 0。
        """
        rank = 0
        for name in self.equipment.values():
            r = items_mod.tier_rank_of(name)
            if r:
                rank = max(rank, r)
                break
        if rank <= 0:
            return 0.0
        table = items_mod.SET_PENETRATION_BY_RANK
        base = table.get(rank, 0.0)
        if sid == "法穿":
            base *= items_mod.SET_FPEN_MULT
        return base

    def damage_bonus(self) -> float:
        """增伤比例。

        v3.4：从「穿满 5 件 +5%」改为**法穿套专属**（+8% / +20%）。
        物穿套走穿甲 + 无视防御，肉盾套走减伤 + 反伤，
        三套各有自己的收益通道，不再共用同一个 +5%。
        """
        return self.set_mech("magic_dmg_bonus") / 100.0

    def avoid_def_pct(self) -> float:
        """无视防御的概率（%）。物穿套中/上品专属，其余返回 0。"""
        return self.set_mech("ignore_def_pct")

    def crit_bonus_pct(self) -> float:
        """额外暴击率（%）。物穿套上品专属。"""
        return self.set_mech("crit_bonus")

    def reflect_pct(self) -> float:
        """反伤比例（%）。肉盾套中/上品专属，其余返回 0。"""
        return self.set_mech("reflect_pct")

    def damage_reduce_pct(self) -> float:
        """受伤减免（%）。肉盾套专属，其余返回 0。"""
        try:
            from . import items as _items
        except ImportError:  # pragma: no cover
            return 0.0
        sid = self.active_set()
        if sid != "肉盾":
            return 0.0
        for name in self.equipment.values():
            r = _items.tier_rank_of(name)
            if r:
                return _items.SET_DR_BY_RANK.get(r, 0.0) * 100.0
        return 0.0

    def low_hp_extra_dr_pct(self) -> float:
        """低血线额外减免（%）。肉盾套上品专属（气血 <30% 时生效）。"""
        return self.set_mech("low_hp_extra_dr_pct")

    def cd_reduce(self) -> int:
        """秘典冷却缩减（回合）。法穿套上品专属。"""
        return int(self.set_mech("cd_reduce"))

    def atk(self, item_db: dict[str, dict]) -> int:
        """面板攻击力 = 基础 × 乘区 × (1 + 装备%) × (1 + 特殊装备%)。"""
        base = int(base_atk(self.turns) * self.stat_mult())
        mult = (1.0 + self.equip_pct("atk_pct", item_db)) * (1.0 + self.special_pct())
        return int(base * mult)

    def defense(self, item_db: dict[str, dict]) -> int:
        """面板防御力 = 基础 × 乘区 × (1 + 装备%) × (1 + 特殊装备%)。"""
        base = int(base_def(self.turns) * self.stat_mult())
        mult = (1.0 + self.equip_pct("def_pct", item_db)) * (1.0 + self.special_pct())
        return int(base * mult)

    def max_hp(self, item_db: dict[str, dict]) -> int:
        """面板气血上限 = 基础 × 乘区 × (1 + 装备%) × (1 + 特殊装备%)。"""
        base = int(base_hp(self.turns) * self.stat_mult())
        mult = (1.0 + self.equip_pct("hp_pct", item_db)) * (1.0 + self.special_pct())
        return int(base * mult)

    @property
    def needed_exp(self) -> int:
        """精进到下一转还需多少修为。"""
        return exp_needed(self.level)

    # ---------------- 背包操作 ----------------

    def add_item(self, name: str, count: int = 1, limit: int | None = None) -> bool:
        """加物品，返回 False 表示背包已满。

        limit 用于让调用方传入配置里的背包上限；
        不传则用模块默认值。
        """
        cap = MAX_INVENTORY if limit is None else limit
        if name not in self.inventory and len(self.inventory) >= cap:
            return False
        self.inventory[name] = self.inventory.get(name, 0) + count
        return True

    def remove_item(self, name: str, count: int = 1) -> bool:
        """扣物品，返回 False 表示数量不足。"""
        have = self.inventory.get(name, 0)
        if have < count:
            return False
        if have == count:
            del self.inventory[name]
        else:
            self.inventory[name] = have - count
        return True

    # ---------------- 背包名字解析（v3.4 新增，指令层统一入口） ----------------

    def find_inventory_name(self, name: str, *, prefer_quality: bool = True) -> str | None:
        """在储物袋里找一件与 `name` 匹配的物品，返回**实际存储的名字**。

        【为什么必须有这个方法 —— 这是 v3.4 最容易踩的一类坑】

        储物袋里的名字是**带品质后缀**的完整名（`小回气丹[普]`、
        `青锋剑[稀]`），而玩家输入的几乎总是基础名（`小回气丹`、
        `青锋剑`）。如果指令里直接写：

            player.inventory.get(item_name)

        就会因为「名字不完全相等」而查不到 —— 玩家明明有 5 颗丹药，
        却被告知「你没有」。这类 bug 不会崩，只会让玩家觉得游戏坏了。

        匹配规则：
          1. 完全相等 -> 直接命中（快路径，也保证精确指定品质时行为不变）
          2. 同基础名 -> 命中；`prefer_quality=True` 时品质高者优先，
             否则品质**低者**优先

        为什么默认品质高者优先：玩家说「装上剑」「吃回气丹」时，
        默认意图是**用最好的那件**。反过来「品质最低优先」虽然省药，
        但会让玩家困惑「为什么我的传说剑没装上」。

        为什么卖出场景要反过来：卖东西时意图相反 —— 先出手最不值钱的，
        把好的留下。所以 /出售 显式传 prefer_quality=False。

        返回 None 表示储物袋里确实没有。

        ⚠️ 两个分支都必须是**全量扫描后取极值**，不能遇到第一个就 return ——
        那等价于依赖 dict 迭代顺序，行为不可复现（踩过：同一份背包
        在两次调用里返回了不同品质的装备）。
        """
        if not isinstance(name, str) or not name:
            return None
        if self.inventory.get(name, 0) > 0:
            return name

        base, _q = _split_quality_name(name)
        best: str | None = None
        best_key: tuple[float, str] | None = None
        for inv_name, cnt in self.inventory.items():
            if cnt <= 0:
                continue
            inv_base, inv_q = _split_quality_name(inv_name)
            if inv_base != base:
                continue
            rank = _quality_rank(inv_q)
            # 排序键：品质倍率（高优先时取负）+ 名字（保证唯一确定）
            sort_rank = -rank if prefer_quality else rank
            key = (sort_rank, inv_name)
            if best_key is None or key < best_key:
                best, best_key = inv_name, key
        return best

    def has_item(self, name: str) -> bool:
        """储物袋里是否有该物品（自动匹配品质后缀）。"""
        return self.find_inventory_name(name) is not None

    def consume_item(self, name: str, count: int = 1) -> str | None:
        """消耗物品，自动解析品质后缀。成功返回实际消耗的名字。"""
        actual = self.find_inventory_name(name)
        if actual is None:
            return None
        if not self.remove_item(actual, count):
            return None
        return actual

    # ---------------- 秘典操作 ----------------

    def learned_tier(self, scripture_name: str) -> int:
        """某条秘典已修到几阶。0 表示未习得。"""
        try:
            return int(self.scriptures.get(scripture_name, 0) or 0)
        except (TypeError, ValueError):
            return 0

    def equip_scripture(self, name: str) -> tuple[bool, str]:
        """装配一条秘典。返回 (是否成功, 提示)。"""
        from .scriptures import has_scripture

        if not has_scripture(name):
            return False, f"没有「{name}」这条大道。"
        if self.learned_tier(name) <= 0:
            return False, f"你还没有习得「{name}」。"
        if name in self.equipped_scriptures:
            return False, f"「{name}」已经装配了。"
        if len(self.equipped_scriptures) >= self.equip_slots:
            return False, (
                f"装配位已满（{len(self.equipped_scriptures)}/{self.equip_slots}）。"
                f"突破大境界可以增加装配位。"
            )
        self.equipped_scriptures.append(name)
        return True, f"已装配「{name}」。"

    def unequip_scripture(self, name: str) -> tuple[bool, str]:
        """卸下一条秘典。"""
        if name not in self.equipped_scriptures:
            return False, f"「{name}」没有装配。"
        self.equipped_scriptures.remove(name)
        return True, f"已卸下「{name}」。"

    # ---------------- 序列化 ----------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Player":
        """从存档还原。

        用已知字段名白名单过滤，避免旧存档里的多余字段导致
        TypeError（比如以后删掉的字段还留在 JSON 里）。
        group_id 缺失时默认为空字符串，兼容旧版存档格式。

        v2.0 新增字段（avatar / created_at / last_hunt_ts / rest_until）
        与 v3.0 新增字段（scriptures / equipped_scriptures / ascensions /
        breakthroughs / last_cultivate_ts / last_explore_ts / explore_start_ts）
        全部有默认值，老存档读进来自动补齐，不会崩。
        """
        known = {f for f in cls.__dataclass_fields__}
        payload = {k: v for k, v in data.items() if k in known}
        payload.setdefault("group_id", "")
        # 老存档没记录建号时间，用 0 表示「未知」，UI 层不展示即可
        payload.setdefault("created_at", 0.0)

        # v3 迁移：v2 的 level 上限是 200，超过 72 的一律按 72 处理，
        # 避免出现「大乘期 137 转」这种越界展示。
        if "level" in payload:
            payload["level"] = realm_mod.clamp_turns(payload["level"])

        # 老存档的 experience 字段名可能是 exp，已在白名单里，无需处理
        # scriptures / equipped 可能存成 None，做一次兜底
        if not isinstance(payload.get("scriptures"), dict):
            payload["scriptures"] = {}
        if not isinstance(payload.get("equipped_scriptures"), list):
            payload["equipped_scriptures"] = []
        # 老存档可能把装备表存成 None，做一次兜底
        if not isinstance(payload.get("equipment"), dict):
            payload["equipment"] = {}
        if not isinstance(payload.get("inventory"), dict):
            payload["inventory"] = {}

        # v3 迁移：旧物品名 -> 新物品名（小型生命药水 -> 小回气丹 等）。
        #
        # 必须在这里做，而不是在 add_item / 查表时做：
        # 老存档里的名字是**存量数据**，不迁移的话：
        #   - /使用 小型生命药水 查不到定义，丹药成了废品
        #   - /背包 显示的是 v2 的名字，与 /坊市 里的新名对不上
        #
        # 【幂等性怎么保证】先拆品质后缀、只对基础名做映射、再拼回去。
        # 早期版本是直接对整个名字映射，导致带后缀的名字
        # （如「寒铁剑[稀]」）匹配不上映射表，而「小回气丹[common]」
        # 这种被错误重构过的名字又会被二次拼接成 [common][common]。
        # 现在的写法：基础名映射表里只有旧名，新名原样返回 -> 天然幂等。
        inv_raw = payload["inventory"]
        migrated_inv: dict[str, int] = {}
        for nm, cnt in inv_raw.items():
            if isinstance(nm, str):
                _b, _q = _split_quality_name(nm)
                new_nm = _rebuild_item_name(_resolve_item_alias(_b), _q)
            else:
                new_nm = nm
            # 同名合并：若新旧名同时存在（如 小型生命药水 + 小回气丹），
            # 数量要相加而不是后者覆盖前者
            migrated_inv[new_nm] = migrated_inv.get(new_nm, 0) + cnt
        payload["inventory"] = migrated_inv

        # v3 迁移：旧部位名 -> 新部位名（武器 -> weapon 等）
        # v3.4：同时迁移**装备名**（铁剑 -> 寒铁剑 等）。
        #
        # 装备名迁移比部位名更要紧：v3.4 把 42 件旧装备整体换成了
        # 十品体系（135 件）。不迁移的话，老玩家身上穿的、包里存的
        # 旧装备名在新表里查不到 -> 属性全部归零、/背包 显示为空。
        # 迁移是幂等的（新名不在映射表里，原样返回）。
        equip_raw = payload["equipment"]
        migrated_eq: dict = {}
        for slot, val in equip_raw.items():
            new_slot = SLOT_ALIASES.get(slot, slot) if isinstance(slot, str) else slot
            if isinstance(val, str) and val:
                base, qual = _split_quality_name(val)
                new_base = _resolve_item_alias(base)
                # 用 _rebuild_item_name 拼回去（它会把品质 key 转成中文标记）
                val = _rebuild_item_name(new_base, qual)
            migrated_eq[new_slot] = val
        payload["equipment"] = migrated_eq

        # v3.4：特殊装备字段兜底（老存档没有）
        if not isinstance(payload.get("special"), str):
            payload["special"] = ""
        try:
            payload["special_growth_pct"] = float(payload.get("special_growth_pct", 0.0) or 0.0)
        except (TypeError, ValueError):
            payload["special_growth_pct"] = 0.0

        # v3.5：天道令相关字段兜底（老存档没有）
        #
        # special_owned 只增不减，所以这里**不能**用「无则置空」了事 ——
        # 要兼容三种脏数据：缺失(None) / 存成了字符串 / 列表里混进非字符串。
        # 用 dict.fromkeys 去重且保持顺序（兑换流水有先后意义）。
        _owned = payload.get("special_owned")
        if not isinstance(_owned, list):
            _owned = []
        payload["special_owned"] = list(
            dict.fromkeys(str(x) for x in _owned if isinstance(x, str) and x)
        )
        if not isinstance(payload.get("sign_in_date"), str):
            payload["sign_in_date"] = ""

        return cls(**payload)

