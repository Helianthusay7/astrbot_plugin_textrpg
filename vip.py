"""VIP（赞助者）系统 —— 全局名单 + 天道令加成。

【两条硬约束（老板定的，改代码前务必读）】

1. **VIP 只能由主人（天道）发放。**
   没有自助购买、没有卡密、没有「消费满额自动升级」。
   发放权只在老板手上 —— 任何把发放权交出去的机制（卡密、
   自助激活）都与这条直接冲突。

2. **VIP 不碰战斗数值。**
   只加速「攒天道令 → 换帝兵」这一条线：**给时间，不给战力。**
   明确不做：加攻击 / 加防御 / 加掉率 / 减冷却 / 扩容背包。
   理由：一旦 VIP 能加战力，免费玩家就变成「陪玩」——
   10 元/月的小社区经不起这个，群里没人了，赞助也没了。

【为什么 VIP 数据必须单独存一份全局文件】

本插件的游戏存档是**按群隔离**的（键是 `群号:QQ号`）。
如果 VIP 写进群存档，会出现一个荒谬的结果：

    同一个玩家在 A 群充了 VIP，去 B 群玩就没权益了。

所以这里用独立的 `vip.json`，按 **QQ 号**索引，与群无关 —— 跨群有效。

    data/plugin_data/astrbot_plugin_textrpg/
    ├── players.json    ← 群隔离的游戏存档
    └── vip.json        ← 全局 VIP 名单（按 QQ 号）

【为什么加成跟着签到发，而不是「每天自动发」】

签到本来就有「一天一次」的保护（`sign_in_date` 日期字符串，跨零点重置）。
VIP 加成挂在这里就**自动继承**这层防刷 —— 一行防刷代码都不用新写。

「每天首次任意指令时发」要在 30+ 个指令入口都判断一次「今天发过没」，
改动面大，而且容易被玩家用 `/属性` 之类无成本指令刷。

代价：VIP 玩家某天不签到，当天加成拿不到。这是**可接受**的 ——
天道令本来就只有「签到 + 秘境首通」两个来源，
VIP 的定位是「加速签到」，不是「躺着收钱」。开通时要跟玩家说清这点。

【过期不删数据】

`vip_tier()` 对已过期的记录返回空串，但**记录本身保留**。原因：
  * 玩家续费时能延续 `since_ts` / `total_months`
  * 老板能查历史收入
  * 删了就找不回来了
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger

VIP_FILENAME = "vip.json"

TIER_NORMAL = "normal"
TIER_SUPREME = "supreme"

# 每档每天额外给几枚天道令（老板定的定价对应关系）
#   普通 VIP 10 元/月 → +1
#   至尊 VIP 30 元/月 → +3
# 3 倍价格 = 3 倍权益，定价结构自洽。
TIER_TOKEN_BONUS: dict[str, int] = {
    TIER_NORMAL: 1,
    TIER_SUPREME: 3,
}

# 每档定价（元/月）。放在代码里是为了让 `/vip` 能直接打出价目表，
# 不用老板每次去翻聊天记录。
TIER_PRICES: dict[str, int] = {
    TIER_NORMAL: 10,
    TIER_SUPREME: 30,
}

# 发放主入口的指令名（供帮助文案与文档引用，避免多处硬编码）
GRANT_COMMANDS: dict[str, str] = {
    TIER_NORMAL: "/激活vip",
    TIER_SUPREME: "/至尊vip",
}

# 面板称号。
# 【为什么值得为这 3 行代码单独设计】
# 10 元/月的玩家付钱买的不只是 1 枚天道令，还有「我在支持这个群」
# 的身份感。`/属性` 面板上挂个标识，成本一行代码，
# 但续费率会明显不同 —— 否则玩家下个月会想「好像也没什么用」。
TIER_LABELS: dict[str, str] = {
    TIER_NORMAL: "赞助者",
    TIER_SUPREME: "至尊赞助者",
}

# 一个月按 30 天算 —— 老板的定价单位就是「月」，不需要自然月复杂度
MONTH_SECONDS = 30 * 86400
# 到期前 N 天在签到回复里提醒（提升续费率，避免「悄悄过期」的失落感）
REMIND_BEFORE_DAYS = 3


# ---------------------------------------------------------------------------
# 存储层
# ---------------------------------------------------------------------------


class VipStore:
    """全局 VIP 名单。

    【为什么内存是权威，不每次读盘】
    只有本进程会写这个文件（发放 / 撤销都走 `/vip` 指令），
    所以内存里的记录永远是最新的，读盘只在首次加载时做一次。
    好处是 `vip_tier()` 变成纯内存查找 —— 而它在每次 `/签到`
    和 `/属性` 上都会被调用，不能有 IO。

    【写入为什么是同步的】
    `vip.json` 只有几 KB，而且写入**极稀疏**（一个月几次发放）。
    为它引入 `asyncio.to_thread` 是过度设计。相比之下
    `players.json`（几百 KB、每次操作都写）才必须走线程池。
    """

    def __init__(self, data_dir: Path | None = None):
        self.data_dir: Path | None = None
        self.path: Path | None = None
        self._records: dict[str, dict[str, Any]] = {}
        self._loaded = False
        if data_dir is not None:
            self.init(data_dir)

    def init(self, data_dir: Path) -> None:
        """绑定数据目录并丢弃缓存（下次访问时重新加载）。"""
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / VIP_FILENAME
        self._records = {}
        self._loaded = False

    # ---------------- 加载 ----------------

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self._load_sync()

    def _load_sync(self) -> None:
        """读盘。**任何异常都降级为空名单** —— VIP 不该让插件起不来。"""
        self._loaded = True
        if self.path is None or not self.path.exists():
            self._records = {}
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.error(f"[textrpg] vip.json 解析失败，本次以空名单启动: {exc}")
            self._records = {}
            return
        if not isinstance(data, dict):
            logger.error("[textrpg] vip.json 顶层不是对象，本次以空名单启动")
            self._records = {}
            return

        clean: dict[str, dict[str, Any]] = {}
        for qq, rec in data.items():
            if not isinstance(rec, dict):
                continue
            try:
                expire = float(rec.get("expire_ts", 0) or 0)
            except (TypeError, ValueError):
                expire = 0.0
            clean[str(qq)] = {
                "tier": str(rec.get("tier", "") or ""),
                "expire_ts": expire,
                "since_ts": float(rec.get("since_ts", 0) or 0),
                "total_months": int(rec.get("total_months", 0) or 0),
                "note": str(rec.get("note", "") or ""),
            }
        self._records = clean
        if clean:
            logger.info(f"[textrpg] 已加载 {len(clean)} 条 VIP 记录")

    # ---------------- 写入 ----------------

    def _write_sync(self) -> None:
        """原子写入：先写临时文件，再 rename 覆盖（同 players.json 的做法）。"""
        if self.path is None or self.data_dir is None:
            return
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            text = json.dumps(
                self._records, ensure_ascii=False, indent=2
            )
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.error(f"[textrpg] vip.json 写入失败: {exc}")

    # ---------------- 查询 ----------------

    def raw(self, qq: str) -> dict[str, Any] | None:
        """取原始记录（**含已过期**），没有返回 None。"""
        self.ensure_loaded()
        rec = self._records.get(str(qq))
        return dict(rec) if rec else None

    def tier(self, qq: str, now: float | None = None) -> str:
        """有效等级：`"normal"` / `"supreme"` / `""`（非 VIP 或已过期）。

        `expire_ts` 是判断是否有效的**唯一依据**。已过期返回空串，
        但记录保留（见模块头注释「过期不删数据」）。
        """
        rec = self.raw(qq)
        if not rec:
            return ""
        try:
            expire = float(rec.get("expire_ts", 0) or 0)
        except (TypeError, ValueError):
            return ""
        if expire <= (time.time() if now is None else float(now)):
            return ""
        tier = str(rec.get("tier", "") or "")
        return tier if tier in TIER_TOKEN_BONUS else ""

    def days_left(self, qq: str, now: float | None = None) -> int:
        """剩余天数（向上取整）；非 VIP 或已过期返回 0。"""
        rec = self.raw(qq)
        if not rec:
            return 0
        ts = time.time() if now is None else float(now)
        try:
            expire = float(rec.get("expire_ts", 0) or 0)
        except (TypeError, ValueError):
            return 0
        remain = expire - ts
        if remain <= 0:
            return 0
        return int(remain // 86400) + (1 if remain % 86400 else 0)

    def list_valid(self, now: float | None = None) -> list[tuple[str, dict[str, Any], int]]:
        """所有**当前有效**的 VIP，按剩余天数降序。"""
        self.ensure_loaded()
        out: list[tuple[str, dict[str, Any], int]] = []
        for qq in self._records:
            if self.tier(qq, now):
                out.append((qq, self.raw(qq) or {}, self.days_left(qq, now)))
        out.sort(key=lambda item: -item[2])
        return out

    # ---------------- 修改 ----------------

    def grant(
        self,
        qq: str,
        months: int = 1,
        tier: str = TIER_NORMAL,
        note: str = "",
        now: float | None = None,
    ) -> tuple[bool, str, dict[str, Any]]:
        """发放 / 续费 → `(是否成功, 提示, 记录)`。

        【续费为什么不能简单用 now + months】
        未到期就续费时，从 `now` 起算会**吃掉剩余天数** ——
        这是最容易引起投诉的低级 bug。

            base_ts = max(now, 已有 expire_ts)      # 未到期 -> 从原到期日往后加
            new_expire = base_ts + months * 30 天

        【升级 normal -> supreme 为什么不折算剩余天数】
        差价只有 20 元，折算规则复杂且玩家看不懂，容易吵架。
        直接覆盖 tier，`expire_ts` 按上面规则累加，开通时提示一句即可。
        """
        self.ensure_loaded()
        qq = str(qq or "").strip()
        if not qq.isdigit():
            return False, f"「{qq}」不是有效的 QQ 号。", {}
        try:
            months = int(months)
        except (TypeError, ValueError):
            return False, "月数必须是整数。", {}
        if months < 1:
            return False, "月数至少为 1。", {}
        tier = str(tier or TIER_NORMAL).strip().lower()
        if tier not in TIER_TOKEN_BONUS:
            return False, f"未知的 VIP 等级「{tier}」（可用 normal / supreme）。", {}

        ts = time.time() if now is None else float(now)
        old = self._records.get(qq)
        old_expire = 0.0
        old_tier = ""
        if old:
            try:
                old_expire = float(old.get("expire_ts", 0) or 0)
            except (TypeError, ValueError):
                old_expire = 0.0
            old_tier = str(old.get("tier", "") or "")

        # 关键：未到期则从原到期时间往后加，不让玩家损失剩余天数
        base_ts = max(ts, old_expire)
        new_expire = base_ts + months * MONTH_SECONDS

        since = float(old.get("since_ts", 0) or 0) if old else 0.0
        if since <= 0:
            since = ts
        total_months = int(old.get("total_months", 0) or 0) if old else 0

        rec = {
            "tier": tier,
            "expire_ts": new_expire,
            "since_ts": since,
            "total_months": total_months + months,
            "note": str(note or "").strip(),
        }
        self._records[qq] = rec
        self._write_sync()

        bits = []
        if old_tier and old_tier != tier:
            bits.append(f"等级 {TIER_LABELS.get(old_tier, old_tier)} → {TIER_LABELS.get(tier, tier)}")
        if old_expire > ts:
            bits.append("剩余天数已保留")
        return True, "；".join(bits), dict(rec)

    def revoke(self, qq: str) -> tuple[bool, str]:
        """立即失效（退款 / 误操作时用）。**删除记录**，不留痕。"""
        self.ensure_loaded()
        qq = str(qq or "").strip()
        if qq not in self._records:
            return False, f"{qq} 不在 VIP 名单里。"
        del self._records[qq]
        self._write_sync()
        return True, "已撤销。"

    def clear_all(self) -> int:
        """清空全部 VIP 记录。仅测试 / 重置用。"""
        self.ensure_loaded()
        n = len(self._records)
        self._records = {}
        self._write_sync()
        return n


# ---------------------------------------------------------------------------
# 模块级单例 + 薄封装
# ---------------------------------------------------------------------------
#
# main.py 只在 __init__ 里调一次 init(data_dir)，
# 之后各处直接用下面这些函数，不用到处传 store。

_store = VipStore()


def init(data_dir: Path) -> None:
    """绑定数据目录。插件启动时调用一次。"""
    _store.init(data_dir)


def store() -> VipStore:
    """取底层 store（测试需要直接访问时用）。"""
    return _store


# ---------------- 查询 ----------------

def vip_tier(qq: str, now: float | None = None) -> str:
    """有效等级：`"normal"` / `"supreme"` / `""`。"""
    return _store.tier(qq, now)


def is_vip(qq: str, now: float | None = None) -> bool:
    return bool(_store.tier(qq, now))


def tier_label(tier: str) -> str:
    """等级 -> 面板称号。未知等级返回空串。"""
    return TIER_LABELS.get(str(tier or ""), "")


def title_of(qq: str, now: float | None = None) -> str:
    """玩家的面板称号（非 VIP 返回空串）。"""
    return tier_label(_store.tier(qq, now))


def daily_token_bonus(qq: str, now: float | None = None) -> int:
    """签到里额外给几枚天道令：非 VIP 0 / 普通 1 / 至尊 3。"""
    tier = _store.tier(qq, now)
    return int(TIER_TOKEN_BONUS.get(tier, 0)) if tier else 0


def days_left(qq: str, now: float | None = None) -> int:
    return _store.days_left(qq, now)


def record(qq: str) -> dict[str, Any] | None:
    """原始记录（含已过期）。"""
    return _store.raw(qq)


def list_vips(now: float | None = None) -> list[tuple[str, dict[str, Any], int]]:
    return _store.list_valid(now)


def expiring_soon(qq: str, now: float | None = None) -> int:
    """距离到期还有几天；不在提醒窗口内返回 0。

    返回 1..REMIND_BEFORE_DAYS 表示「该提醒续费了」。
    """
    left = _store.days_left(qq, now)
    if 0 < left <= REMIND_BEFORE_DAYS:
        return left
    return 0


# ---------------- 修改 ----------------

def grant(
    qq: str,
    months: int = 1,
    tier: str = TIER_NORMAL,
    note: str = "",
    now: float | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """发放 / 续费。**只能由天道（管理员）调用。**"""
    return _store.grant(qq, months, tier, note, now)


def revoke(qq: str) -> tuple[bool, str]:
    """撤销。**只能由天道（管理员）调用。**"""
    return _store.revoke(qq)


def clear_all() -> int:
    """清空（测试 / 重置用）。"""
    return _store.clear_all()


# ---------------- 展示工具 ----------------

def format_date(ts: float) -> str:
    """时间戳 -> `YYYY-MM-DD`（本地时区）。"""
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return "—"
