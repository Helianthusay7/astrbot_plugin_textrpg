"""指令闸门装饰器。

为什么不用 AstrBot 的 CustomFilter：
  查过 astrbot/core/star/filter/custom_filter.py，CustomFilter 确实存在，
  且 register_custom_filter 支持 raise_error=False 静默拦截。
  但它在不同小版本上的导出路径不稳定（astrbot.api.event 里没有直接导出），
  而我们真正需要的是「指令执行前」这个时机——用装饰器包一层更直接、
  不依赖框架内部结构，换个 AstrBot 版本也不会失效。

用法：
    @filter.command("打怪")
    @gate(plugin_attr="_gate_session")   # 这个指令受会话闸门保护
    async def battle_cmd(self, event):
        ...
"""

from __future__ import annotations

import functools
import inspect

from astrbot.api import logger


def _is_async_gen(fn) -> bool:
    return inspect.isasyncgenfunction(fn)


def gate_session(acl_getter):
    """会话闸门：群未激活则不执行指令体。

    acl_getter 是一个无参函数，返回 (allowed: bool, reason: str)。
    之所以传函数而不是直接传 ACL 对象，是因为装饰器在类定义时求值，
    那时还没有 self。
    """

    def decorator(fn):
        if not _is_async_gen(fn):
            # 指令方法必须是 async generator（要 yield 消息）
            return fn

        @functools.wraps(fn)
        async def wrapper(self, event, *args, **kwargs):
            allowed, reason = acl_getter(self, event)
            if not allowed:
                # 静默拦截：不回复、不执行。
                # 在陌生群里回一句「本群未启用」本身就是刷屏，会暴露机器人。
                group_id = ""
                try:
                    group_id = event.get_group_id() or ""
                except Exception:  # noqa: BLE001
                    group_id = ""
                logger.info(
                    f"[textrpg] 拦截未激活群 {group_id} 的指令：{reason}"
                )
                return
            async for item in fn(self, event, *args, **kwargs):
                yield item

        return wrapper

    return decorator


def gate_sensitive(acl_getter):
    """敏感指令闸门：仅主人（或群管理员）可执行。

    与 guard_session 不同，这里**要回复**拒绝原因——
    因为对方已经知道机器人的存在（它在群里激活了），
    回一句「仅限主人」是正常的权限提示，不算刷屏。
    """

    def decorator(fn):
        if not _is_async_gen(fn):
            return fn

        @functools.wraps(fn)
        async def wrapper(self, event, *args, **kwargs):
            allowed, reason = acl_getter(self, event)
            if not allowed:
                yield event.plain_result(reason)
                return
            async for item in fn(self, event, *args, **kwargs):
                yield item

        return wrapper

    return decorator
