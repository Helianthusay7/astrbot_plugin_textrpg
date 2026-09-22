"""激活状态管理：记录哪些群已被主人激活。

为什么单独存一份而不是塞进 players.json：
  - 玩家存档是「游戏数据」，激活状态是「管理数据」，
    混在一起会让存档结构和清档逻辑都变复杂。
  - 玩法上可能有「重置本群存档」的需求，那时不该把激活状态一起清掉，
    否则重置完机器人就不理这个群了，用户会一头雾水。

存储格式（activated_groups.json）：
{
  "groups": {
    "123456789": {"by": "3455845639", "at": 1758211200.0, "name": "某群"}
  }
}
用 dict 而不是 list，是为了记下「谁激活的、什么时候」，便于排查。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

FILENAME = "activated_groups.json"


class ActivationStore:
    """已激活群列表的持久化。

    并发保护与玩家存档一致：模块级 asyncio.Lock 串行化写入，
    阻塞 IO 丢到线程池，避免卡住事件循环。
    """

    _lock = asyncio.Lock()

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / FILENAME
        self._cache: dict[str, dict] | None = None

    # ------------------------------------------------------------------
    # 同步读写（内部）
    # ------------------------------------------------------------------

    def _load_sync(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 文件损坏时当作空，不让插件起不来
            return {}
        groups = raw.get("groups")
        return groups if isinstance(groups, dict) else {}

    def _write_sync(self, groups: dict[str, dict]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {"groups": groups}
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)

    def _ensure(self) -> dict[str, dict]:
        if self._cache is None:
            self._cache = self._load_sync()
        return self._cache

    # ------------------------------------------------------------------
    # 业务接口
    # ------------------------------------------------------------------

    def is_activated(self, group_id: str) -> bool:
        if not group_id:
            return False
        return str(group_id) in self._ensure()

    def activated_groups(self) -> list[str]:
        return sorted(self._ensure().keys())

    def info(self, group_id: str) -> dict | None:
        return self._ensure().get(str(group_id))

    async def activate(self, group_id: str, by: str = "",
                       name: str = "") -> bool:
        """激活一个群。返回 True 表示本次是新激活，False 表示本来就激活过。"""
        import time

        if not group_id:
            return False
        async with self._lock:
            groups = self._ensure()
            key = str(group_id)
            if key in groups:
                return False
            groups[key] = {
                "by": str(by),
                "at": time.time(),
                "name": name,
            }
            await asyncio.to_thread(self._write_sync, dict(groups))
            return True

    async def deactivate(self, group_id: str) -> bool:
        """取消激活。返回 True 表示确实删掉了一条。"""
        if not group_id:
            return False
        async with self._lock:
            groups = self._ensure()
            key = str(group_id)
            if key not in groups:
                return False
            groups.pop(key)
            await asyncio.to_thread(self._write_sync, dict(groups))
            return True

    async def clear(self) -> int:
        async with self._lock:
            groups = self._ensure()
            count = len(groups)
            groups.clear()
            await asyncio.to_thread(self._write_sync, {})
            return count
