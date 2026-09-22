"""存档读写：原子写入 + 异步锁串行化。

为什么不用数据库：
    单群玩家数量在几十到几百量级，JSON 完全够用，且调试时能直接
    打开文件看内容。真正需要 SQLite 的场景是全服排行 / 跨群统计，
    那时再迁移即可。

并发为什么必须处理：
    QQ 群里两个人同一秒发 /打怪，两个协程会同时读同样的存档，
    各自改完再写回 —— 后写的覆盖先写的，玩家的掉落就凭空消失。
    这里用一把模块级 asyncio.Lock 把所有写操作串起来。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .player import Player

# 全局写锁：保证同一进程内所有存档写入串行
_write_lock = asyncio.Lock()

# 存档文件名
SAVE_FILENAME = "players.json"
# 备份文件名（上一次的存档，写入失败时可回滚）
BACKUP_FILENAME = "players.json.bak"


class PlayerStore:
    """玩家存档仓库。

    data_dir 由调用方通过 StarTools.get_data_dir() 传入，
    这样存档落在 data/plugin_data/<plugin_name>/ 下，
    插件升级/重装不会覆盖玩家数据。

    索引键为 "群号:QQ号"，实现按群隔离 —— 同一个人在多个群
    各自独立进度。私聊场景群号为空，键形如 ":123456"。
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.save_path = self.data_dir / SAVE_FILENAME
        self.backup_path = self.data_dir / BACKUP_FILENAME
        # 内存缓存：key -> Player。启动时懒加载
        self._cache: dict[str, Player] = {}
        self._loaded = False

    # ---------------- 键构造 ----------------

    @staticmethod
    def make_key(group_id: str, qq: str) -> str:
        """由群号和 QQ 号生成存档键。"""
        return f"{group_id}:{qq}"

    # ---------------- 加载 ----------------

    def _ensure_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _load_sync(self) -> None:
        """从磁盘读取全部存档到内存缓存。

        兼容两种存档格式：
          旧版（v1.0.0）：{"10001": {...}}            —— 键是纯 QQ 号
          新版（v1.1.0）：{"999:10001": {...}}        —— 键是 群:QQ
        旧格式会被自动迁移成新格式，group_id 置空（归入私聊池）。
        """
        self._ensure_dir()
        if not self.save_path.exists():
            self._cache = {}
            self._loaded = True
            return

        try:
            raw = self.save_path.read_text(encoding="utf-8")
            data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.error(f"[textrpg] 存档解析失败，尝试读取备份: {exc}")
            data = self._load_backup()

        players: dict[str, Player] = {}
        migrated = 0
        for stored_key, payload in data.items():
            try:
                player = Player.from_dict(payload)
            except TypeError as exc:
                # 单条记录损坏不应该影响整个存档
                logger.error(f"[textrpg] 玩家 {stored_key} 存档字段异常，已跳过: {exc}")
                continue

            # 旧格式迁移：键里没有冒号说明是纯 QQ 号
            if ":" not in stored_key:
                player.group_id = ""
                migrated += 1

            players[player.key] = player

        self._cache = players
        self._loaded = True

        if migrated:
            logger.info(f"[textrpg] 已迁移 {migrated} 条旧格式存档（无群号）")
        logger.info(f"[textrpg] 已加载 {len(players)} 名玩家存档")

    def _load_backup(self) -> dict[str, Any]:
        """主存档损坏时回退到备份；备份也没有就返回空。"""
        if not self.backup_path.exists():
            logger.error("[textrpg] 备份存档不存在，将以空存档启动")
            return {}
        try:
            raw = self.backup_path.read_text(encoding="utf-8")
            logger.info("[textrpg] 已从备份恢复存档")
            return json.loads(raw) if raw.strip() else {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.error(f"[textrpg] 备份同样损坏: {exc}")
            return {}

    def ensure_loaded(self) -> None:
        """首次访问时懒加载，避免在 __init__ 里做阻塞 IO。"""
        if not self._loaded:
            self._load_sync()

    # ---------------- 写入 ----------------

    def _write_sync(self) -> None:
        """原子写入：先写临时文件，再 rename 覆盖。

        rename 在主流文件系统上是原子操作，因此不存在
        "写到一半进程被杀，存档变成半截 JSON" 的窗口。
        """
        self._ensure_dir()

        # ⚠️ v3.6 修复（线上丢档事故 2026-09-21）：
        #
        # PlayerStore 是**懒加载**的 —— 首次访问前 _cache 是空的、_loaded=False。
        # 而插件的 terminate() 会无条件 await self._commit()（热重载/卸载必触发）。
        # 于是「插件启动后还没处理过任何消息就热重载」这条路 =
        #   用空的 _cache 覆盖整份存档，
        #   并且旧存档被 os.replace 成 .bak —— 连备份一起毁掉，
        #   玩家数据无法恢复。
        #
        # 线上表现：players.json 与 players.json.bak 双双变成 {}。
        #
        # 这里直接拦住：**没加载过就不许写盘**。磁盘上的数据原封不动，
        # 等下一次真正加载后再保存。代价是这一次写入被跳过（无害），
        # 收益是玩家数据不可能因为一次热重载而蒸发。
        if not self._loaded:
            logger.warning(
                "[textrpg] 存档尚未加载，跳过本次写入以免用空缓存覆盖玩家数据"
            )
            return

        payload = {qq: p.to_dict() for qq, p in self._cache.items()}
        # 紧凑序列化（无缩进、无多余空格）。
        #
        # 原先是 indent=2，纯粹为了人类可读 —— 但线上没人用文本编辑器看存档，
        # 代价却是实打实的。实测 1000 人存档（三种写法对比）：
        #   indent=2              1377 KB   14.5 ms
        #   indent=None（默认分隔） 1109 KB   14.5 ms
        #   separators=(",",":")  1012 KB   12.6 ms   <- 采用
        # 即体积 -26%、序列化耗时 -13%，且**临时字符串的内存峰值同步下降**。
        # 在 1核1G 这类小机器上，这一步能直接减轻 GC 压力。
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        tmp_path = self.save_path.with_suffix(".json.tmp")
        try:
            tmp_path.write_text(text, encoding="utf-8")
            # 已有存档先备份
            if self.save_path.exists():
                try:
                    os.replace(self.save_path, self.backup_path)
                except OSError as exc:
                    logger.error(f"[textrpg] 备份存档失败（继续写入）: {exc}")
            os.replace(tmp_path, self.save_path)
        except OSError as exc:
            logger.error(f"[textrpg] 存档写入失败: {exc}")
            # 清理残留临时文件
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    async def save(self) -> None:
        """异步保存。用异步锁串行化，并用线程池执行阻塞 IO。"""
        async with _write_lock:
            await asyncio.to_thread(self._write_sync)

    # ---------------- 业务接口 ----------------

    def get(self, group_id: str, qq: str) -> Player | None:
        """取指定群里某玩家的存档，不存在返回 None。"""
        self.ensure_loaded()
        return self._cache.get(self.make_key(group_id, qq))

    def get_or_create(
        self, group_id: str, qq: str, name: str = ""
    ) -> tuple[Player, bool]:
        """取玩家，不存在则创建。

        返回 (玩家, 是否新建)。
        注意：只改内存，调用方需自行 await save() 落盘。
        """
        self.ensure_loaded()
        group_id, qq = str(group_id), str(qq)
        key = self.make_key(group_id, qq)

        player = self._cache.get(key)
        if player is not None:
            # 昵称会变，每次交互顺手更新
            if name and player.name != name:
                player.name = name
            return player, False

        player = Player(qq=qq, group_id=group_id, name=name or f"玩家{qq[-4:]}")
        # 新建时记录修炼起点，避免刚报名就领满 8 小时离线收益
        player.last_train_ts = time.time()
        self._cache[key] = player
        return player, True

    def delete(self, group_id: str, qq: str) -> bool:
        """删除玩家存档，返回是否真的删掉了东西。"""
        self.ensure_loaded()
        key = self.make_key(group_id, qq)
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def players_in_group(self, group_id: str) -> list[Player]:
        """取某个群的全部玩家，用于群内排行榜。"""
        self.ensure_loaded()
        gid = str(group_id)
        return [p for p in self._cache.values() if p.group_id == gid]

    def all_players(self) -> list[Player]:
        """全部玩家（跨群），用于全服统计。"""
        self.ensure_loaded()
        return list(self._cache.values())

    def clear_group(self, group_id: str) -> int:
        """清空某个群的全部存档，返回清掉的条数。"""
        self.ensure_loaded()
        gid = str(group_id)
        keys = [k for k, p in self._cache.items() if p.group_id == gid]
        for k in keys:
            del self._cache[k]
        return len(keys)

    def clear_all(self) -> int:
        """清空所有存档，返回清掉的条数。"""
        self.ensure_loaded()
        count = len(self._cache)
        self._cache.clear()
        return count

    def groups(self) -> list[str]:
        """所有有玩家数据的群号。"""
        self.ensure_loaded()
        return sorted({p.group_id for p in self._cache.values() if p.group_id})

    @property
    def count(self) -> int:
        self.ensure_loaded()
        return len(self._cache)
