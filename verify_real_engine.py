"""真实引擎验证：用 AstrBot 自带的 astrbot 包加载插件。

【为什么需要这个脚本】
`selftest.py` 注入的是**假 astrbot**（自己写的 stub）。
stub 能验证游戏逻辑，但验证不了：

  * `from astrbot.api import AstrBotConfig, logger` 这些**真实导入路径**是否存在
  * `@filter.command("xxx")` 在**真实 filter** 下的行为（含别名注册）
  * `Star` 基类 / `Context` / `StarTools` 的**真实签名**
  * 有没有用到 stub 里恰好有、真实环境里没有的属性

stub 是自己写的，所以「stub 里有」这件事完全没有保证 ——
这正是 stub 测试的结构性盲区。

用法（必须在 AstrBot 的 venv 下跑）：
    cd D:/ai/AstrBot
    ./.venv/Scripts/python.exe <此脚本>

它会以 AstrBot 自己的方式（importlib + metadata.name）加载
data/plugins/ 下已部署的插件，然后检查指令注册情况。
"""
from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

PLUGINS_DIR = Path("D:/ai/AstrBot/data/plugins")
ASTRBOT_ROOT = Path("D:/ai/AstrBot")
PLUGIN_NAME = "astrbot_plugin_textrpg"

# ⚠️ 必须手动把 AstrBot 根目录塞进 sys.path。
# 跑脚本时 Python 放进 sys.path 的是**脚本所在目录**，不是 cwd ——
# 所以 `cd D:/ai/AstrBot && python 别的路径/x.py` 是找不到 astrbot 的。
# （用 `-c` 时才把 cwd 放进去，两者行为不同，容易误判。）
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
    print(" 真实 AstrBot 引擎验证")
    print("=" * 70)

    # ---- 1. 真实 astrbot 可用 ----
    try:
        import astrbot
        from astrbot.core.config import VERSION
        print(f"\n[1] 环境\n  真实 astrbot 版本：{VERSION}")
        check("真实 astrbot 已加载", True)
    except Exception as exc:
        print(f"\n[1] 环境\n  ✗ 无法导入真实 astrbot：{exc!r}")
        return 1

    # ---- 2. 关键 API 面存在 ----
    print("\n[2] API 面")
    try:
        from astrbot.api import AstrBotConfig, logger  # noqa: F401
        check("astrbot.api 提供 AstrBotConfig / logger", True)
    except Exception as exc:
        check("astrbot.api 提供 AstrBotConfig / logger", False, repr(exc))

    try:
        from astrbot.api.event import AstrMessageEvent, filter  # noqa: F401
        check("astrbot.api.event 提供 AstrMessageEvent / filter", True)
    except Exception as exc:
        check("astrbot.api.event 提供 AstrMessageEvent / filter", False, repr(exc))

    try:
        from astrbot.api.star import Context, Star, StarTools  # noqa: F401
        check("astrbot.api.star 提供 Context / Star / StarTools", True)
    except Exception as exc:
        check("astrbot.api.star 提供 Context / Star / StarTools", False, repr(exc))

    # 真实 filter 是否有我们用到的装饰器
    try:
        from astrbot.api.event import filter
        for attr in ("command", "command_group", "permission_type",
                     "event_message_type"):
            check(f"filter.{attr} 存在", hasattr(filter, attr))
    except Exception as exc:
        check("filter 可导入", False, repr(exc))

    # ---- 3. 按 AstrBot 的方式加载插件 ----
    print("\n[3] 加载插件（importlib + metadata.name）")
    plugin_dir = PLUGINS_DIR / PLUGIN_NAME
    check("插件目录存在", plugin_dir.is_dir(), str(plugin_dir))

    try:
        mod = importlib.import_module(f"{PLUGIN_NAME}.main")
        check("导入 main 模块成功", True)
    except Exception:
        check("导入 main 模块成功", False)
        traceback.print_exc()
        return 1

    # ---- 4. 子模块都能导入 ----
    print("\n[4] 子模块导入")
    for sub in ("battle", "player", "items", "realm", "scriptures",
                "dungeon", "data", "guard", "gate", "acl", "activation",
                "character", "menu", "special"):
        try:
            importlib.import_module(f"{PLUGIN_NAME}.{sub}")
            check(f"{sub} 导入成功", True)
        except Exception as exc:
            check(f"{sub} 导入成功", False, repr(exc))

    # ---- 5. 指令注册（走真实注册表）----
    #
    # ⚠️ 真实 `filter.command` **不往函数上挂属性**，而是把
    # `StarHandlerMetadata` 追加到全局 `star_handlers_registry`。
    # 所以「遍历类属性找 _cmd」这种做法在真实环境下探到 0 个 ——
    # 那是探针写错了，不是装饰器没生效。
    print("\n[5] 指令注册（真实 star_handlers_registry）")
    try:
        from astrbot.core.star.filter.command import CommandFilter
        from astrbot.core.star.star_handler import star_handlers_registry

        handlers = star_handlers_registry.get_handlers_by_module_name(
            f"{PLUGIN_NAME}.main"
        )
        print(f"    注册表里 {PLUGIN_NAME}.main 的 handler 数：{len(handlers)}")
        check("注册表里有 handler（说明 filter 装饰器生效）", len(handlers) > 0)

        # 把「方法名 -> 指令名(含别名)」摊平出来
        cmd_map: dict[str, list[str]] = {}
        for h in handlers:
            names: list[str] = []
            for f in getattr(h, "event_filters", []) or []:
                if isinstance(f, CommandFilter):
                    names.append(getattr(f, "command_name", "") or "")
                    names.extend(sorted(getattr(f, "alias", set()) or set()))
            if names:
                cmd_map.setdefault(h.handler_name, []).extend(
                    n for n in names if n
                )
        for m, cs in sorted(cmd_map.items()):
            print(f"      {m:<22} {'/'.join(cs)}")
        check("解析出指令名", len(cmd_map) > 0)

        # 核心指令必须在册
        for want, method in (("菜单", "cmd_menu"), ("简介", "cmd_intro"),
                             ("特殊", "cmd_special"), ("装备", "equip"),
                             ("历练", "hunt"), ("属性", "profile")):
            got = cmd_map.get(method, [])
            check(f"/{want} 已注册（{method}）", want in got, f"实际：{got}")

        # v3.6：挂机状态的四条指令 + 别名，必须在**真实注册表**里也在册。
        # 这一层是 stub 自测的结构性盲区 —— 本项目曾因多装饰器叠加
        # 让 24 个指令名在线上全死，而自测 827 项全绿。
        for want, method in (("闭关", "seclusion"),
                             ("结束闭关", "end_seclusion"),
                             ("出关", "end_seclusion"),
                             ("探索", "explore"),
                             ("结束探索", "end_explore"),
                             ("收工", "end_explore"),
                             ("结束游历", "end_explore")):
            got = cmd_map.get(method, [])
            check(f"/{want} 已注册（{method}）", want in got, f"实际：{got}")

        # ★ 一个 handler 挂多个 CommandFilter = AND 语义 = 该指令永远匹配不上。
        # selftest 的 AST 检查看的是**源码**，这里看的是**注册表里真挂上去的 filter**，
        # 两者互补：源码对但注册时合并错了，只有这一层能发现。
        _multi = {}
        for _h in handlers:
            _n = len([
                _f for _f in (getattr(_h, "event_filters", []) or [])
                if isinstance(_f, CommandFilter)
            ])
            if _n > 1:
                _multi[_h.handler_name] = _n
        check("没有 handler 挂多个 CommandFilter（AND 语义会让指令全死）",
              not _multi, f"违规：{_multi}")

        # ★ 指令路由：/结束闭关 只能命中 end_seclusion，/闭关 只能命中 seclusion。
        # 框架规则是 startswith(f"{cmd} ") or == cmd，所以「结束闭关」天然
        # 不会被「闭关」吞掉 —— 但这是**推理**，这里把它变成**实测**。
        def _cmd_filters(_method: str):
            _out = []
            for _h in handlers:
                if _h.handler_name != _method:
                    continue
                for _f in getattr(_h, "event_filters", []) or []:
                    if isinstance(_f, CommandFilter):
                        _out.append(_f)
            return _out

        for _msg, _hit, _miss in (
            ("结束闭关", "end_seclusion", "seclusion"),
            ("出关", "end_seclusion", "seclusion"),
            ("闭关", "seclusion", "end_seclusion"),
            ("结束探索", "end_explore", "explore"),
            ("收工", "end_explore", "explore"),
            ("结束游历", "end_explore", "explore"),
            ("探索", "explore", "end_explore"),
        ):
            _h_ok = any(_f.equals(_msg) for _f in _cmd_filters(_hit))
            _m_ok = any(_f.equals(_msg) for _f in _cmd_filters(_miss))
            check(f"「{_msg}」路由到 {_hit}、且不撞 {_miss}",
                  _h_ok and not _m_ok, f"hit={_h_ok} miss={_m_ok}")
    except Exception:
        check("指令注册检查", False)
        traceback.print_exc()

    # ---- 6. 实例化（真实 Star 基类）----
    print("\n[6] 实例化")
    try:
        # `StarTools.get_data_dir()` 靠 `star_map[调用方模块名]` 反查插件名。
        # 脱离 AstrBot 运行时直接 new 会报 "Unable to resolve plugin name" ——
        # 这是**验证脚本的局限**，不是插件 bug（真实加载时 AstrBot 会先注册）。
        # 这里手动补一次注册，把真实路径补齐。
        from astrbot.core.star.star import StarMetadata, star_map
        star_map[f"{PLUGIN_NAME}.main"] = StarMetadata(
            name=PLUGIN_NAME,
            author="老板",
            version="3.4.0",
            module_path=f"{PLUGIN_NAME}.main",
            star_cls_type=mod.TextRPGPlugin,
        )
        print("    已手动注册 StarMetadata（补齐 get_data_dir 的前置条件）")

        inst = mod.TextRPGPlugin(context=None)
        check("TextRPGPlugin(context=None) 可构造", inst is not None)
        check("实例有 item_db", hasattr(inst, "item_db"))
        check("item_db 非空", bool(getattr(inst, "item_db", {})))
        check("item_db 覆盖 135 件装备",
              len(getattr(inst, "item_db", {})) == 135,
              str(len(getattr(inst, "item_db", {}))))
        check("data_dir 已解析",
              getattr(inst, "data_dir", None) is not None,
              str(getattr(inst, "data_dir", None)))
        print(f"    data_dir = {getattr(inst, 'data_dir', None)}")
    except Exception:
        check("实例化", False)
        traceback.print_exc()

    print("\n" + "=" * 70)
    print(f"结果：{PASS} 通过，{FAIL} 失败")
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
