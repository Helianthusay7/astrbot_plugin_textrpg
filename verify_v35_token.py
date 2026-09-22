"""v3.5 天道令 / 兑换 / 里程碑 端到端验证。

【为什么单独一个脚本，而不是塞进 selftest.py】
selftest 是**回归网**（跑得快、只验不变式），而这里要验的是
「一条完整链路走通」：签到 -> 攒令 -> 兑换 -> 认主 -> 拦截。
链路测试依赖真实指令入口（cmd_sign_in / cmd_redeem / dungeon），
和 selftest 的断言粒度不同，混在一起会让 selftest 变慢变脆。

【怎么跑】
    D:/ai/AstrBot/.venv/Scripts/python.exe verify_v35_token.py

复用 selftest.py 的桩（astrbot.api 等），所以必须在同目录下运行。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 复用 selftest 的桩与工具函数。
# selftest 底部的 asyncio.run(main()) 在 __main__ 保护下，import 不会触发。
import selftest as ST  # noqa: E402

importlib = ST.importlib
plugin_mod = ST.plugin_mod
battle_mod = ST.battle_mod
dungeon_mod = ST.dungeon_mod
items_mod = ST.items_mod
player_mod = ST.player_mod
special_mod = importlib.import_module(f"{HERE.name}.special")
AstrMessageEvent = ST.AstrMessageEvent

PASS = 0
FAIL = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label}  {extra}")


async def main() -> int:
    print("=" * 60)
    print("v3.5 天道令 / 兑换 / 里程碑 端到端验证")
    print("=" * 60)

    plugin = plugin_mod.TextRPGPlugin(context=None, config={
        "group_whitelist_mode": "all",
        "reply_delay_range": [0, 0],
        "rate_limit_count": 10000,
    })
    plugin.item_db = {}
    ev = AstrMessageEvent(qq="777501", name="天道令", group="998")
    await ST.fast_create(plugin, ev, "天道令", level=5)
    p = plugin.store.get("998", "777501")

    # ---------------------------------------------------------------
    print("\n[1] /签到")
    # ---------------------------------------------------------------
    out = await ST.run_cmd(plugin, "cmd_sign_in", ev)
    check("签到成功", "签到成功" in out, out[:200])
    check("签到发 1 枚天道令", special_mod.token_count(p) == 1,
          str(special_mod.token_count(p)))
    check("签到写入日期", p.sign_in_date == dungeon_mod.today_str(),
          repr(p.sign_in_date))
    check("签到提示进度条", "天道令：1 / 100" in out, out[:300])

    out2 = await ST.run_cmd(plugin, "cmd_sign_in", ev)
    check("同日重复签到被拦", "今日已签到" in out2, out2[:200])
    check("重复签到不再发令", special_mod.token_count(p) == 1,
          str(special_mod.token_count(p)))

    # 跨零点（换个日期）应能再签 —— 这是「日期字符串」而非时间戳的意义
    p.sign_in_date = "2000-01-01"
    out3 = await ST.run_cmd(plugin, "cmd_sign_in", ev)
    check("跨天后可再签到", "签到成功" in out3, out3[:200])
    check("再签到累加到 2 枚", special_mod.token_count(p) == 2,
          str(special_mod.token_count(p)))

    # ---------------------------------------------------------------
    print("\n[2] /特殊 列表（兑换面板）")
    # ---------------------------------------------------------------
    out = await ST.run_cmd(plugin, "cmd_special", ev, "列表")
    check("面板标题是天道兑换", "天道兑换" in out, out[:200])
    check("面板显示 2 / 100", "2 / 100" in out, out[:300])
    check("面板列出帝兵 id", "dibing_000" in out, out[:400])
    check("面板给出还差多少", "还差 98 枚" in out, out[:500])
    check("面板说明产出途径", "/签到" in out and "首通" in out, out[-200:])

    # ---------------------------------------------------------------
    print("\n[3] 未兑换时 /特殊 认主 应被拦")
    # ---------------------------------------------------------------
    out = await ST.run_cmd(plugin, "cmd_special", ev, "认主 dibing_000")
    check("未兑换不能白认主", "尚未兑换" in out, out[:300])
    check("被拦后 special 仍为空", p.special == "", repr(p.special))

    # ---------------------------------------------------------------
    print("\n[4] /兑换 —— 数量不足")
    # ---------------------------------------------------------------
    out = await ST.run_cmd(plugin, "cmd_redeem", ev, "dibing_000")
    check("不足 100 枚被拒", "天道令不足" in out, out[:200])
    check("被拒后不扣令", special_mod.token_count(p) == 2,
          str(special_mod.token_count(p)))

    out = await ST.run_cmd(plugin, "cmd_redeem", ev, "不存在")
    check("未知 id 被拒", "查无此物" in out, out[:200])

    # ---------------------------------------------------------------
    print("\n[5] 秘境首通发天道令（按整趟，不是按层）")
    # ---------------------------------------------------------------
    #
    # 【为什么这里要替换战斗、还要压层数上限】
    # 真实战斗有随机性：5 转角色运气差时第 1 层就败 -> first_clears = 0
    # -> 不发令，断言随机失败（实测 3 次里挂 1 次）。
    # 测「发令口径」这件事不该依赖数值平衡，所以：
    #   * 战斗换成必胜（隔离随机）
    #   * 单次层数上限压到 3（让「重刷」这一侧可构造）
    # 但**仍然走 /秘境 指令入口**，这样 _format_dungeon 和 mark_ran 也被覆盖到。
    _real_fight5 = battle_mod.fight
    _real_max5 = dungeon_mod.DUNGEON_MAX_LAYERS_PER_RUN

    def _always_win5(player, monster, db, **kw):
        return battle_mod.BattleResult(
            outcome=battle_mod.OUTCOME_WIN, monster_name=getattr(monster, "name", "x")
        )

    dungeon_mod.DUNGEON_MAX_LAYERS_PER_RUN = 3
    battle_mod.fight = _always_win5
    try:
        before = special_mod.token_count(p)
        p.dungeon_date = ""
        p.dungeon_best = 0
        dg_out = await ST.run_cmd(plugin, "dungeon", ev)
        after = special_mod.token_count(p)
    finally:
        battle_mod.fight = _real_fight5
        dungeon_mod.DUNGEON_MAX_LAYERS_PER_RUN = _real_max5

    check("秘境确实推进了纪录", p.dungeon_best == 3, str(p.dungeon_best))
    check("整趟只发 1 枚（不是按层发）", after - before == 1,
          f"{before} -> {after}（首通 {p.dungeon_best} 层）")
    check("战果里展示天道令", "天道令 +1" in dg_out, dg_out[:400])

    # 重刷（起止层全落在旧纪录之内）不给令
    dungeon_mod.DUNGEON_MAX_LAYERS_PER_RUN = 3
    battle_mod.fight = _always_win5
    try:
        before2 = special_mod.token_count(p)
        p.dungeon_date = ""
        await ST.run_cmd(plugin, "dungeon", ev)
        after2 = special_mod.token_count(p)
    finally:
        battle_mod.fight = _real_fight5
        dungeon_mod.DUNGEON_MAX_LAYERS_PER_RUN = _real_max5
    check("重刷不破纪录则不发令", after2 == before2, f"{before2} -> {after2}")

    # ---------------------------------------------------------------
    print("\n[6] 凑满 100 枚 -> 兑换 -> 认主")
    # ---------------------------------------------------------------
    special_mod.grant_token(p, 100 - special_mod.token_count(p), limit=None)
    check("凑满 100 枚", special_mod.token_count(p) == 100,
          str(special_mod.token_count(p)))

    out = await ST.run_cmd(plugin, "cmd_redeem", ev, "dibing_000")
    check("兑换成功", "认你为主" in out, out[:300])
    check("扣掉 100 枚", special_mod.token_count(p) == 0,
          str(special_mod.token_count(p)))
    check("写入 special", p.special == "dibing_000", repr(p.special))
    check("写入兑换流水", "dibing_000" in p.special_owned, str(p.special_owned))
    check("回复带【特殊】栏", "【特殊】" in out, out[:400])

    out = await ST.run_cmd(plugin, "cmd_redeem", ev, "dibing_000")
    check("重复兑换被拒", "已兑换过" in out, out[:200])

    # ---------------------------------------------------------------
    print("\n[7] 卸下后仍不能重复兑换（防白扣 100 枚）")
    # ---------------------------------------------------------------
    await ST.run_cmd(plugin, "cmd_special", ev, "卸下")
    check("卸下清空 special", p.special == "", repr(p.special))
    check("卸下保留成长进度字段", hasattr(p, "special_growth_pct"))
    special_mod.grant_token(p, 100, limit=None)
    out = await ST.run_cmd(plugin, "cmd_redeem", ev, "dibing_000")
    check("卸下后重复兑换仍被拒", "已兑换过" in out, out[:200])
    check("100 枚没被白扣", special_mod.token_count(p) == 100,
          str(special_mod.token_count(p)))

    out = await ST.run_cmd(plugin, "cmd_special", ev, "认主 dibing_000")
    check("已兑换过的可重新认主", "认你为主" in out, out[:300])
    check("重新认主生效", p.special == "dibing_000", repr(p.special))

    # ---------------------------------------------------------------
    print("\n[8] /使用 与 /出售 必须拦下天道令")
    # ---------------------------------------------------------------
    out = await ST.run_cmd(plugin, "use_item", ev, "天道令")
    check("天道令不能被服用", "不能服用" in out, out[:200])
    check("服用失败后数量不变", special_mod.token_count(p) == 100,
          str(special_mod.token_count(p)))

    gold_before = p.gold
    out = await ST.run_cmd(plugin, "sell", ev, "天道令")
    check("天道令不能被出售", "不能出售" in out, out[:200])
    check("出售失败后数量不变", special_mod.token_count(p) == 100,
          str(special_mod.token_count(p)))
    check("出售失败后灵石不变", p.gold == gold_before,
          f"{gold_before} -> {p.gold}")
    check("天道令数据层价格 = 0（第二道保险）",
          float((items_mod.get_item("天道令") or {}).get("price", -1)) == 0.0,
          str((items_mod.get_item("天道令") or {}).get("price")))

    # ---------------------------------------------------------------
    print("\n[9] 秘境里程碑：首通 30 / 35 / 40 层给七 / 八 / 九品")
    # ---------------------------------------------------------------
    # 用「战斗必胜」替换真实战斗，隔离里程碑逻辑 ——
    # 否则这段测试就变成在测数值平衡，而不是测里程碑代码。
    _real_fight = battle_mod.fight

    def _always_win(player, monster, db, **kw):
        return battle_mod.BattleResult(
            outcome=battle_mod.OUTCOME_WIN, monster_name=getattr(monster, "name", "x")
        )

    battle_mod.fight = _always_win
    try:
        p2 = player_mod.Player(qq="777502", name="里程碑")
        p2.dungeon_best = 25          # 从 21 层起闯，25 -> 40 全是首通
        p2.dungeon_date = ""
        res = dungeon_mod.run_dungeon(p2, {}, None)
    finally:
        battle_mod.fight = _real_fight

    check("里程碑表 = {30:7, 35:8, 40:9}",
          dungeon_mod.DUNGEON_TIER_MILESTONES == {30: 7, 35: 8, 40: 9},
          str(dungeon_mod.DUNGEON_TIER_MILESTONES))
    check("拿到 3 件里程碑装备", len(res.milestone_items) == 3,
          str(res.milestone_items))
    tiers = sorted(
        int(items_mod.EQUIPMENT_BY_NAME[n]["tier_rank"])
        for n in res.milestone_items if n in items_mod.EQUIPMENT_BY_NAME
    )
    check("品阶正好是 7 / 8 / 9", tiers == [7, 8, 9], str(tiers))
    check("三件都进了背包",
          all(p2.inventory.get(n, 0) >= 1 for n in res.milestone_items),
          str(p2.inventory))
    check("战报里标出里程碑", any("里程碑" in ln for ln in res.logs),
          str(res.logs[-5:]))
    check("整趟仍只发 1 枚天道令", res.tokens == 1, str(res.tokens))

    # 重刷同一批层不再给里程碑 / 天道令
    #
    # ⚠️ 这里必须把「单次层数上限」压到很小，否则整趟 60 层会**继续破纪录**
    # （40 -> 95），那 first_clears > 0 就是对的，测出来的失败是假警报。
    # 正确构造是：起始层到上限**全部落在旧纪录之内**，才叫「纯重刷」。
    _real_max = dungeon_mod.DUNGEON_MAX_LAYERS_PER_RUN
    dungeon_mod.DUNGEON_MAX_LAYERS_PER_RUN = 2
    try:
        p2.dungeon_best = 100          # 起始层 = 96，两层 96/97 都 <= 100
        p2.dungeon_date = ""
        battle_mod.fight = _always_win
        try:
            res2 = dungeon_mod.run_dungeon(p2, {}, None)
        finally:
            battle_mod.fight = _real_fight
    finally:
        dungeon_mod.DUNGEON_MAX_LAYERS_PER_RUN = _real_max

    check("纯重刷：清 0 个首通层", res2.first_clears == 0, str(res2.first_clears))
    check("重刷不给里程碑装备", res2.milestone_items == [],
          str(res2.milestone_items))
    check("重刷不发天道令", res2.tokens == 0, str(res2.tokens))
    check("单次层数上限已还原",
          dungeon_mod.DUNGEON_MAX_LAYERS_PER_RUN == _real_max,
          str(dungeon_mod.DUNGEON_MAX_LAYERS_PER_RUN))

    # ---------------------------------------------------------------
    print("\n[10] 套系展示名（破锋 / 通玄 / 镇岳）")
    # ---------------------------------------------------------------
    check("物穿 -> 破锋", items_mod.set_display_name("物穿") == "破锋")
    check("法穿 -> 通玄", items_mod.set_display_name("法穿") == "通玄")
    check("肉盾 -> 镇岳", items_mod.set_display_name("肉盾") == "镇岳")
    check("未知 id 原样返回",
          items_mod.set_display_name("玄天") == "玄天")
    check("空 id 返回空串", items_mod.set_display_name("") == "")
    r = battle_mod.BattleResult(
        outcome=battle_mod.OUTCOME_WIN, monster_name="x",
        active_set="物穿", mech_hits={"crit": 2},
    )
    check("战斗小结用展示名", r.mech_summary().startswith("破锋套"),
          r.mech_summary())

    print("\n" + "=" * 60)
    print(f"结果：{PASS} 通过，{FAIL} 失败")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
