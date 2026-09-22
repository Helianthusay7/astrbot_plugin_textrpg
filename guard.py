"""防封辅助：回复节流、随机延迟、长消息分段。

为什么需要这个模块存在：
    封号风险主要来自协议端和账号本身，插件层能做的只有一件事 ——
    「别给协议端添乱」，也就是让机器人的行为特征别那么像机器。

    具体是三个可观测的机器人特征：
      1. 秒回     —— 人类打字有思考时间，机器人是 0ms 响应
      2. 长消息   —— 一条 500 字的整齐文本，人不会这么发
      3. 高频     —— 短时间内大量消息，尤其在多个群同时发

    本模块就是针对这三点。注意这不是"防封SDK"，
    它只是降低特征值，不能突破腾讯的风控。

设计原则：
    - 所有节流逻辑集中在此，业务代码不散落 sleep
    - 任何异常都不能影响正常回复（宁可漏节流，不能吞消息）
    - 参数全部来自插件配置，方便随时关闭
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict, deque

from astrbot.api import logger

# ---------------------------------------------------------------------------
# 默认参数
# ---------------------------------------------------------------------------

# 回复前随机延迟（秒）。模拟人类"看到消息 → 思考 → 打字"的时间。
# 下限不能太低（秒回是明显特征），上限也不能太高（不然体验太差）。
DEFAULT_DELAY_RANGE = (0.4, 1.6)

# 单条消息最大字符数，超过就分段
DEFAULT_MAX_CHUNK = 180

# 分段之间的间隔（秒）
DEFAULT_CHUNK_GAP = (0.6, 1.3)

# 频率熔断：每个会话在窗口内最多发多少条
#
# 阈值不能定太低。曾经定 8 条/60 秒，结果一个群里几个人
# 各发几条指令就触发了，正常玩家被静默，体验很差。
# 20 条/60 秒更合理：真人正常玩不会到这个量，
# 但脚本刷屏或机器人被滥用时会立刻被拦住。
DEFAULT_RATE_LIMIT = 20
DEFAULT_RATE_WINDOW = 60.0

# 熔断触发后的沉默时长（秒）。
# 也不能太久，否则玩家会觉得"机器人坏了"。
DEFAULT_MUTE_DURATION = 20.0


class ReplyGuard:
    """回复节流器。

    按会话（群）维度维护状态，因为风控关注的是
    "一个群里的消息频率"，而不是全局频率。

    用法：
        guard = ReplyGuard(config_getter)
        chunks = guard.split(text)
        for i, chunk in enumerate(chunks):
            await guard.wait_before(session_key, first=(i == 0))
            if guard.is_muted(session_key):
                return
            yield event.plain_result(chunk)
            guard.record(session_key)
    """

    def __init__(self, conf):
        """
        conf: 一个 callable，签名 (key, default) -> value。
              传入插件的 _conf 方法即可，这样参数能跟随 WebUI 配置。
        """
        self._conf = conf
        # 会话 -> 最近消息时间戳队列，用于频率熔断
        self._history: dict[str, deque[float]] = defaultdict(deque)
        # 会话 -> 沉默截止时间戳
        self._muted_until: dict[str, float] = {}

    # ---------------- 配置读取 ----------------

    def _num(self, key: str, default: float) -> float:
        """读一个数值配置，非法值退回默认。"""
        try:
            value = self._conf(key, default)
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _pair(self, key: str, default: tuple[float, float]) -> tuple[float, float]:
        """读一个区间配置（形如 [0.4, 1.6]）。"""
        raw = self._conf(key, list(default))
        try:
            lo, hi = float(raw[0]), float(raw[1])
            if lo < 0 or hi < lo:
                return default
            return lo, hi
        except (TypeError, ValueError, IndexError, KeyError):
            return default

    @property
    def enabled(self) -> bool:
        """总开关。关掉后所有节流都跳过，方便调试。"""
        value = self._conf("anti_ban_enable", True)
        return bool(value) if isinstance(value, bool) else True

    @property
    def delay_range(self) -> tuple[float, float]:
        return self._pair("reply_delay_range", DEFAULT_DELAY_RANGE)

    @property
    def max_chunk(self) -> int:
        try:
            value = int(self._conf("max_chunk_chars", DEFAULT_MAX_CHUNK))
            # 太小会导致消息被切得稀碎，给个下限
            return max(50, value)
        except (TypeError, ValueError):
            return DEFAULT_MAX_CHUNK

    @property
    def chunk_gap(self) -> tuple[float, float]:
        return self._pair("chunk_gap_range", DEFAULT_CHUNK_GAP)

    @property
    def rate_limit(self) -> int:
        try:
            value = int(self._conf("rate_limit_count", DEFAULT_RATE_LIMIT))
            return max(1, value)
        except (TypeError, ValueError):
            return DEFAULT_RATE_LIMIT

    @property
    def rate_window(self) -> float:
        return max(1.0, self._num("rate_limit_window", DEFAULT_RATE_WINDOW))

    @property
    def mute_duration(self) -> float:
        return max(0.0, self._num("mute_duration", DEFAULT_MUTE_DURATION))

    # ---------------- 频率熔断 ----------------

    def _prune(self, session: str) -> deque[float]:
        """清掉窗口外的历史记录。"""
        q = self._history[session]
        cutoff = time.time() - self.rate_window
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def is_muted(self, session: str) -> bool:
        """当前会话是否处于沉默期。"""
        if not self.enabled:
            return False
        until = self._muted_until.get(session, 0.0)
        if until <= time.time():
            # 沉默期已过，清掉记录
            if session in self._muted_until:
                del self._muted_until[session]
            return False
        return True

    def mute_remaining(self, session: str) -> float:
        """沉默还剩多少秒。"""
        return max(0.0, self._muted_until.get(session, 0.0) - time.time())

    def record(self, session: str) -> None:
        """记录一条已发出的消息，必要时触发熔断。"""
        if not self.enabled:
            return
        try:
            q = self._prune(session)
            q.append(time.time())
            if len(q) >= self.rate_limit:
                self._muted_until[session] = time.time() + self.mute_duration
                logger.info(
                    f"[textrpg] 会话 {session} 触发频率熔断，"
                    f"沉默 {self.mute_duration:.0f} 秒"
                )
        except Exception as exc:  # noqa: BLE001 - 节流失败不能影响回复
            logger.error(f"[textrpg] 频率记录异常: {exc}")

    # ---------------- 延迟 ----------------

    async def wait_before(self, session: str, first: bool = True) -> None:
        """发送前等待。first=True 用首条延迟，否则用分段间隔。"""
        if not self.enabled:
            return
        try:
            lo, hi = self.delay_range if first else self.chunk_gap
            if hi <= 0:
                return
            await asyncio.sleep(random.uniform(lo, hi))
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[textrpg] 延迟异常: {exc}")

    # ---------------- 分段 ----------------

    def split(self, text: str) -> list[str]:
        """把长文本切成若干段。

        切分优先级：换行 > 句末标点 > 逗号 > 硬切。
        尽量在语义边界断开，避免把一句话劈成两半。
        """
        if not self.enabled:
            return [text] if text else []
        return split_text(text, self.max_chunk)


def split_text(text: str, limit: int) -> list[str]:
    """无状态的文本分段函数。

    供测试与不依赖插件配置的场景使用。
    与 ReplyGuard.split 共用同一套切分规则。
    """
    if not text:
        return []
    if limit <= 0:
        return [text]
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text

    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut < limit * 0.5:
            for punct in ("。", "！", "？", "；", ".", "!", "?"):
                pos = window.rfind(punct)
                if pos > cut:
                    cut = pos + 1
        if cut < limit * 0.5:
            for punct in ("，", ",", "、"):
                pos = window.rfind(punct)
                if pos > cut:
                    cut = pos + 1
        if cut < limit * 0.5:
            cut = limit

        chunks.append(remaining[:cut].rstrip("\n"))
        remaining = remaining[cut:].lstrip("\n")

    if remaining:
        chunks.append(remaining)

    return [c for c in chunks if c]
