"""真实存档迁移验证：在**副本**上跑，绝不碰线上数据。

【为什么必须做这一步】
线上 `data/plugin_data/astrbot_plugin_textrpg/players.json` 里是
**真实玩家数据**。v3.4 改了物品名体系（十品装备）和品质后缀格式，
`from_dict()` 的迁移逻辑会**就地改写**这些数据。

迁移一旦出错，玩家背包就废了 —— 而且这类错误不会崩，
只会「物品变成未知道具」这种静默损坏。

所以规矩是：
  1. 先备份
  2. 在副本上跑迁移
  3. 逐项核对迁移结果
  4. 确认无误才让线上跑

用法（用 AstrBot 的 venv，走真实 astrbot）：
    cd D:/ai/AstrBot
    ./.venv/Scripts/python.exe <此脚本>
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ASTRBOT_ROOT = Path("D:/ai/AstrBot")
PLUGINS_DIR = ASTRBOT_ROOT / "data" / "plugins"
LIVE_DIR = ASTRBOT_ROOT / "data" / "plugin_data" / "astrbot_plugin_textrpg"
PLUGIN_NAME = "astrbot_plugin_textrpg"
COPY_DIR = PLUGINS_DIR / PLUGIN_NAME / "_testdata_migrate"

for _p in (ASTRBOT_ROOT, PLUGINS_DIR):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

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


def main() -> int:
    print("=" * 70)
    print(" 真实存档迁移验证（只读线上，改动落在副本）")
    print("=" * 70)

    live_save = LIVE_DIR / "players.json"
    if not live_save.exists():
        print(f"\n线上存档不存在：{live_save}")
        return 0

    raw_before = live_save.read_text(encoding="utf-8")
    data_before = json.loads(raw_before)

    print(f"\n[1] 线上存档快照（只读）")
    print(f"    路径：{live_save}")
    print(f"    大小：{len(raw_before)} 字节，玩家数 {len(data_before)}")
    for k, v in data_before.items():
        print(f"      {k}: lv={v.get('level')} "
              f"inv={list(v.get('inventory', {}).keys())} "
              f"eq={v.get('equipment')}")
    check("线上存档可读", True)

    # ---- 2. 备份 ----
    print("\n[2] 备份线上存档")
    backup = LIVE_DIR / "players.json.bak_pre_v34_migrate_check"
    if not backup.exists():
        shutil.copy2(live_save, backup)
        print(f"    已备份 -> {backup.name}")
    else:
        print(f"    备份已存在 -> {backup.name}")
    check("备份文件存在", backup.exists())

    # ---- 3. 副本上跑迁移 ----
    print("\n[3] 副本迁移")
    shutil.rmtree(COPY_DIR, ignore_errors=True)
    COPY_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(live_save, COPY_DIR / "players.json")

    from astrbot.core.star.star import StarMetadata, star_map

    from astrbot_plugin_textrpg import data as data_mod
    from astrbot_plugin_textrpg import items as items_mod

    star_map[f"{PLUGIN_NAME}.main"] = StarMetadata(
        name=PLUGIN_NAME, module_path=f"{PLUGIN_NAME}.main")

    store = data_mod.PlayerStore(COPY_DIR)
    store.ensure_loaded()
    check("副本可加载", store.count == len(data_before),
          f"{store.count} vs {len(data_before)}")

    # ---- 4. 逐项核对 ----
    print("\n[4] 迁移结果核对")
    known = set(items_mod.all_item_names())
    # all_item_names 返回基础名，带品质后缀的要靠 get_item 解析
    ok_all = True
    for key, payload in data_before.items():
        # store._cache 的键就是 "群:QQ"，与存档键一致，直接取
        p = store._cache.get(key)
        if p is None:
            check(f"{key} 迁移后仍可读", False)
            ok_all = False
            continue
        print(f"    {key}:")
        print(f"      背包 -> {p.inventory}")
        print(f"      装备 -> {p.equipment}")
        print(f"      特殊 -> {p.special!r}  成长 {p.special_growth_pct}")

        # 背包里每一项都必须能查到定义（这是迁移正确性的硬标准）
        for nm in p.inventory:
            item = items_mod.get_item(nm)
            if item is None:
                check(f"背包物品「{nm}」能查到定义", False,
                      "迁移后名字无效 —— 玩家会看到未知道具")
                ok_all = False
        # 装备名同理
        for slot, nm in p.equipment.items():
            if not nm:
                continue
            if items_mod.get_item(nm) is None:
                check(f"装备「{nm}」能查到定义", False)
                ok_all = False
            if slot not in ("weapon", "armor", "accessory", "boots",
                            "talisman"):
                check(f"部位 key「{slot}」合法", False)
                ok_all = False
    check("迁移后所有物品名都能查到定义", ok_all)

    # ---- 5. 幂等性 ----
    print("\n[5] 幂等性（再读一次不应变化）")
    store2 = data_mod.PlayerStore(COPY_DIR)
    store2.ensure_loaded()
    same = True
    for key in store._cache:
        a, b = store._cache[key], store2._cache.get(key)
        if b is None or a.inventory != b.inventory or a.equipment != b.equipment:
            same = False
            print(f"      {key} 不一致：")
            print(f"        一次 {a.inventory} / {a.equipment}")
            if b:
                print(f"        二次 {b.inventory} / {b.equipment}")
    check("二次迁移结果不变（幂等）", same)

    # ---- 6. 线上存档未被改动 ----
    print("\n[6] 线上存档未被改动")
    raw_after = live_save.read_text(encoding="utf-8")
    check("线上 players.json 字节级未变", raw_after == raw_before,
          "迁移验证脚本动了线上数据！")

    shutil.rmtree(COPY_DIR, ignore_errors=True)
    print("\n" + "=" * 70)
    print(f"结果：{PASS} 通过，{FAIL} 失败")
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
