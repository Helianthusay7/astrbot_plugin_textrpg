"""访问控制模块：群白名单 + 主人判定 + 敏感指令闸门。

设计目标
--------
把「谁能用、在哪能用」这一类判断从 main.py 里抽出来，
原因是 main.py 有 15 条指令、58 处回复点，权限逻辑散落进去必然漏。

拦截分两层：
  1. 会话层（群白名单）—— 用 CustomFilter 在**指令执行前**拦掉，
     这样不仅不会回复，连存档都不会被创建。
     早期版本只在回复出口拦，结果是指令已经跑完、存档已经写盘，
     用户看不到回复却在后台留下了垃圾数据。这个坑已修。
  2. 指令层（主人/管理员）—— 敏感指令额外加一道闸。

为什么不用 @filter.permission_type 一把梭：
  那个只覆盖「主人/管理员」，管不了「这个群能不能用插件」，
  而且逐个装饰器上加容易漏。集中判断更可靠、更好测。
"""

from __future__ import annotations

# 配置项名（与 _conf_schema.json 保持一致）
CONF_ENABLED_GROUPS = "enabled_groups"
CONF_GROUP_MODE = "group_whitelist_mode"
CONF_MASTERS = "master_qq_list"
CONF_ADMIN_ONLY = "reset_admin_only"
CONF_ACTIVATE_WORD = "activate_command"
CONF_ACTIVATED_GROUPS = "activated_groups"

# 白名单模式
MODE_WHITELIST = "whitelist"   # 仅白名单内的群生效
MODE_PRIVATE_ONLY = "private"  # 仅私聊生效，任何群都不响应
MODE_ALL = "all"               # 所有群都生效（不推荐）

VALID_MODES = (MODE_WHITELIST, MODE_PRIVATE_ONLY, MODE_ALL)

# 默认激活口令
DEFAULT_ACTIVATE_WORD = "激活"


def parse_id_list(raw) -> list[str]:
    """把配置里的各种写法统一解析成 QQ/群号字符串列表。

    用户在 WebUI 里可能填：
      "123456,789012"            -> 逗号分隔
      "123456 789012"            -> 空格分隔
      "123456\\n789012"          -> 换行分隔（文本框里最常见）
      [123456, "789012"]         -> list 形式
      123456                     -> 单个数字

    这些都容忍，避免用户因为分隔符不对而「配了没生效」还找不到原因。
    """
    if raw is None:
        return []

    items: list[str] = []

    if isinstance(raw, (list, tuple, set)):
        for entry in raw:
            items.extend(str(entry).replace(",", " ").replace("，", " ").split())
    else:
        text = str(raw)
        # 统一中英文逗号、分号、换行、制表符为空格，再切
        for ch in ("，", ",", ";", "；", "\n", "\r", "\t"):
            text = text.replace(ch, " ")
        items = text.split()

    result: list[str] = []
    for item in items:
        token = item.strip()
        if token and token not in result:
            result.append(token)
    return result


class AccessControl:
    """封装所有权限判断。构造时传入 _conf 读取函数。"""

    def __init__(self, conf_reader, activation=None):
        # conf_reader(key, default) -> value
        self._conf = conf_reader
        # ActivationStore 实例，可为 None（此时退化为纯白名单模式）
        self.activation = activation

    # ------------------------------------------------------------------
    # 配置解析
    # ------------------------------------------------------------------

    @property
    def mode(self) -> str:
        raw = self._conf(CONF_GROUP_MODE, MODE_WHITELIST)
        mode = str(raw).strip().lower() if raw is not None else MODE_WHITELIST
        # 配置写错时退回最安全的白名单模式，而不是放行所有群
        return mode if mode in VALID_MODES else MODE_WHITELIST

    @property
    def activate_word(self) -> str:
        raw = self._conf(CONF_ACTIVATE_WORD, DEFAULT_ACTIVATE_WORD)
        word = str(raw).strip() if raw is not None else ""
        return word or DEFAULT_ACTIVATE_WORD

    @property
    def allowed_groups(self) -> list[str]:
        return parse_id_list(self._conf(CONF_ENABLED_GROUPS, []))

    @property
    def masters(self) -> list[str]:
        return parse_id_list(self._conf(CONF_MASTERS, []))

    @property
    def admin_only(self) -> bool:
        return bool(self._conf(CONF_ADMIN_ONLY, True))

    # ------------------------------------------------------------------
    # 核心判断
    # ------------------------------------------------------------------

    @property
    def whitelist_configured(self) -> bool:
        """白名单是否已配置。未配置时插件对所有群生效（见 is_group_allowed）。"""
        if self.mode != MODE_WHITELIST:
            return True
        return bool(self.allowed_groups)

    def is_group_allowed(self, group_id: str) -> bool:
        """这个群是否允许触发插件。

        判定顺序（重要）：
          私聊                -> 放行
          ALL 模式            -> 放行
          PRIVATE_ONLY 模式   -> 拦截
          WHITELIST 模式：
              1. 在静态群列表里 -> 放行（WebUI 手动填的群号，优先）
              2. 已被主人激活    -> 放行（在群里发「激活」得到的）
              3. 两者都没有      -> 拦截
        """
        mode = self.mode

        # 私聊（group_id 为空）不走群规则
        if not group_id:
            return True

        if mode == MODE_ALL:
            return True
        if mode == MODE_PRIVATE_ONLY:
            return False

        # MODE_WHITELIST
        if str(group_id) in self.allowed_groups:
            return True

        # 群内激活（主人发「激活」口令）
        if self.activation is not None and self.activation.is_activated(group_id):
            return True

        return False

    def is_master(self, qq: str) -> bool:
        """是否为主人。

        主人列表为空时回退到一个「安全兜底」：
        返回 False，即没有任何人被视为主人。
        这比「空列表=人人都是主人」安全得多。
        """
        masters = self.masters
        if not masters:
            return False
        return str(qq) in masters

    def check_session(self, group_id: str, qq: str) -> tuple[bool, str]:
        """会话级检查。返回 (是否放行, 拒绝原因)。

        注意：群未激活时这里虽然返回了原因，但调用方（CustomFilter）
        是静默拦截的——不能回复。因为在陌生群里回一句「本群未启用」
        本身就是刷屏，反而暴露机器人、增加被踢/被举报的风险。
        原因文本只进日志，供排查用。
        """
        if self.is_group_allowed(group_id):
            return True, ""
        return False, (
            f"群 {group_id} 未激活文字 RPG。"
            f"如需启用，请主人在该群发送「{self.activate_word}」。"
        )

    def check_sensitive(self, event, group_id: str, qq: str) -> tuple[bool, str]:
        """敏感指令检查（重置等）。

        判定顺序：
          1. 是主人 → 放行（无论 admin_only 怎么配）
          2. admin_only=True → 只认主人，群管理员也不行 → 拒绝
          3. admin_only=False → 群管理员也放行
        """
        if self.is_master(qq):
            return True, ""

        if self.admin_only:
            # 严格模式：只认主人列表，不认群管理员
            return False, (
                "该指令仅限主人使用。"
                "如需授权，请让主人把你的 QQ 号加入插件配置的「主人列表」。"
            )

        # 宽松模式：群管理员可用
        try:
            if event.is_admin():
                return True, ""
        except Exception:  # noqa: BLE001 - 平台不支持时按无权限处理
            pass

        return False, (
            "该指令仅限主人或群管理员使用。"
            "如需授权，请让主人把你的 QQ 号加入插件配置的「主人列表」。"
        )

    # ------------------------------------------------------------------
    # 入群邀请
    # ------------------------------------------------------------------

    def check_invite(self, inviter_qq: str) -> tuple[bool, str]:
        """是否接受这次入群邀请。

        策略：只接受主人的邀请。其他任何人拉机器人进群，一律拒绝。

        为什么不用「白名单里的群就放行」：
        别人可以把机器人拉进一个「群号恰好撞上白名单」的群——
        但群号是唯一的，撞不了。真正的风险是：
        你不想让机器人在任何你没亲自点头的群里出现。
        所以判据定为「邀请人是不是主人」，这是最直接的。
        """
        if self.is_master(inviter_qq):
            return True, ""

        if not self.masters:
            # 没配主人时，无人可邀请——否则装上插件就会被陌生人拉来拉去
            return False, "未配置主人，拒绝入群邀请"

        return False, f"邀请人 {inviter_qq} 不是主人，拒绝入群邀请"
