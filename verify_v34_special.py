"""端到端验证：帝兵（特殊装备）成长在真实战斗流程里是否推进。

用真实插件对象 + 真实指令（/历练），不是纯函数调用。
目的是确认 `_apply_death -> advance_passives` 这条链路真的接上了，
而不是「单元测试过了但没接线」。

复用 selftest.py 的 astrbot 桩与 run_cmd，避免两处维护同一套桩。

【v3.5 更新】帝兵数值被**刻意下调**（62%→20% / 1%→0.1% / 150%→30%），
原因是帝兵**去唯一化**（人人可得）—— 原值是为「全服唯一」设计的，
全民化后 +212% 会把 135 件装备的梯度整个压平。
本脚本原本把这些数字**硬编码**在断言里，因此当场挂了 9 项。
**教训：数值类断言要从数据表读，不要抄写。** 现已改为从
`items.SPECIAL_EQUIPMENTS` 取值 —— 以后再调数值，这里自动跟上。

另外 v3.5 起 `/特殊 认主` 需要「已兑换过」才能认主，
所以本脚本在建号后先补一条兑换流水（等价于攒够 100 枚天道令换过）。

用法：
    python verify_v34_special.py
"""
from __future__ import annotations

import asyncio
import importlib
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

# 导入 selftest 会执行它模块级的 install_stubs()，把 astrbot 桩装好。
# 它不会跑测试（main() 只在 __main__ 下执行）。
_selftest = importlib.import_module(f"{HERE.name}.selftest")
run_cmd = _selftest.run_cmd
fast_create = _selftest.fast_create
AstrMessageEvent = _selftest.AstrMessageEvent

plugin_mod = _selftest.plugin_mod
special_mod = _selftest.plugin_mod  # 占位，下面重新取

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {extra}")


async def main() -> int:
    special_mod = importlib.import_module(f"{HERE.name}.special")
    items_mod = importlib.import_module(f"{HERE.name}.items")

    # 【不要硬编码数值】从数据表读，以后调数值这里自动跟上。
    _spec = items_mod.SPECIAL_EQUIPMENT_BY_ID["dibing_000"]
    BASE_PCT = float(_spec.get("base_pct", 0.0) or 0.0)
    _passive = (_spec.get("passives") or [{}])[0]
    PER_KILL = float(_passive.get("pct", 0.0) or 0.0)
    CAP_PCT = float(_passive.get("cap_pct", 0.0) or 0.0)
    print(f"（数据表：基础 {BASE_PCT}% / 每击杀 {PER_KILL}% / 封顶 {CAP_PCT}%）")
    items_mod = importlib.import_module(f"{HERE.name}.items")

    # 【不要硬编码数值】从数据表读，以后调数值这里自动跟上。
    _spec = items_mod.SPECIAL_EQUIPMENT_BY_ID["dibing_000"]
    BASE_PCT = float(_spec.get("base_pct", 0.0) or 0.0)
    _passive = (_spec.get("passives") or [{}])[0]
    PER_KILL = float(_passive.get("pct", 0.0) or 0.0)
    CAP_PCT = float(_passive.get("cap_pct", 0.0) or 0.0)
    print(f"（数据表：基础 {BASE_PCT}% / 每击杀 {PER_KILL}% / 封顶 {CAP_PCT}%）")

    data_dir = HERE / "_testdata_special"
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    # 用与 selftest 相同的构造方式：group_whitelist_mode="all" 绕过群白名单，
    # 关掉回复节流（否则每次 reply 都要等随机延迟，20 场战斗会非常慢）。
    plugin = plugin_mod.TextRPGPlugin(context=None, config={
        "reply_delay_range": [0, 0],
        "chunk_gap_range": [0, 0],
        "rate_limit_count": 10000,
        "group_whitelist_mode": "all",
    })
    plugin.data_dir = data_dir
    plugin.store = plugin_mod.PlayerStore(data_dir)
    plugin.store.ensure_loaded()

    ev = AstrMessageEvent(qq="555001", name="帝兵测试", group="999")
    await fast_create(plugin, ev, name="帝兵测试", level=40)
    p = plugin.store.get("999", "555001")
    check("建号成功", p is not None)
    if p is None:
        return 1

    print("\n[1] 无帝兵时打怪")
    before = p.special_growth_pct
    out = await run_cmd(plugin, "hunt", ev)
    check("战斗正常返回", "遭遇" in out, out[:200])
    check("无帝兵时成长不变", p.special_growth_pct == before,
          f"{before} -> {p.special_growth_pct}")
    check("无帝兵时不出现成长提示行", "☄️" not in out, out[-200:])

    print("\n[2] 认主帝兵")
    # v3.5：认主前必须「已兑换过」—— 补一条兑换流水
    out_deny = await run_cmd(plugin, "cmd_special", ev, "认主 dibing_000")
    check("未兑换时认主被拦（v3.5 新增校验）",
          "尚未兑换" in out_deny, out_deny[:200])
    p.special_owned = ["dibing_000"]
    out = await run_cmd(plugin, "cmd_special", ev, "认主 dibing_000")
    check("认主成功", "认你为主" in out, out[:200])
    check("special 字段已写入", p.special == "dibing_000", str(p.special))
    check("认主回复带出【特殊】栏", "【特殊】" in out, out[:400])

    print("\n[3] 带帝兵打怪 —— 成长必须推进")
    p.last_hunt_ts = 0.0
    p.rest_until = 0.0
    p.full_heal(plugin.item_db)
    await plugin._commit()
    before = p.special_growth_pct
    out = await run_cmd(plugin, "hunt", ev)
    if "✅" in out:
        check(f"击杀后成长 +{PER_KILL}%",
              abs(p.special_growth_pct - (before + PER_KILL)) < 1e-9,
              f"{before} -> {p.special_growth_pct}")
        check("战斗回复里出现成长提示", "☄️" in out, out[-300:])
    else:
        check("败北不涨成长", p.special_growth_pct == before,
              f"{before} -> {p.special_growth_pct}")

    print("\n[4] 高转数连续击杀累积")
    p.level = 72
    p.full_heal(plugin.item_db)
    p.special_growth_pct = 0.0
    kills = 0
    for _ in range(20):
        p.last_hunt_ts = 0.0
        p.rest_until = 0.0
        p.full_heal(plugin.item_db)
        out = await run_cmd(plugin, "hunt", ev)
        if "✅" in out:
            kills += 1
    check(f"连续 20 场后成长 = 击杀数 × {PER_KILL}%",
          abs(p.special_growth_pct - kills * PER_KILL) < 1e-9,
          f"击杀 {kills}，成长 {p.special_growth_pct}")
    check("至少赢了一场", kills > 0, str(kills))

    print("\n[5] 面板加成反映成长")
    p.special_growth_pct = 10.0
    expect = (BASE_PCT + 10.0) / 100.0
    check(f"面板加成 = (基础 {BASE_PCT} + 成长 10)%",
          abs(p.special_pct() - expect) < 1e-9, str(p.special_pct()))

    print("\n[6] 封顶保护")
    p.special_growth_pct = CAP_PCT - PER_KILL / 2   # 贴着封顶，再赢一次必超
    p.last_hunt_ts = 0.0
    p.rest_until = 0.0
    p.full_heal(plugin.item_db)
    out = await run_cmd(plugin, "hunt", ev)
    check(f"成长不会超过封顶 {CAP_PCT}%", p.special_growth_pct <= CAP_PCT,
          str(p.special_growth_pct))

    print("\n[7] 帝兵面板增益叠乘验证")
    p.special_growth_pct = 0.0
    base_atk = p.atk(plugin.item_db)
    p.special = ""
    naked_atk = p.atk(plugin.item_db)
    p.special = "dibing_000"
    check("帝兵让面板攻击提升",
          base_atk > naked_atk, f"{naked_atk} -> {base_atk}")
    ratio = base_atk / naked_atk if naked_atk else 0
    check(f"提升幅度 ≈ 1 + {BASE_PCT}%",
          abs(ratio - (1 + BASE_PCT / 100)) < 0.02, f"{ratio:.4f}")

    shutil.rmtree(data_dir, ignore_errors=True)
    print("\n" + "=" * 50)
    print(f"结果：{PASS} 通过，{FAIL} 失败")
    print("=" * 50)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
