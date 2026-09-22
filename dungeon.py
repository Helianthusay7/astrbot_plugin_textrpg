"""秘境（副本）：无限层，逐层递增，自动连闯。

设计要点（2026-09-19 与老板确认）：

1. **难度用固定锚点，玩家实力决定能打多深。**
   第 N 层的怪属性 = 「锚点玩家」的战力 × 层数倍率，
   锚点玩家 = 大乘九转（72 转）的裸装属性，是**常量**。
   所以：
   - 1 转的玩家打第 1 层就很吃力（他远低于锚点）
   - 大乘期的玩家能轻松推到几十层
   - 转数、法宝、秘典、转生，全都能转化成副本进度
   这是"成长感"的来源。

2. **奖励只看层数，不看玩家转数。**
   打通第 N 层给多少，对任何玩家都一样。
   避免"转数高了奖励自动膨胀"，也让排行榜有可比性。

3. **自动连闯，失败即止。**
   `/秘境` 一次指令，从起始层逐层挑战，
   赢了自动进下一层，输了（或平局）立刻停止并汇报战果。

4. **失败不计死亡、不掉转。**
   秘境是「试炼」，不是「历练」。
   失败只是终止本次闯关，不触发死亡惩罚（不掉转、不进休整）。

5. **每日一次。**
   按自然日重置（用本地日期字符串比较），
   防止玩家无限刷秘境跳过修炼过程。

6. **从"历史最高层"往回打。**
   每次从 max(1, best-2) 层开始，快速跳过已碾压的层，
   让玩家把时间花在真正有挑战的层数上。

本模块负责「副本的全部逻辑」：难度曲线、奖励、连闯流程、每日限制。
`main.py` 只做指令解析与文案渲染，`battle.py` 只提供单场战斗。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from . import realm as realm_mod
from . import player as player_mod

# ---------------------------------------------------------------------------
# 难度曲线
# ---------------------------------------------------------------------------

# 【V2 重做 2026-09-19 晚，老板定的三条】
#
# 老板原话：
#   「难度设计的不够合理，应该按照第一层最简单，然后逐层加难度，
#     妖兽属性走成长曲线，刚开始怪物属性尽量低，然后成长，并且我前面
#     设计的"怪血必须在玩家10回合输出上限以下"这个限制条件的前提是错的，
#     因为玩家可以靠装备属性来提高输出，比如穿甲和增伤。」
#
# 所以旧模型的三个错误：
#   ✗ 旧模型把"怪血 <= 玩家10回合输出上限"当硬约束 —— 那只在裸装纯普攻
#     时成立。实测裸装输出 = 1.8×自身气血，加秘典/装备能到 3~4×，
#     所以怪血完全可以超过这个"上限"。
#   ✗ 旧模型用单一系数（DUNGEON_LAYER_STEP）拉难度，怪血恒定，
#     结果是"打得动就全赢、打不动就全输"的二元阶跃。
#   ✗ 旧模型第 1 层就按 0.9~1.0 倍锚点算，新人直接卡死，副本失去意义。

# ---- 成长曲线：三围各自独立，各按各的步长涨 ----
#
# 第 1 层刻意做弱，给新玩家正反馈；之后每层复利递增。
# 复利很可怕：第 30 层三围倍率 = 0.70 * 1.13^29 ≈ 25 倍，
# 第 50 层 ≈ 285 倍 —— 无限层自然形成"迟早会卡住"的天花板，
# 不需要额外写死上限。
#
# 标定结论（锚点 1 转，selftest [47] 钉住）：
#   1转→L2  3转→L10  10转→L21  30转→L33  50转→L40  72转→L46
# 每个转数档位都有明确不同的深度，"转数越高打得越远"成立。
DUNGEON_HP_BASE = 0.70        # 第 1 层怪血 = 锚点血 × 0.70
DUNGEON_HP_STEP = 1.13        # 每层气血 ×1.13
DUNGEON_ATK_BASE = 1.20       # 第 1 层怪攻 = 锚点攻 × 1.20
DUNGEON_ATK_STEP = 1.13       # 每层攻击 ×1.13
DUNGEON_DEF_BASE = 0.40       # 第 1 层怪防 = 锚点防 × 0.40
DUNGEON_DEF_STEP = 1.13       # 每层防御 ×1.13

# 单次闯关最多打多少层（防止一次指令跑太久）
DUNGEON_MAX_LAYERS_PER_RUN = 60

# 从历史最高层往回退几层开始打（跳过已碾压的层）
#
# v3.3：2 -> 4（2026-09-19 秘境数值复核后调整）
#
# 【为什么改】backoff=2 时起始层太高，玩家一进秘境就在天花板上撞墙，
# 前两场就是本日最难的两场，缺少"热身、逐层爬"的节奏感。
# 退 4 层后多打 4 场已碾压的低层 —— 它们打得快、不吃资源，
# 但让「进秘境」这件事有起承转合，而不是开门就挨打。
#
# 【⚠️ 实测澄清：这个改动**不解决**「进度停滞」】
# 曾以为退更多层能提高净推进速度，实测否定了这个猜测。
# 12 天连续闯关模拟（真实 run_dungeon）：
#   backoff=2: 10转 18->18(+0)／30转 30->30(+0)／72转 43->44(+1)
#   backoff=4: 10转 18->19(+1)／30转 30->30(+0)／72转 43->44(+1)
# 中期（30 转 / 46 转）无论 backoff 取多少都是 0 —— 因为**卡点是曲线天花板，
# 不是起始层**。真正的修法见「秘境副本数值设计.md」（重刷锚定产出 /
# 首通里程碑），那些是独立的改动，不在本参数职责范围内。
#
# 一句话：这个参数管的是「节奏感」，不是「推进速度」。
DUNGEON_WARMUP_BACKOFF = 4

# 妖兽等级固定用这个值，让 monster_stats 走「不缩放」分支 ——
# 秘境自己算好了属性，不需要再被按转数缩放一次。
#
# 为什么用 0 而不是 None：None 也走不缩放，但 0 能让调用方一眼看出
# "这是有意为之"，而不是"忘了传参数"。
DUNGEON_MONSTER_LEVEL = 0

# 气血安全上限：怪血最多不超过玩家气血的这个比例，防止"血厚到打不死"
# 的纯平局关卡。
#
# 这个上限是**兜底**，不是设计基准 —— 正常情况下曲线自己算出来的怪血
# 在低层远低于它，只有层数很高时才会被它拦住。
#
# v3.3：3.0 -> 5.0（跟随回合上限 10->30 一起抬，保持"兜底线"的语义）
# 30 回合输出约为 10 回合的 3 倍，兜底线等比抬到 5.0。
#
# 【⚠️ 实测：当前曲线下这个参数**从未被触发**】
# cap=3.0 / 5.0 / 99.0 三档跑首通，各转数最高层完全一致
# （1转 2.0 ／10转 18.3 ／21转 25.4 ／30转 29.9 ／46转 36.7 ／60转 40.2 ／72转 43.1）。
# 说明曲线自身的怪血一直没有撞到这条线。
# 也就是说：这个改动**既不影响难度，也不影响深度**，是"把安全垫抬到
# 与新回合上限匹配的高度"，防的是将来加内容后撞线，不是修当前问题。
DUNGEON_HP_CAP_RATIO = 5.0

# ---------------------------------------------------------------------------
# 难度锚点
# ---------------------------------------------------------------------------
#
# 难度基准是**常量**：取锚点转数的裸装属性，之后层数倍率纯粹按层数复利。
# 玩家转数越高 -> 自身属性越高 -> 自然能过越深的层。
#
# 锚点为什么取 1 转而不是 72 转：
#   锚点越高，第 1 层的怪就越强，新人越进不来。
#   实测（见 selftest [47]）：
#     锚点 72 转 -> 1 转玩家连第 1 层都打不过，副本对新人是死内容
#     锚点  3 转 -> 1 转玩家仍然 0%，因为 0.7×3转血 已经超出新人输出
#     锚点  1 转 -> 1 转玩家能过 2 层，72 转能过 46 层，曲线完全连续
#   锚点取最低档（1 转 = 炼气期一转），曲线的起点就落在最弱玩家脚下，
#   整条成长线才没有断档。
DUNGEON_ANCHOR_TURNS = 1

_ANCHOR_CACHE: dict | None = None


def anchor_stats() -> dict[str, int]:
    """锚点玩家的裸装属性（DUNGEON_ANCHOR_TURNS 转）。算一次缓存起来。"""
    global _ANCHOR_CACHE
    if _ANCHOR_CACHE is None:
        t = DUNGEON_ANCHOR_TURNS
        sm = realm_mod.stat_mult(t, 0, 0)
        _ANCHOR_CACHE = {
            "atk": max(1, int(player_mod.base_atk(t) * sm)),
            "def": max(0, int(player_mod.base_def(t) * sm)),
            "hp": max(1, int(player_mod.base_hp(t) * sm)),
        }
    return _ANCHOR_CACHE


def layer_ratio(layer: int) -> float:
    """第 layer 层的气血倍率（相对锚点）。

    保留这个函数供面板/文案展示用（显示"当前层强度倍率"）。
    真正的三围计算见 build_monster，气血/攻击/防御各有各的步长。
    """
    try:
        n = max(1, int(layer))
    except (TypeError, ValueError):
        n = 1
    return DUNGEON_HP_BASE * (DUNGEON_HP_STEP ** (n - 1))


def layer_stats(layer: int) -> dict[str, int]:
    """第 layer 层守关怪的三围（绝对值，单位与玩家面板一致）。

    这是难度曲线的**唯一真相源** —— build_monster 和面板展示都走这里，
    保证"展示的强度"和"实际打的怪"永远一致。
    """
    try:
        n = max(1, int(layer))
    except (TypeError, ValueError):
        n = 1
    a = anchor_stats()
    k = n - 1
    return {
        "hp": max(1, int(a["hp"] * DUNGEON_HP_BASE * (DUNGEON_HP_STEP ** k))),
        "atk": max(1, int(a["atk"] * DUNGEON_ATK_BASE * (DUNGEON_ATK_STEP ** k))),
        "defense": max(
            0, int(a["def"] * DUNGEON_DEF_BASE * (DUNGEON_DEF_STEP ** k))
        ),
    }


def layer_power(layer: int) -> int:
    """第 layer 层的怪战力（用于面板对比玩家战力）。"""
    s = layer_stats(layer)
    return int(s["atk"] * 2.0 + s["defense"] * 1.5 + s["hp"] * 0.5)


def build_monster(layer: int, player=None, item_db: dict | None = None) -> dict:
    """构造第 layer 层的守关怪。

    ⚠️ 难度参数在文件顶部，改动前先读上面那段「V2 重做」注释。

    曲线（全部相对 DUNGEON_ANCHOR_TURNS 转的裸装属性）：

        气血 = 锚点血 × 0.70 × 1.13^(n-1)
        攻击 = 锚点攻 × 1.20 × 1.13^(n-1)
        防御 = 锚点防 × 0.40 × 1.13^(n-1)
        等级 = 0                    <- 让 monster_stats 走不缩放分支

    第 1 层刻意做弱，新人能过；之后缓慢复利，第 30 层三围已经
    约 25 倍锚点，第 50 层约 285 倍 —— 无限层的"天花板"是复利
    自己形成的，不需要额外写死上限。

    兜底：传了 player 时，怪血会被钳制在玩家气血的
    DUNGEON_HP_CAP_RATIO 倍以内。这是**保护**不是设计 ——
    只有层数很高时才会触发，防止出现"10 回合必然打不死"的纯平局层。

    标定后的胜率曲线见 selftest [47]。
    """
    n = max(1, int(layer))
    s = layer_stats(n)
    hp = s["hp"]

    # 气血兜底（见 docstring 最后一段）
    #
    # 注意 max_hp 的签名是 max_hp(item_db)，必须把 item_db 透传进来。
    # 早先这里写成了 player.max_hp()，抛 TypeError 后被下面的
    # except 静默吞掉，导致高层怪血的兜底**完全失效** ——
    # 第 999 层算出了 6.5e54 的血量，玩家必死。
    # 教训：宽泛的 except 会把"参数传错"这种真 bug 伪装成"没事"。
    if player is not None:
        try:
            p_hp = int(player.max_hp(item_db or {}))
            if p_hp > 0:
                hp = min(hp, max(1, int(p_hp * DUNGEON_HP_CAP_RATIO)))
        except Exception:  # noqa: BLE001 - 面板异常不该让副本崩掉
            pass

    return {
        "name": f"秘境守卫 · 第 {n} 层",
        "level": DUNGEON_MONSTER_LEVEL,
        "hp": max(1, int(hp)),
        "atk": s["atk"],
        "defense": s["defense"],
        "exp": 0,          # 秘境修为由 dungeon_qi_reward 单独给
        "gold": (0, 0),
        "drops": [],
        "element": _layer_element(n),
        # 关键标记：告诉 monster_stats "这怪已经标定好了，别再缩"。
        # 不加这个，副本难度会被按转数缩放的逻辑覆盖掉。
        "prescaled": True,
    }


_ELEMENTS = ("fire", "water", "earth", "wind")


def _layer_element(layer: int) -> str:
    """按层数轮换五行属性，让不同秘典抗性都有用武之地。"""
    return _ELEMENTS[(max(1, int(layer)) - 1) % len(_ELEMENTS)]


# ---------------------------------------------------------------------------
# 奖励：只看层数，不看转数
# ---------------------------------------------------------------------------
#
# 老板明确要求：
#   ①「副本奖励肯定要与难度挂钩，这个可不是根据玩家转数定」
#   ②「奖励就是首通给多，后刷大幅降低，或者说干脆不要重复刷，你来决定」
#
# 决策（两条都做，合成一个自洽的机制）：
#   - 奖励数值**只按层数**算，与玩家转数完全解耦（便于横向比较）。
#   - **首通全额**，**重刷按 RERUN_MULT 打折**。
#   - 配合「每日一次」+「从 best-2 层开打」，实际效果是：
#       低层（已通关的）只是热身，几乎不给东西；
#       越接近自己纪录、越往新层推进，收益越高。
#
# 为什么不做"干脆不能重复刷"：
#   每日一次本来就把重复刷压到极低频；再从 best-2 开打，
#   玩家每天必然要重打几层热身。若这几层 0 收益，
#   会让"每日一次"的体验变得很亏 —— 打折比归零更合理。

# 打通第 N 层的基础修为奖励 = DUNGEON_QI_BASE * N^1.35
DUNGEON_QI_BASE = 60
DUNGEON_QI_EXPONENT = 1.35

# 重刷（非首通）层的奖励倍率。
# 取 0.25 = 打到已通关的层只有 1/4 收益，逼玩家往新层推进。
DUNGEON_RERUN_MULT = 0.25

# 灵石奖励
DUNGEON_GOLD_BASE = 12
DUNGEON_GOLD_EXPONENT = 1.25

# 每隔多少层给一次物品奖励
DUNGEON_ITEM_EVERY = 3


def clear_multiplier(layer: int, best_before: int) -> float:
    """这一层是首通还是重刷，对应什么奖励倍率。

    best_before = 本次闯关**开始前**的历史最高层。
      - layer > best_before  -> 首通，全额（1.0）
      - layer <= best_before -> 重刷，打折（DUNGEON_RERUN_MULT）
    """
    n = max(1, int(layer))
    return 1.0 if n > int(best_before) else DUNGEON_RERUN_MULT


def dungeon_qi_reward(layer: int, best_before: int | None = None) -> int:
    """打通第 layer 层的修为奖励（不乘任何玩家属性）。

    best_before 传 None 表示"按首通全额算"（纯展示用）。
    """
    n = max(1, int(layer))
    base = DUNGEON_QI_BASE * (n ** DUNGEON_QI_EXPONENT)
    mult = 1.0 if best_before is None else clear_multiplier(n, best_before)
    return int(base * mult)


def dungeon_gold_reward(layer: int, best_before: int | None = None) -> int:
    """打通第 layer 层的灵石奖励。规则同 dungeon_qi_reward。"""
    n = max(1, int(layer))
    base = DUNGEON_GOLD_BASE * (n ** DUNGEON_GOLD_EXPONENT)
    mult = 1.0 if best_before is None else clear_multiplier(n, best_before)
    return int(base * mult)


def dungeon_item_layers(layer: int, best_before: int | None = None) -> bool:
    """这一层是否给物品奖励。

    每 DUNGEON_ITEM_EVERY 层给一次；**重刷层不给物品**
    （物品是最值钱的奖励，只该在首通时拿）。
    """
    n = max(1, int(layer))
    if n % DUNGEON_ITEM_EVERY != 0:
        return False
    if best_before is None:
        return True
    return n > int(best_before)


# ---------------------------------------------------------------------------
# 闯关结果
# ---------------------------------------------------------------------------


@dataclass
class DungeonRun:
    """一次 /秘境 的完整战果。"""

    start_layer: int = 1
    cleared: int = 0                    # 这次通了几层
    first_clears: int = 0               # 其中首通（破纪录）的层数
    reached_layer: int = 0              # 打到第几层（含失败那层）
    failed_layer: int = 0               # 在哪层失败，0 表示打满上限
    qi_gain: int = 0
    gold_gain: int = 0
    items: list[str] = field(default_factory=list)
    best_layer: int = 0                 # 历史最高层（结算后）
    is_new_record: bool = False
    capped: bool = False                # 是否因达到单次上限而停止
    logs: list[str] = field(default_factory=list)

    # ---------------- v3.5 新增 ----------------
    # 本次获得的天道令数量（**整趟最多 1 枚**，见 run_dungeon 里的说明）
    tokens: int = 0
    # 本次拿到的里程碑装备（首通 30/35/40 层给的七/八/九品）
    milestone_items: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 每日限制
# ---------------------------------------------------------------------------


def today_str(now: float | None = None) -> str:
    """本地日期字符串，用于"每日一次"的判定。"""
    ts = time.time() if now is None else float(now)
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def can_run_today(player, now: float | None = None) -> bool:
    """今天是否还能闯秘境。"""
    last = str(getattr(player, "dungeon_date", "") or "")
    return last != today_str(now)


def mark_ran(player, now: float | None = None) -> None:
    """记录今天已经闯过。"""
    player.dungeon_date = today_str(now)


# ---------------------------------------------------------------------------
# 起始层
# ---------------------------------------------------------------------------


def start_layer_for(player) -> int:
    """本次从第几层开始闯。

    从历史最高层往回退 DUNGEON_WARMUP_BACKOFF 层，
    这样不用每次从第 1 层重新碾压一遍。
    """
    best = int(getattr(player, "dungeon_best", 0) or 0)
    if best <= 0:
        return 1
    return max(1, best - DUNGEON_WARMUP_BACKOFF)


# ---------------------------------------------------------------------------
# 连闯流程
# ---------------------------------------------------------------------------


def _roll_layer_item(layer: int) -> str | None:
    """按层数抽一件物品奖励。层数越高，越可能出好的。

    物品池跨境界扩大，但**不按玩家转数**筛 —— 严格遵循
    「奖励只跟层数有关」的规则。
    """
    try:
        from .items import EQUIPMENTS, explore_consumables
    except Exception:  # noqa: BLE001 - 循环导入保护
        return None

    n = max(1, int(layer))
    # 层数越高，出装备的概率越大（1~4 层几乎只出丹药）
    equip_chance = min(0.65, 0.10 + n * 0.035)
    if random.random() < equip_chance:
        pool = [e["name"] for e in EQUIPMENTS]
        if pool:
            return random.choice(pool)
    pool = explore_consumables()
    return random.choice(pool) if pool else None


# ---------------------------------------------------------------------------
# v3.5：秘境里程碑（七/八/九品装备的主出口）
# ---------------------------------------------------------------------------
#
# 【为什么需要它】
# v3.5 把七品以上的装备**从坊市下架**了（`items.SHOP_MAX_TIER_RANK = 6`）。
# 下架是对的（顶级装备不该花钱买），但必须有个「打得出来」的出口，
# 否则顶级装备会**断供**。
#
# 出口有两条：
#   1. 这里 —— 秘境里程碑，**最确定**的主出口
#   2. `explore_equipment_pool()` —— 已有，但 135 件里随机，单件期望低，
#      定位是「日常补给」而非主出口
#
# 【为什么必须「指定品阶」而不是走全池随机】
# 里程碑的意义就是「爬到这一层，确定拿到一件顶级装备」。
# 如果还走全池随机，玩家爬到 40 层拿到一品剑，会骂人的。
DUNGEON_TIER_MILESTONES: dict[int, int] = {
    30: 7,   # 首通第 30 层 -> 七品
    35: 8,   # 首通第 35 层 -> 八品
    40: 9,   # 首通第 40 层 -> 九品
}


def _roll_tier_item(tier_rank: int) -> str | None:
    """按品阶抽一件装备。**部位随机、套系随机** ——
    让玩家有「再来一件凑套」的动力，而不是一次毕业。"""
    try:
        from .items import EQUIPMENTS
    except Exception:  # noqa: BLE001 - 循环导入保护
        return None
    try:
        want = int(tier_rank)
    except (TypeError, ValueError):
        return None
    pool = [e["name"] for e in EQUIPMENTS if int(e.get("tier_rank", 0)) == want]
    return random.choice(pool) if pool else None


def _grant_token(player, count: int, inventory_limit: int | None = None) -> int:
    """发天道令。返回实际到手数量。

    真正实现在 `special.grant_token()` —— 签到（main.py）和秘境首通
    （这里）共用同一份逻辑（含「背包满强制入包」的兜底），不重复实现。
    """
    try:
        from . import special as special_mod
    except Exception:  # noqa: BLE001
        return 0
    return special_mod.grant_token(player, count, limit=inventory_limit)


def run_dungeon(
    player,
    item_db: dict[str, dict] | None = None,
    inventory_limit: int | None = None,
) -> DungeonRun:
    """从起始层开始自动连闯，直到失败或打满本次上限。

    直接修改传入的 player：
      - 加修为、加灵石、加物品
      - 更新 dungeon_best / dungeon_date
      - **不**改 hp / level / total_deaths（失败不计死亡、不掉转）

    每次战斗前会补满气血 —— 秘境是"满血开打"的试炼，
    否则前面几层的消耗会让玩家在后面的层被"累积伤害"杀死，
    那不是难度，是折磨。

    奖励规则（见上面「奖励」小节）：
      首通层 = 全额，重刷层 = 打 DUNGEON_RERUN_MULT 折，重刷层不给物品。
      首通/重刷的分界线是**本次开始前**的历史最高层 best_before。
    """
    from . import battle as battle_mod

    db = item_db if item_db is not None else {}

    result = DungeonRun()
    start = start_layer_for(player)
    result.start_layer = start
    # 本次开始前的纪录，作为「首通/重刷」的分界线。
    # 必须在结算更新 dungeon_best 之前取，否则整趟都会被算成重刷。
    best_before = int(getattr(player, "dungeon_best", 0) or 0)

    for offset in range(DUNGEON_MAX_LAYERS_PER_RUN):
        layer = start + offset
        result.reached_layer = layer

        # 每层满血开打（见 docstring）
        try:
            player.full_heal(db)
        except TypeError:
            player.full_heal()

        monster = build_monster(layer, player, db)
        # count_death=False：秘境是试炼，败北不计死亡（老板硬要求）。
        # 不清气血、不掉转、不进休整 —— 这些都靠「只传 count_death」实现。
        fight_res = battle_mod.fight(player, monster, db, count_death=False)

        if not fight_res.won:
            # 失败/平局：立刻停止。不计死亡、不掉转。
            result.failed_layer = layer
            result.logs.append(
                f"第 {layer} 层：{battle_mod.OUTCOME_LABELS.get(fight_res.outcome, '失败')}"
            )
            break

        # ---- 通关本层：结算奖励 ----
        result.cleared += 1
        is_first = layer > best_before
        qi = dungeon_qi_reward(layer, best_before)
        gold = dungeon_gold_reward(layer, best_before)
        result.qi_gain += qi
        result.gold_gain += gold
        if is_first:
            result.first_clears += 1

        outcome_label = battle_mod.OUTCOME_LABELS.get(
            fight_res.outcome, "通关"
        )
        mark = "🆕" if is_first else "↻"
        result.logs.append(
            f"{mark} 第 {layer} 层：{outcome_label}  +{qi} 修为"
        )

        if dungeon_item_layers(layer, best_before):
            item = _roll_layer_item(layer)
            if item:
                added = player.add_item(item, 1, inventory_limit)
                if added:
                    result.items.append(item)

        # ---- v3.5 秘境里程碑：首通指定层 → 指定品阶装备 ----
        # 只在 `is_first` 时给（重刷拿不到），避免「反复刷同一层刷满套装」。
        _ms_tier = DUNGEON_TIER_MILESTONES.get(layer)
        if _ms_tier and is_first:
            _ms_item = _roll_tier_item(_ms_tier)
            if _ms_item:
                if not player.add_item(_ms_item, 1, inventory_limit):
                    # 背包满也要给 —— 里程碑是「爬到这一层的确定回报」，
                    # 静默吞掉它比多占 1 格严重得多（同天道令的处理逻辑）
                    player.inventory[_ms_item] = (
                        int(player.inventory.get(_ms_item, 0) or 0) + 1
                    )
                    result.logs.append(f"　（背包已满，{_ms_item} 已强行放入）")
                result.milestone_items.append(_ms_item)
                result.logs.append(
                    f"🏆 第 {layer} 层里程碑：获得 {_ms_tier} 品装备「{_ms_item}」"
                )
    else:
        # for 跑完没 break = 打满单次上限
        result.capped = True

    # ---- v3.5 天道令：整趟只要破了纪录（有首通层）就发 1 枚 ----
    #
    # ⚠️ **按「整趟」发，不是按「每层」发。** 这个口径是硬要求：
    #   每首通一层给 1 枚 → 玩家从第 5 层开打、连破 20 层，
    #   一次拿 20 枚，5 天就凑齐帝兵，节奏全崩。
    #   整趟有首通才给 1 枚 → 一天最多 1 枚，节奏可控。
    #
    # 这个判定**天然防刷**：打不过去的人拿不到，反复重刷老层也拿不到，
    # 不需要额外加计数器或冷却。
    if result.first_clears > 0:
        from .special import TOKEN_FROM_DUNGEON
        result.tokens = _grant_token(player, TOKEN_FROM_DUNGEON, inventory_limit)

    # ---- 更新历史最高层 ----
    # 记录「通关到第几层」。一层没通就保持原记录不动。
    if result.cleared > 0:
        reached = result.start_layer + result.cleared - 1
        old_best = int(getattr(player, "dungeon_best", 0) or 0)
        if reached > old_best:
            player.dungeon_best = reached
            result.is_new_record = True
    result.best_layer = int(getattr(player, "dungeon_best", 0) or 0)

    return result

