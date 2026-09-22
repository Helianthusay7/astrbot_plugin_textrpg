"""捏脸（角色创建）会话状态机。

设计要点：
- 三步交互：名字 -> 形象图 -> 确认预览。
- 会话只存内存，**不落盘** —— 它是「正在进行中的对话」，
  不是玩家数据。落盘会留下大量垃圾（走一半放弃的会话）。
- 60 秒超时自动回收，防止内存泄漏。
- 名字校验含同群重名检查，通过 dup_checker 回调注入，
  避免本模块直接依赖 PlayerStore（保持可单测）。

为什么不用 AstrBot 的 conversation 机制：
    插件需要在「玩家已经 /报名 之后」继续接住他的下一条消息，
    而 AstrBot 的 @filter.command 只在指令匹配时触发。
    所以走 event_message_type(ALL) 全局拦截 + 会话表查询，
    命中会话才处理，否则放行给正常指令流水线。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable

# ---------------------------------------------------------------------------
# 可调参数
# ---------------------------------------------------------------------------

# 会话超时（秒）。超过这个时间没走完流程就自动放弃。
SESSION_TIMEOUT = 60.0

# 名字长度限制
NAME_MIN = 2
NAME_MAX = 12

# 头像允许的扩展名
ALLOWED_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")

# 头像大小上限（MB）
AVATAR_MAX_MB = 5.0

# 步骤常量
STEP_NAME = "name"
STEP_AVATAR = "avatar"
STEP_CONFIRM = "confirm"

# 特殊指令词
SKIP_WORDS = ("跳过", "默认", "不换")
CANCEL_WORDS = ("取消", "放弃", "算了")
CONFIRM_WORDS = ("确认", "确定", "是", "好", "yes", "y")

# 名字过滤：纯数字、纯符号、含控制字符的一律拒绝
_RE_HAS_WORD = re.compile(r"[\w\u4e00-\u9fff]")
_RE_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# 敏感词（极简表，只拦明显不合适的；不做过度审查）
BANNED_WORDS = (
    "习近平", "共产党", "法轮", "台独", "港独",
    "傻逼", "操你", "妈的", "色情", "赌博",
)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def clean_name(raw: str) -> str:
    """去掉首尾空白与控制字符。"""
    if not raw:
        return ""
    return _RE_CONTROL.sub("", str(raw)).strip()


def validate_name(raw: str, *, min_len: int = NAME_MIN, max_len: int = NAME_MAX) -> tuple[bool, str]:
    """校验角色名，返回 (是否合法, 错误提示)。"""
    name = clean_name(raw)

    if not name:
        return False, "角色名不能为空，请重新发送。"

    if len(name) < min_len:
        return False, f"角色名太短了，至少要 {min_len} 个字。"

    if len(name) > max_len:
        return False, f"角色名太长了，最多 {max_len} 个字。"

    if not _RE_HAS_WORD.search(name):
        return False, "角色名不能全是符号或空格，换一个吧。"

    if name.isdigit():
        return False, "角色名不能是纯数字，换一个吧。"

    for word in BANNED_WORDS:
        if word in name:
            return False, "这个名字不太合适，换一个吧。"

    return True, ""


def sniff_image_ext(data: bytes) -> str:
    """从图片的魔术字节猜扩展名，猜不出返回空串。

    【为什么要这个】图片组件的 file 字段在两种情况下拿不到可用文件名：
      1. base64 内联（AstrBot 的 Image.fromBase64 会塞成 "base64://..."），
         os.path.basename 得到的是一长串 base64，没有扩展名；
      2. 上游只给了 url 或裸二进制。
    这时若直接拿 basename 去过扩展名白名单，会把合法图片误判成
    「图片格式不支持」。所以退化成看内容头。

    覆盖头像白名单里的四种格式（ALLOWED_EXT）。
    """
    if not data:
        return ""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def validate_avatar(filename: str, size_bytes: int, max_mb: float = AVATAR_MAX_MB) -> tuple[bool, str]:
    """校验头像文件，返回 (是否合法, 错误提示)。"""
    lower = (filename or "").lower()
    if not any(lower.endswith(ext) for ext in ALLOWED_EXT):
        exts = "/".join(e.lstrip(".") for e in ALLOWED_EXT)
        return False, f"图片格式不支持，请发 {exts} 格式的图片。"

    if size_bytes <= 0:
        return False, "图片读取失败了，麻烦重新发一次。"

    if size_bytes > max_mb * 1024 * 1024:
        return False, f"图片太大了（超过 {max_mb:g}MB），请换一张小一点的。"

    return True, ""


def is_skip(text: str) -> bool:
    return clean_name(text).lower() in SKIP_WORDS


def is_cancel(text: str) -> bool:
    return clean_name(text).lower() in CANCEL_WORDS


def is_confirm(text: str) -> bool:
    return clean_name(text).lower() in CONFIRM_WORDS


# ---------------------------------------------------------------------------
# 会话对象
# ---------------------------------------------------------------------------

@dataclass
class CreationSession:
    """一次「正在创建角色」的对话状态。"""

    group_id: str
    qq: str
    step: str = STEP_NAME
    name: str = ""
    avatar: str = ""          # 头像相对路径，空表示用默认
    # 触发本次会话的那条消息 id。
    # 用来防止「/报名」这条消息本身被 on_creation_message 当成角色名吞掉 ——
    # 同一条消息会先跑 register（建会话），再跑全局拦截器（见 main.py）。
    origin_msg_id: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        return f"{self.group_id}:{self.qq}"

    def touch(self) -> None:
        self.updated_at = time.time()

    def expired(self, now: float | None = None, timeout: float = SESSION_TIMEOUT) -> bool:
        return (now or time.time()) - self.updated_at > timeout


# ---------------------------------------------------------------------------
# 会话池
# ---------------------------------------------------------------------------

class CreationFlow:
    """捏脸会话池。

    纯内存、无 IO，方便单元测试。
    dup_checker 是一个回调 (group_id, name) -> bool，
    返回 True 表示该名字已被占用。不注入则不做重名检查。
    """

    def __init__(
        self,
        timeout: float = SESSION_TIMEOUT,
        dup_checker: Callable[[str, str], bool] | None = None,
    ):
        self.timeout = timeout
        self.dup_checker = dup_checker
        self._sessions: dict[str, CreationSession] = {}

    # ---------------- 生命周期 ----------------

    def start(
        self,
        group_id: str,
        qq: str,
        origin_msg_id: str = "",
    ) -> CreationSession:
        """开一个会话。已有会话则重置（支持 /报名 重来）。

        origin_msg_id 记录「是哪条消息把会话开起来的」，拦截器据此跳过那条
        消息本身 —— 否则 /报名 会被当成角色名提交。
        """
        sess = CreationSession(
            group_id=str(group_id),
            qq=str(qq),
            origin_msg_id=str(origin_msg_id or ""),
        )
        self._sessions[sess.key] = sess
        return sess

    def get(self, group_id: str, qq: str) -> CreationSession | None:
        """取会话，顺带做过期检查。过期则丢弃并返回 None。"""
        key = f"{group_id}:{qq}"
        sess = self._sessions.get(key)
        if sess is None:
            return None
        if sess.expired(timeout=self.timeout):
            del self._sessions[key]
            return None
        return sess

    def drop(self, group_id: str, qq: str) -> bool:
        """丢弃会话，返回是否真的存在过。"""
        return self._sessions.pop(f"{group_id}:{qq}", None) is not None

    def active_count(self) -> int:
        return len(self._sessions)

    def sweep(self, now: float | None = None) -> int:
        """清理所有超时会话，返回清理数量。"""
        now = now or time.time()
        dead = [k for k, s in self._sessions.items() if s.expired(now, self.timeout)]
        for k in dead:
            del self._sessions[k]
        return len(dead)

    # ---------------- 步骤推进 ----------------

    def submit_name(self, group_id: str, qq: str, raw: str) -> tuple[bool, str]:
        """提交名字。成功则推进到 avatar 步骤。

        返回 (是否通过, 提示文案)。
        """
        sess = self.get(group_id, qq)
        if sess is None:
            return False, "创建流程已经结束了，请重新发送 /报名。"

        ok, err = validate_name(raw)
        if not ok:
            sess.touch()
            return False, err

        name = clean_name(raw)
        if self.dup_checker and self.dup_checker(group_id, name):
            sess.touch()
            return False, f"本群已经有玩家叫「{name}」了，换一个吧。"

        sess.name = name
        sess.step = STEP_AVATAR
        sess.touch()
        return True, name

    def submit_avatar(self, group_id: str, qq: str, path: str) -> tuple[bool, str]:
        """提交头像相对路径（空串表示用默认）。成功则推进到 confirm 步骤。"""
        sess = self.get(group_id, qq)
        if sess is None:
            return False, "创建流程已经结束了，请重新发送 /报名。"

        sess.avatar = path or ""
        sess.step = STEP_CONFIRM
        sess.touch()
        return True, sess.avatar

    def back_to_avatar(self, group_id: str, qq: str) -> None:
        """从确认页退回头像步骤（玩家想换图）。"""
        sess = self.get(group_id, qq)
        if sess is not None:
            sess.step = STEP_AVATAR
            sess.touch()
