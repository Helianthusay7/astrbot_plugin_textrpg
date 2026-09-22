"""特殊装备（帝兵）与天道抽奖 —— 系统层。

【设计约束（老板定的，改代码前务必读）】

1. **不写死。** 帝兵现在只有 1 件，但老板明确说「后续可能会出更多，
   我打的比方只是一件帝兵」。所以：
   - 数据全在 `items.SPECIAL_EQUIPMENTS` 里，这里是**逻辑**，不是清单；
   - 新加一件帝兵 = 往表里加一条记录，**不需要改这个文件**；
   - 成长被动的种类走 `PASSIVE_HANDLERS` 注册表，加新被动只加函数。

2. **不占普通装备槽。** 帝兵放【特殊】栏（`Player.special`），
   加成**叠乘**在 5 件普通装备之上。没帝兵时不显示【特殊】栏。

3. **无阶位限制。** 1 转就能带（这是帝兵的核心价值 —— 越早拿越强）。

4. **成长被动「具体问题具体分析」。** 老板没想好细节，所以：
   - 「怎么涨、涨到哪、死不死」全部由数据里的 `passives` 决定；
   - 累加值存玩家存档（`special_growth_pct`），不存则在数据里写死。

5. **获取途径走天道令兑换（`source: "token_exchange"`）。**
   老板 v3.5 拍板：**废弃天道抽奖**，改「攒 100 枚天道令换一件」。
   理由是结构性的 —— 抽奖 + 唯一物是死局：概率低了活动形同虚设，
   概率高了第一个人抽走后池子空掉，后面所有人抽了个寂寞。
   而「今天 73/100」是**确定的进度条**，比概率更有粘性。
   （详见设计师文档《天道令兑换与装备收尾方案》§1.2）
"""
from __future__ import annotations

from typing import Any, Callable

# ---------------------------------------------------------------------------
# 成长被动注册表 —— 加新被动只加函数，不改主流程
# ---------------------------------------------------------------------------

# 每种被动：handler(ctx) -> 新的累积百分比
# ctx 里带 enough 上下文让 handler 自己决定怎么涨（不预设规则）。
PASSIVE_HANDLERS: dict[str, Callable[[dict], float]] = {}


def register_passive(kind: str):
    """注册一种成长被动的计算方式。

    handler 签名：`handler(ctx) -> float`
      ctx = {
        "spec": 特殊装备定义（dict）,
        "passive": 当前被动定义（dict）,
        "current": 当前累积百分比,
        "kills": 本次击杀数（可能是 0）,
        "died": 本次是否死亡,
      }
    返回**新的累积百分比**（不是增量）。
    """

    def deco(fn: Callable[[dict], float]):
        PASSIVE_HANDLERS[kind] = fn
        return fn

    return deco


@register_passive("grow_on_kill")
def _grow_on_kill(ctx: dict) -> float:
    """击杀累积型：每击杀一只妖兽 +pct%，封顶 cap_pct%。

    这是老板举的例子（「每击杀一只怪物加 1% 全属性」），
    但注意 —— **具体数值不写在这里**，来自数据表的 passive 字段。
    将来老板说「改成每次击杀 +2%，但渡劫失败归零」，只改数据即可。

    `lose_on_death=True` 时死亡清零（老板说「不掉（推荐）」，
    所以默认值是 False，但结构上留着，以后想开就开）。
    """
    passive = ctx["passive"]
    cur = float(ctx["current"])
    kills = int(ctx.get("kills", 0))
    per = float(passive.get("pct", 0.0) or 0.0)
    cap = float(passive.get("cap_pct", 0.0) or 0.0)

    if ctx.get("died") and passive.get("lose_on_death", False):
        return 0.0

    if kills <= 0 or per <= 0:
        return cur

    if str(passive.get("curve", "linear")) == "linear":
        new = cur + per * kills
    else:
        # 将来要加曲线（递减/递增/分段）时在这里分支，不动调用方
        new = cur + per * kills

    if cap > 0:
        new = min(new, cap)
    return new


# ---------------------------------------------------------------------------
# 特殊装备查询（薄封装，方便将来换数据源）
# ---------------------------------------------------------------------------

def all_specials() -> list[dict]:
    from . import items as _items
    return list(_items.SPECIAL_EQUIPMENTS)


def find_special(spec_id: str) -> dict | None:
    """按 id 找特殊装备。id 稳定，名字可改。"""
    if not spec_id:
        return None
    from . import items as _items
    return _items.SPECIAL_EQUIPMENT_BY_ID.get(spec_id)


def special_display_name(spec: dict) -> str:
    """展示名。

    【v3.5 改动】去掉了原来的「（xxx 专属）」拼接。
    帝兵已**去唯一化**（老板：「不搞唯一了，全民可获取」），
    `unique` 恒为 False，那段逻辑永远不会触发 —— 留着只是死代码。
    `owner` 字段在数据表里保留（结构无害），但展示层不再读它。
    """
    return str(spec.get("name", "特殊法宝"))


# ---------------------------------------------------------------------------
# 成长被动推进
# ---------------------------------------------------------------------------

def advance_passives(player, *, kills: int = 0, died: bool = False) -> list[str]:
    """按本次战斗结果推进玩家的特殊装备成长被动，返回给玩家看的提示行。

    【为什么放在这里而不是 battle.py】
    战斗引擎只负责「打」，成长结算属于**装备系统**的事。
    分开放的好处：以后加别的被动（比如「每天首次登录 +2%」）
    完全不碰战斗代码，而且不影响战斗的纯粹性（便于数值采样）。

    【无帝兵时是纯 no-op】
    不装特殊栏的玩家走这里什么都不会发生，零开销。
    """
    spec_id = getattr(player, "special", "") or ""
    if not spec_id:
        return []
    spec = find_special(spec_id)
    if not spec:
        return []

    cur = float(getattr(player, "special_growth_pct", 0.0) or 0.0)
    lines: list[str] = []

    for passive in spec.get("passives", []) or []:
        kind = str(passive.get("kind", ""))
        handler = PASSIVE_HANDLERS.get(kind)
        if handler is None:
            # 数据里写了未注册的被动 —— 不要静默吞掉，提示出来
            lines.append(f"（{spec.get('name')}：未知被动 {kind}，已跳过）")
            continue

        new = handler({
            "spec": spec, "passive": passive, "current": cur,
            "kills": kills, "died": died,
        })
        if abs(new - cur) > 1e-9:
            gain = new - cur
            lines.append(
                f"{spec.get('name')}（{special_passive_label(kind)}）"
                f" 累积 +{gain:.1f}%（当前共 +{new:.1f}%）"
            )
            cur = new

    player.special_growth_pct = cur
    return lines


def special_passive_label(kind: str) -> str:
    return {
        "grow_on_kill": "弑杀成长",
    }.get(kind, kind)


def growth_progress(player) -> tuple[float, float]:
    """返回 (当前累积, 封顶值)。没帝兵 / 无封顶返回 (0, 0)。"""
    spec = find_special(getattr(player, "special", "") or "")
    if not spec:
        return 0.0, 0.0
    cur = float(getattr(player, "special_growth_pct", 0.0) or 0.0)
    cap = 0.0
    for passive in spec.get("passives", []) or []:
        c = float(passive.get("cap_pct", 0.0) or 0.0)
        if c > cap:
            cap = c
    return cur, cap


# ---------------------------------------------------------------------------
# 【特殊】栏渲染
# ---------------------------------------------------------------------------

def render_special_slot(player) -> str | None:
    """渲染【特殊】栏。**没帝兵时返回 None**（上层就不显示这一栏）。

    老板要求：「无帝兵装备时不显示【特殊】栏」。
    """
    spec_id = getattr(player, "special", "") or ""
    if not spec_id:
        return None
    spec = find_special(spec_id)
    if not spec:
        return None

    lines = [f"【特殊】{special_display_name(spec)}"]
    base = float(spec.get("base_pct", 0.0) or 0.0)
    cur, cap = growth_progress(player)
    total = base + cur

    if cur:
        lines.append(
            f"　全属性 +{total:.1f}%　（基础 {base:.1f}% + 成长 {cur:.1f}%）"
        )
    else:
        lines.append(f"　全属性 +{total:.1f}%")

    for passive in spec.get("passives", []) or []:
        kind = str(passive.get("kind", ""))
        per = float(passive.get("pct", 0.0) or 0.0)
        label = special_passive_label(kind)
        if cap > 0:
            lines.append(f"　{label}：每击杀 +{per:.1f}%，封顶 +{cap:.1f}%（当前 {cur:.1f}%）")
        else:
            lines.append(f"　{label}：每击杀 +{per:.1f}%（当前 {cur:.1f}%）")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 天道令兑换（v3.5）
# ---------------------------------------------------------------------------
#
# 【为什么换掉了抽奖】
# v3.4 这里放的是 lottery_pool() / roll_special()。老板 v3.5 拍板废弃，
# 理由见模块头注释第 5 条。一句话：**确定性 > 概率**。
#
# 【这一节的形状】
#   token_count()      玩家现在有几枚
#   grant_token()      发令（签到 / 秘境首通共用）
#   owns_special()     是否曾兑换过（读 player.special_owned）
#   exchange_pool()    可兑换清单
#   can_redeem()       校验 → (能否, 原因)
#   redeem()           执行 → (成功, 消息, 定义)
#   redeem_preview()   渲染面板（进度条 + 清单）
#
# 【防重复兑换为什么必须靠 special_owned 而不是 player.special】
# `/特殊 卸下` 会清空 player.special。若只看它，玩家
# 「卸下 -> 再兑换」就会被**白扣 100 枚**。所以兑换流水单独存一份，
# 且**只增不减**（见 player.Player.special_owned 的注释）。

TOKEN_NAME = "天道令"
TOKEN_COST = 100        # 老板定的：100 枚换一件极品帝兵
TOKEN_FROM_SIGNIN = 1   # 每日签到给 1 枚
TOKEN_FROM_DUNGEON = 1  # 单次秘境**有首通**给 1 枚（按整趟发，不是按层）


def token_count(player) -> int:
    """玩家现有天道令数量（自动匹配品质后缀，走 find_inventory_name）。"""
    if player is None:
        return 0
    try:
        name = player.find_inventory_name(TOKEN_NAME)
    except AttributeError:  # pragma: no cover - 非 Player 对象的兜底
        name = None
    if name is None:
        return 0
    try:
        return int(player.inventory.get(name, 0) or 0)
    except (TypeError, ValueError):
        return 0


def grant_token(player, count: int = 1, limit: int | None = None) -> int:
    """发天道令，返回实际到手的数量（0 表示没发）。

    【为什么入包失败要强制写进去】
    天道令是**进度型道具**。如果因为「背包满」就吞掉一次产出，
    玩家会白等一天，而且**根本不知道漏了** —— 这种静默损失最伤人。
    所以入包失败时直接写 inventory（最多突破 1 格上限，
    卖掉点东西就恢复），比丢进度好得多。
    """
    if player is None:
        return 0
    try:
        n = int(count)
    except (TypeError, ValueError):
        return 0
    if n <= 0:
        return 0
    try:
        if player.add_item(TOKEN_NAME, n, limit=limit):
            return n
    except AttributeError:  # pragma: no cover
        return 0
    # 背包满 —— 强制入包
    cur = 0
    try:
        cur = int(player.inventory.get(TOKEN_NAME, 0) or 0)
    except (TypeError, ValueError):
        cur = 0
    player.inventory[TOKEN_NAME] = cur + n
    return n


def owns_special(player, spec_id: str) -> bool:
    """是否**曾经兑换过**这件特殊装备（读 `special_owned`，只增不减）。

    注意它和 `player.special` 的区别：后者会被 `/特殊 卸下` 清空，
    前者是永久流水。兑换去重必须用前者。
    """
    if not spec_id or player is None:
        return False
    owned = getattr(player, "special_owned", None) or []
    if not isinstance(owned, (list, tuple, set)):
        return False
    return spec_id in owned


def exchange_pool() -> list[dict]:
    """当前可通过天道令兑换的特殊装备列表。

    与已废弃的 `lottery_pool()` 的关键区别：**不再过滤 owner** ——
    帝兵已去唯一化（人人可得），「被别人拿了就没了」的语义不存在了。
    """
    return [
        s for s in all_specials()
        if str(s.get("source", "")) in ("token_exchange", "any")
    ]


def can_redeem(player, spec_id: str) -> tuple[bool, str]:
    """能否兑换 → `(是否可兑换, 原因/提示语)`。

    四条校验，顺序是「先便宜的先查」：查表 O(1) -> 查流水 -> 查数量。
    任何一条不过都**不会扣令**（扣款在 redeem 里，且有二次确认）。
    """
    spec = find_special(spec_id)
    if spec is None:
        return False, "查无此物。发送 /特殊 列表 查看可兑换的帝兵。"
    if str(spec.get("source", "")) not in ("token_exchange", "any"):
        return False, f"{special_display_name(spec)} 不是天道令兑换所得。"
    if owns_special(player, spec_id):
        return False, f"你已兑换过 {special_display_name(spec)}，无需重复兑换。"
    have = token_count(player)
    if have < TOKEN_COST:
        return False, (
            f"天道令不足：现有 {have} / {TOKEN_COST}，"
            f"还差 {TOKEN_COST - have} 枚。"
        )
    return True, ""


def redeem(player, spec_id: str) -> tuple[bool, str, dict | None]:
    """执行兑换 → `(是否成功, 给玩家看的话, 装备定义)`。

    【扣款顺序：先校验 → 再扣令 → 最后写归属】
    这个顺序保证任何一步失败都不会出现「令扣了但装备没到手」。
    反过来（先写归属再扣令）在扣令失败时就会白送一件帝兵。
    """
    ok, why = can_redeem(player, spec_id)
    if not ok:
        return False, why, None

    spec = find_special(spec_id)
    if spec is None:  # pragma: no cover - can_redeem 已经查过
        return False, "查无此物。", None

    # 1) 扣令（此时已确认数量足够；失败只可能是脏数据）
    if not player.consume_item(TOKEN_NAME, TOKEN_COST):
        return False, "扣除天道令失败，未扣除任何天道令，请稍后再试。", None

    # 2) 记流水（只增不减，用于去重）
    owned = getattr(player, "special_owned", None)
    if not isinstance(owned, list):
        owned = []
        player.special_owned = owned
    if spec_id not in owned:
        owned.append(spec_id)

    # 3) 直接认主
    #    帝兵**不在 EQUIPMENTS 表里**，背包 / `/装备` 那套 5 部位机制
    #    认不出它，走 `/特殊 认主` 才是它的入口。兑换后直接写 special，
    #    玩家不用再多敲一条指令。
    player.special = spec_id

    return (
        True,
        f"天道令 -{TOKEN_COST}，{special_display_name(spec)} 认你为主！",
        spec,
    )


def redeem_preview(player) -> str:
    """渲染兑换面板：进度条 + 可兑换清单 + 每条还差多少。

    这就是「确定性比概率更有粘性」的落点 ——
    玩家每天能看见一个会涨的数字（今天 73/100）。
    """
    have = token_count(player)
    ratio = (have / TOKEN_COST) if TOKEN_COST else 0.0
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(ratio * 20))
    bar = "█" * filled + "·" * (20 - filled)

    lines = [
        "⚜️ 天道兑换",
        f"天道令：{have} / {TOKEN_COST}",
        f"　[{bar}]",
        "",
    ]

    pool = exchange_pool()
    if not pool:
        lines.append("　天道之上暂无可求之兵。")

    for spec in pool:
        sid = str(spec.get("id", ""))
        name = special_display_name(spec)
        base = float(spec.get("base_pct", 0.0) or 0.0)
        lines.append(f"　{name}（{sid}）　全属性 +{base:.1f}%")
        for passive in spec.get("passives", []) or []:
            kind = str(passive.get("kind", ""))
            per = float(passive.get("pct", 0.0) or 0.0)
            cap = float(passive.get("cap_pct", 0.0) or 0.0)
            label = special_passive_label(kind)
            if cap > 0:
                lines.append(f"　　{label}：每击杀 +{per:.1f}%，封顶 +{cap:.1f}%")
            else:
                lines.append(f"　　{label}：每击杀 +{per:.1f}%")
        if owns_special(player, sid):
            lines.append("　　→ 已兑换（可在 /特殊 认主 重新佩戴）")
        elif have >= TOKEN_COST:
            lines.append("　　→ 可以兑换")
        else:
            lines.append(f"　　→ 还差 {TOKEN_COST - have} 枚天道令")

    lines.append("")
    lines.append(
        "发送 /兑换 <id> 兑换。天道令来自：每日 /签到 +1 枚、秘境首通 +1 枚。"
    )
    return "\n".join(lines)
