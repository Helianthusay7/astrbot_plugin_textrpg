"""群聊指令式文字修仙插件入口。

职责边界（重要）：
    main.py 只做三件事 —— 注册指令、做输入校验、组装消息。
    所有数值计算在 battle.py / player.py / realm.py，存档读写在本文件内的
    _commit() 辅助方法里。保持这个边界，以后改平衡不用碰这里。

指令一览（v3 共 24 条）：
    管理：激活 / 取消激活 / 激活列表 / 重置（后三条仅主人）
    基础：报名 / 属性（面板 / 属性面板）/ 背包 / 改名
    成长：修炼 / 闭关 / 探索 / 突破
    秘典：大道 / 修行 / 装配
    战斗：历练（打怪）/ 寻敌（挑战）/ 恢复（复活）
    法宝：装备 / 卸下 / 使用 / 出售
    经济：坊市（商店）/ 购买
    社交：排行榜

v3.0 修仙化变更：
    - 题材全量置换：修为 / 转数 / 妖兽 / 法宝 / 灵石 / 气血
    - 修炼语义修正：主动一次性加修为，冷却 5 分钟（原挂机语义改名「闭关」）
    - 新增 /闭关（挂机 8 小时，含走火入魔）、/探索（挂机出秘典）
    - 新增 /突破（大境界突破 + 渡天劫）、/大道 /修行 /装配（秘典系统）
    - /打怪 /挑战 /复活 /商店 保留为别名，只加不减

权限模型（重要）：
    普通指令  -> gate_session   群未激活则静默不执行
    重置      -> gate_session + gate_sensitive
    激活类    -> gate_sensitive 仅主人（不能加 gate_session，否则鸡生蛋）
"""

from __future__ import annotations

import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
import astrbot.api.message_components as Comp

from . import battle
from . import dungeon as dungeon_mod
from . import realm as realm_mod
from . import special as special_mod
from . import vip as vip_mod
from .menu import render_intro, render_menu
from .acl import AccessControl
from .activation import ActivationStore
from .character import (
    ALLOWED_EXT,
    CANCEL_WORDS,
    CONFIRM_WORDS,
    SKIP_WORDS,
    STEP_AVATAR,
    STEP_CONFIRM,
    STEP_NAME,
    CreationFlow,
    clean_name,
    is_cancel,
    is_confirm,
    is_skip,
    sniff_image_ext,
    validate_avatar,
)
from .data import PlayerStore
from .gate import gate_sensitive, gate_session
from .guard import ReplyGuard
from .items import (
    BUY_PRICE_MULTIPLIER,
    EFFECT_LABELS,
    EQUIPMENTS,
    HEAL_ITEMS,
    MONSTER_BY_NAME,
    SHOP_CONSUMABLES,
    buy_price,
    can_equip,
    get_item,
    req_turns_of,
    sell_price,
    set_display_name,
    shop_stock,
    split_quality,
    tier_rank_of,
)
from .player import (
    CULTIVATE_COOLDOWN,
    DEATH_REST_SECONDS,
    DEATH_REVIVE_GOLD,
    EXPLORE_COOLDOWN,
    HUNT_COOLDOWN,
    SECLUSION_COOLDOWN,
    SET_BONUS_SLOTS,
    SET_DAMAGE_BONUS,
    SLOTS,
    Player,
    SLOT_ALIASES,
    SLOT_LABELS,
    base_atk as player_mod_base_atk,
    base_def as player_mod_base_def,
    base_hp as player_mod_base_hp,
)
from .scriptures import (
    MAX_TIER,
    SCRIPTURE_NAMES,
    SCRIPTURES,
    equip_slots,
    get_scripture,
    has_scripture,
    qi_cost,
    relic_cost,
    relic_name,
    tier_info,
)

# 配置项缺省值。与 _conf_schema.json 中的 default 保持一致。
DEFAULT_HUNT_COOLDOWN = HUNT_COOLDOWN
DEFAULT_CULTIVATE_COOLDOWN = CULTIVATE_COOLDOWN
DEFAULT_SECLUSION_COOLDOWN = SECLUSION_COOLDOWN
DEFAULT_EXPLORE_COOLDOWN = EXPLORE_COOLDOWN
DEFAULT_ENABLE_TRAIN = True
DEFAULT_RANKING_SIZE = 10
DEFAULT_DEATH_REST = DEATH_REST_SECONDS
DEFAULT_DEATH_GOLD = DEATH_REVIVE_GOLD
DEFAULT_CREATION_TIMEOUT = 60.0
DEFAULT_ELITE_CHANCE = 0.08


def _fmt_duration(seconds: float) -> str:
    """把秒数格式化成人话，如「4 分 12 秒」。"""
    total = int(max(0.0, seconds))
    if total < 60:
        return f"{total} 秒"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} 分 {sec} 秒" if sec else f"{minutes} 分钟"
    hours, minutes = divmod(minutes, 60)
    if minutes:
        return f"{hours} 小时 {minutes} 分"
    return f"{hours} 小时"


# ----------------------------------------------------------------------
# 闸门取函数
#
# 装饰器在类定义时求值，那时还没有 self，所以这里传的是「如何从 self 取判定结果」
# 的函数，真正的判定在调用时执行。
# ----------------------------------------------------------------------

def _SESSION_GATE(self, event):
    """会话闸门：群未激活则该指令完全不执行（含不建存档）。"""
    try:
        group_id, qq = self._scope(event)
        return self.acl.check_session(group_id, qq)
    except Exception as exc:  # noqa: BLE001 - 闸门自身异常不能崩插件
        logger.error(f"[textrpg] 会话闸门异常，按放行处理: {exc}")
        return True, ""


def _SENSITIVE_GATE(self, event):
    """敏感指令闸门：仅主人（或群管理员）。"""
    try:
        group_id, qq = self._scope(event)
        return self.acl.check_sensitive(event, group_id, qq)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[textrpg] 权限闸门异常，按拒绝处理: {exc}")
        # 敏感操作失败时选择「拒绝」而不是「放行」，这是安全的默认方向
        return False, "权限校验出错，已拒绝执行。"


class TextRPGPlugin(Star):
    """文字 RPG 插件主类。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        # 存档落在 data/plugin_data/<plugin_name>/，插件更新不会覆盖
        self.data_dir = StarTools.get_data_dir()
        self.store = PlayerStore(self.data_dir)
        # v3.5 VIP（赞助者）名单：**全局**文件，按 QQ 号索引，与群无关。
        # 必须单独存一份 —— 否则「A 群充了 VIP，去 B 群就没权益了」。
        vip_mod.init(self.data_dir)
        # 物品定义索引，供 player 对象计算装备加成
        self.item_db: dict[str, dict] = {
            e["name"]: e for e in EQUIPMENTS
        }
        self.config = config
        # 回复节流器：随机延迟 + 分段 + 频率熔断，降低机器人行为特征
        self.guard = ReplyGuard(self._conf)
        # 已激活群（主人发「激活」口令后落盘）
        self.activation = ActivationStore(self.data_dir)
        # 访问控制：群白名单 + 激活状态 + 主人判定 + 敏感指令闸门
        self.acl = AccessControl(self._conf, self.activation)
        # v2 捏脸会话池（纯内存，60 秒超时）
        self.creation = CreationFlow(
            timeout=self.creation_timeout,
            dup_checker=self._name_taken,
        )
        # 头像目录
        self.avatar_dir = self.data_dir / "avatars"
        logger.info(
            f"[textrpg] 插件已加载，存档目录：{self.data_dir}，"
            f"生效模式={self.acl.mode}，"
            f"已激活群={self.activation.activated_groups() or '（无）'}"
        )
        if not self.acl.masters:
            logger.warning(
                "[textrpg] 尚未配置「主人 QQ 列表」。"
                "没有主人 = 没人能用「激活」和重置指令，插件在群里会保持沉默。"
            )
        if self.acl.mode == "all":
            logger.warning(
                "[textrpg] 生效模式为 all，插件会在所有群响应。"
                "这有刷屏和封号风险，建议改为 whitelist 并使用群内激活。"
            )

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------

    def _conf(self, key: str, default):
        """安全读取配置项。

        配置可能不存在（用户没打开过 WebUI）或值非法，
        统一在这里兜底，避免各指令里重复写 try。
        """
        if self.config is None:
            return default
        try:
            value = self.config.get(key, default)
        except AttributeError:
            # config 不是 dict 时（极端情况）退回默认值
            return default
        if value is None:
            return default
        # 类型不符时退回默认值，避免 float("abc") 之类崩掉
        if isinstance(default, bool) and not isinstance(value, bool):
            return default
        if isinstance(default, (int, float)) and isinstance(value, str):
            try:
                value = type(default)(value)
            except (TypeError, ValueError):
                return default
        return value

    @property
    def cooldown(self) -> float:
        """历练（原打怪）冷却（秒）。"""
        try:
            return max(0.0, float(self._conf("battle_cooldown", DEFAULT_HUNT_COOLDOWN)))
        except (TypeError, ValueError):
            return DEFAULT_HUNT_COOLDOWN

    @property
    def cultivate_cooldown(self) -> float:
        """修炼（主动一次性）冷却（秒）。"""
        try:
            return max(
                0.0,
                float(self._conf("cultivate_cooldown", DEFAULT_CULTIVATE_COOLDOWN)),
            )
        except (TypeError, ValueError):
            return DEFAULT_CULTIVATE_COOLDOWN

    @property
    def seclusion_cooldown(self) -> float:
        """闭关（挂机结算）冷却（秒）。"""
        try:
            return max(
                0.0,
                float(self._conf("train_cooldown", DEFAULT_SECLUSION_COOLDOWN)),
            )
        except (TypeError, ValueError):
            return DEFAULT_SECLUSION_COOLDOWN

    @property
    def explore_cooldown(self) -> float:
        """探索冷却（秒）。"""
        try:
            return max(
                0.0, float(self._conf("explore_cooldown", DEFAULT_EXPLORE_COOLDOWN))
            )
        except (TypeError, ValueError):
            return DEFAULT_EXPLORE_COOLDOWN

    # 兼容 v2 命名：老测试与老配置里叫 train_cooldown
    @property
    def train_cooldown(self) -> float:
        """闭关冷却（v2 遗留名字，语义 = seclusion_cooldown）。"""
        return self.seclusion_cooldown

    @property
    def death_rest_seconds(self) -> float:
        """死亡后原地休整时长（秒）。"""
        try:
            return max(
                0.0, float(self._conf("death_rest_seconds", DEFAULT_DEATH_REST))
            )
        except (TypeError, ValueError):
            return DEFAULT_DEATH_REST

    @property
    def death_revive_gold(self) -> int:
        """花钱复活的价格。"""
        try:
            return max(0, int(self._conf("death_revive_gold", DEFAULT_DEATH_GOLD)))
        except (TypeError, ValueError):
            return DEFAULT_DEATH_GOLD

    @property
    def creation_timeout(self) -> float:
        """捏脸会话超时（秒）。"""
        try:
            return max(
                10.0, float(self._conf("creation_timeout", DEFAULT_CREATION_TIMEOUT))
            )
        except (TypeError, ValueError):
            return DEFAULT_CREATION_TIMEOUT

    @property
    def elite_chance(self) -> float:
        """精英怪出现概率。"""
        try:
            v = float(self._conf("elite_chance", DEFAULT_ELITE_CHANCE))
        except (TypeError, ValueError):
            return DEFAULT_ELITE_CHANCE
        return max(0.0, min(1.0, v))

    @property
    def train_enabled(self) -> bool:
        return bool(self._conf("enable_train", DEFAULT_ENABLE_TRAIN))

    @property
    def ranking_size(self) -> int:
        try:
            return max(1, int(self._conf("ranking_size", DEFAULT_RANKING_SIZE)))
        except (TypeError, ValueError):
            return DEFAULT_RANKING_SIZE

    @property
    def inventory_limit(self) -> int | None:
        """背包上限。返回 None 表示用 player 模块的默认值。"""
        value = self._conf("max_inventory", None)
        if value is None:
            return None
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # 回复输出（含防封节流）
    # ------------------------------------------------------------------

    async def _reply(
        self,
        event: AstrMessageEvent,
        text: str,
        single: bool = False,
        image: str | None = None,
    ):
        """统一的回复出口，带随机延迟、分段、频率熔断。

        async for _chunk in self._reply(event, )。):
            yield _chunk
        把这层收口在这里的好处：
          - 节流参数改配置就生效，不用翻 57 处调用点
          - 某个会话被熔断时，安静跳过而不是报错

        注意：熔断触发后本次回复会被丢弃（而不是排队），
        因为排队会把积压的消息在某刻集中发出，反而更像机器人。

        single=True 时**整段一次发出、不分段**。
        用于 /属性 这类「玩家明确要看一整块信息」的场景 ——
        面板被 max_chunk_chars（默认 180）切成三四条反而难读。
        默认 False，保持原来的防封分段行为。

        image 传本地图片路径时，**第一段**会和图片拼成同一条消息
        （图片在上、文字在下，走 chain_result）。
        老板明确要求「一个消息框包括图片和文字」——
        拆成两条发（先图后文）在 QQ 里是两个气泡，观感很怪。
        """
        session = self._session_key(event)

        if self.guard.is_muted(session):
            remaining = self.guard.mute_remaining(session)
            logger.info(
                f"[textrpg] 会话 {session} 处于熔断期，"
                f"丢弃一条回复（剩余 {remaining:.0f} 秒）"
            )
            return

        if single:
            # 不分段：整段一次发出（空文本仍然不发）
            chunks = [text] if text else []
        else:
            chunks = self.guard.split(text)
        for idx, chunk in enumerate(chunks):
            await self.guard.wait_before(session, first=(idx == 0))
            if self.guard.is_muted(session):
                return
            if image and idx == 0:
                # 图文同框：图片在上、文字在下，一条消息发出去
                yield event.chain_result(
                    [Comp.Image.fromFileSystem(image), Comp.Plain(chunk)]
                )
            else:
                yield event.plain_result(chunk)
            self.guard.record(session)

    def _session_key(self, event: AstrMessageEvent) -> str:
        """会话标识，用于按群统计频率。"""
        group_id, qq = self._scope(event)
        return group_id or f"pm:{qq}"

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    async def _commit(self) -> None:
        """统一的落盘入口，失败不抛给用户。"""
        try:
            await self.store.save()
        except Exception as exc:  # noqa: BLE001 - 存档失败不能崩插件
            logger.error(f"[textrpg] 存档写入异常: {exc}")

    @staticmethod
    def _scope(event: AstrMessageEvent) -> tuple[str, str]:
        """取当前会话的作用域，返回 (群号, QQ号)。

        私聊时群号返回空字符串，走独立的私聊存档池。
        这样同一个人在多个群各有独立进度。
        """
        group_id = ""
        try:
            group_id = event.get_group_id() or ""
        except Exception:  # noqa: BLE001 - 部分平台无此接口
            group_id = ""
        return str(group_id), str(event.get_sender_id())

    @staticmethod
    def _msg_id(event: AstrMessageEvent) -> str:
        """取本条消息的 id，拿不到就返回空串。"""
        msg_obj = getattr(event, "message_obj", None)
        return str(getattr(msg_obj, "message_id", "") or "")

    @staticmethod
    def _raw_text(event: AstrMessageEvent) -> str:
        """取消息段里的**原始**纯文本（未经 wake_prefix 剥离）。

        `event.get_message_str()` 已经被框架剥掉 `/` 了
        （core/pipeline/waking_check/stage.py:130），所以想判断「玩家是不是
        在发指令」必须回到消息段去读，否则 `/菜单` 和「菜单」分不出来。
        """
        msg_obj = getattr(event, "message_obj", None)
        comps = list(getattr(msg_obj, "message", []) or [])
        parts = []
        for comp in comps:
            if type(comp).__name__ == "Plain":
                parts.append(str(getattr(comp, "text", "") or ""))
        return "".join(parts)

    def _require_player(self, event: AstrMessageEvent) -> Player | None:
        """取当前玩家，未报名返回 None。"""
        group_id, qq = self._scope(event)
        return self.store.get(group_id, qq)

    @staticmethod
    def _is_admin(event) -> bool:
        """当前发言者是不是「天道」（AstrBot 管理员）。

        【为什么要有这个显式判断，而不是只用框架的 permission_type】
        `/vip 查询` 必须对**所有人**开放（玩家要能看自己还剩几天），
        而 `/vip 开通 / 撤销 / 列表` 只给天道 —— 同一个指令方法只能挂
        一种框架权限，所以只能走方法内分支判断。

        副作用是好的：显式判断**可测**。框架层的拦截在单测里是桩，
        拦不住任何东西，而「非管理员不能发放 VIP」是一条**安全属性**，
        必须能被断言钉住。

        用 getattr 保护：部分平台适配器的事件对象没有 is_admin。
        """
        fn = getattr(event, "is_admin", None)
        if not callable(fn):
            return False
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001
            return False

    def _name_taken(self, group_id: str, name: str) -> bool:
        """同群重名检查（捏脸流程用）。

        只在本群内查重 —— 不同群可以有同名玩家，因为进度本来就隔离。
        """
        try:
            for p in self.store.players_in_group(str(group_id)):
                if p.name == name:
                    return True
        except Exception:  # noqa: BLE001 - 查重失败不该拦住建号
            return False
        return False

    def _avatar_rel_path(self, group_id: str, qq: str) -> str:
        """头像的相对路径（相对 data_dir），用于存档。"""
        gid = group_id or "pm"
        return f"avatars/{gid}_{qq}.png"

    def _avatar_abs_path(self, rel: str):
        """相对路径 -> 绝对路径。空串返回 None。"""
        if not rel:
            return None
        return self.data_dir / rel

    async def _save_avatar(self, event: AstrMessageEvent, group_id: str, qq: str) -> tuple[bool, str]:
        """把消息里的图片存到本地，返回 (是否成功, 相对路径或错误提示)。

        只支持单张图。取第一张 image 组件。
        """
        import os

        comps = []
        try:
            msg_obj = getattr(event, "message_obj", None)
            comps = list(getattr(msg_obj, "message", []) or [])
        except Exception:  # noqa: BLE001
            comps = []

        image_comp = None
        for comp in comps:
            # AstrBot 的 Image 组件类型名是 Image
            if type(comp).__name__ == "Image":
                image_comp = comp
                break

        if image_comp is None:
            return False, "没有检测到图片，请直接发一张图片。"

        try:
            self.avatar_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[textrpg] 创建头像目录失败: {exc}")
            return False, "服务器保存图片失败，请稍后再试。"

        rel = self._avatar_rel_path(group_id, qq)
        abs_path = self.data_dir / rel

        # 按 path -> file -> url 依次尝试。
        #
        # 【为什么要这么绕】AstrBot 的 Image 组件**没有 base64 字段** ——
        # base64 数据是以 "base64://" 前缀塞在 file 里的（见 Image.fromBase64）；
        # 而 NapCat(aiocqhttp) 给的 file 常常是 **URL 编码过的本地路径**，
        # 形如  d:%5Cai%5CAstrBot%5Cdata%5Ctemp%5Cmedia_image_xxx.jpg
        # （%5C 就是 \）。把它当 URL 去 aiohttp 请求必然失败，
        # 玩家看到的就是「图片读取失败了，麻烦重新发一次」。
        raw: bytes | None = None
        for cand in (
            getattr(image_comp, "path", None),
            getattr(image_comp, "file", None),
            getattr(image_comp, "url", None),
        ):
            if not cand:
                continue
            raw = await self._read_image_source(str(cand))
            if raw:
                break

        if raw is None:
            return False, "图片读取失败了，麻烦重新发一次。"

        # 用于校验的文件名：优先用组件给的 basename（含合法扩展名时），
        # 否则（base64 内联 / 只给了 url）用魔术字节猜一个 ——
        # 不然 basename 会是一长串 base64，过不了扩展名白名单。
        base_name = os.path.basename(str(getattr(image_comp, "file", "") or ""))
        _lower = base_name.lower()
        if not any(_lower.endswith(ext) for ext in ALLOWED_EXT):
            _sniffed = sniff_image_ext(raw)
            if _sniffed:
                base_name = "avatar" + _sniffed

        ok, err = validate_avatar(base_name, len(raw))
        if not ok:
            return False, err

        try:
            with open(abs_path, "wb") as fp:
                fp.write(raw)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[textrpg] 写入头像失败: {exc}")
            return False, "服务器保存图片失败，请稍后再试。"

        return True, rel

    async def _read_image_source(self, src: str) -> bytes | None:
        """把图片组件的任意一种「地址形式」读成 bytes。

        四种形态都真实出现过，必须全兜住：

        | 形态 | 例子 |
        |---|---|
        | base64 内联 | `base64://iVBORw0KGgo...` |
        | file URI | `file:///d:/ai/AstrBot/data/temp/x.jpg` |
        | 本地路径（可能被 URL 编码） | `d:%5Cai%5CAstrBot%5Cdata%5Ctemp%5Cx.jpg` |
        | 网络地址 | `http://127.0.0.1:3000/...` |

        读不到就返回 None —— 调用方会依次试下一个候选字段。
        """
        import base64 as _b64
        import os
        import re as _re
        from urllib.parse import unquote

        s = (src or "").strip()
        if not s:
            return None

        # 1) base64 内联
        if s.startswith("base64://"):
            try:
                return _b64.b64decode(s[len("base64://") :])
            except Exception:  # noqa: BLE001
                return None

        # 2) file:// URI
        if s.startswith("file://"):
            p = unquote(s[len("file://") :])
            if _re.match(r"^/[a-zA-Z]:", p):  # file:///d:/x -> d:/x
                p = p[1:]
            if os.name == "nt":
                p = p.replace("/", os.sep)
            try:
                with open(p, "rb") as fp:
                    return fp.read()
            except Exception:  # noqa: BLE001
                return None

        # 3) 本地路径（NapCat 常给 URL 编码的 Windows 路径，如 d:%5Cai%5C...）
        decoded = unquote(s)
        looks_local = bool(
            _re.match(r"^[a-zA-Z]:[\\/]", decoded)  # d:\... / d:/...
            or decoded.startswith("\\\\")  # UNC
            or decoded.startswith("/")  # 类 unix 绝对路径
        )
        if looks_local:
            try:
                with open(decoded, "rb") as fp:
                    return fp.read()
            except Exception:  # noqa: BLE001
                return None

        # 4) 网络地址
        if s.startswith(("http://", "https://")):
            try:
                return await self._download_image(s)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[textrpg] 下载头像失败: {exc}")
                return None

        return None

    async def _download_image(self, url: str) -> bytes:
        """异步下载图片。兼容 http(s) 与 file:// 。"""
        if url.startswith("file://"):
            import os

            path = url[7:]
            with open(path, "rb") as fp:
                return fp.read()

        import aiohttp

        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                return await resp.read()

    # ------------------------------------------------------------------
    # LLM 只读快照（v3.5 新增）
    #
    # 玩家 @ 机器人闲聊时，把角色的**只读**状态注入系统提示词，
    # 让 AI 能准确回答「我多少攻击力」这类问题，而不是瞎编数字。
    #
    # 【为什么必须是只读 —— 三条加起来 = AI 只能「看」，不能「改」】
    #   1. 这里只调 `self.store.get()`（纯读），**绝不写存档**；
    #   2. 插件没有注册任何 `@filter.llm_tool`，LLM 连游戏指令都调不到；
    #   3. AstrBot 的 `computer_use_runtime` 是 "none"，LLM 拿不到文件/shell 工具。
    #
    # 【为什么不直接塞整个存档】
    # 存档里有背包、装备名一大堆内容，全塞进去又贵又容易让 AI 跑偏。
    # 只给「玩家最可能问、且 AI 编不出来」的那几个数。
    # ------------------------------------------------------------------

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req) -> None:
        """把当前玩家的只读状态注入 LLM 系统提示词。

        ⚠️ **必须返回 None**。返回 True 会让框架直接中断这次 LLM 请求
        （`agent_sub_stages/internal.py:261` 的 `if await call_event_hook(...): return`）。
        """
        try:
            player = self._require_player(event)
            if player is None:
                return
            block = self._llm_snapshot(player)
        except Exception as exc:  # noqa: BLE001 - 钩子出错绝不能影响玩家对话
            logger.warning(f"[textrpg] 生成只读快照失败: {exc}")
            return
        if block:
            req.system_prompt = (req.system_prompt or "") + "\n\n" + block

    def _llm_snapshot(self, player: Player) -> str:
        """把角色状态拼成一段**只读**文本，供 LLM 参考。"""
        turns = int(player.level or 0)
        max_hp = player.max_hp(self.item_db)
        qi_cur, qi_need = realm_mod.qi_progress(turns, int(player.exp or 0))

        atk = player.equip_pct("atk_pct", self.item_db) * 100
        dfn = player.equip_pct("def_pct", self.item_db) * 100
        hp_b = player.equip_pct("hp_pct", self.item_db) * 100
        sp = player.special_pct() * 100
        pen = player.penetration() * 100

        lines = [
            "【玩家角色只读快照】",
            f"角色名：{player.name}",
            f"境界：{realm_mod.realm_label(turns)}（总第 {turns} 转）",
            f"气血：{player.hp} / {max_hp}",
            f"修为：{qi_cur} / {qi_need}",
            f"灵石：{player.gold}",
            f"天道令：{int(player.inventory.get('天道令', 0))}",
        ]
        bonus = []
        if atk:
            bonus.append(f"攻击+{atk:.1f}%")
        if dfn:
            bonus.append(f"防御+{dfn:.1f}%")
        if hp_b:
            bonus.append(f"气血+{hp_b:.1f}%")
        if sp:
            bonus.append(f"特殊+{sp:.1f}%")
        if pen:
            bonus.append(f"穿透+{pen:.1f}%")
        if bonus:
            lines.append("装备加成：" + "，".join(bonus))

        lines.append(
            "\n这段数据是只读快照：你只能引用它，不能修改任何游戏数据"
            "（做不到，也没有权限）。快照里没有的内容（某件装备的具体属性、"
            "某只妖兽的血量等）一律不要编造，让玩家自己在游戏里看。"
        )
        return "\n".join(lines)

    def _equip_bonus_text(self, player: Player) -> str:
        """把装备加成拼成一行摘要，没有加成返回空串。

        v3.4：装备从绝对值改成百分比，这里跟着改 ——
        旧版显示「攻击+722」那种绝对数，在百分比体系下已经没有意义了。
        顺带把穿甲和套装也带上，这是一行更完整的状态摘要。
        """
        parts = []
        for key, label in (("atk_pct", "攻击"), ("def_pct", "防御"),
                           ("hp_pct", "气血")):
            val = player.equip_pct(key, self.item_db) * 100
            if val:
                parts.append(f"{label}+{val:.1f}%")
        sp = player.special_pct() * 100
        if sp:
            parts.append(f"特殊+{sp:.1f}%")
        pen = player.penetration() * 100
        if pen:
            parts.append(f"穿甲{pen:.0f}%")
        sid = player.active_set()
        if sid:
            # 【v3.5】套系名走映射：内部 id（物穿/法穿/肉盾）**永不改名**
            # （存档按它匹配套系），给玩家看的是展示名（破锋/通玄/镇岳）。
            parts.append(f"{set_display_name(sid)}套")
        return " ".join(parts)

    def _resting_hint(self, player: Player) -> str:
        """休整中的提示文案。"""
        return (
            f"😴 你还在休整中，剩余 {_fmt_duration(player.rest_remain())}\n"
            f"发送 /复活 可以查看提前恢复的方式。"
        )

    def _idle_hint(self, player: Player) -> str:
        """挂机中（闭关 / 游历）尝试做别的玩法时的拦截文案。

        v3.6：闭关与游历都是「占着人」的状态，期间不能修炼 / 历练 /
        寻敌 / 秘境。这里统一给一句能立刻照做的提示，
        而不是干巴巴地说「不行」。
        """
        if player.is_in_seclusion():
            return (
                f"🕯️ 你正在闭关（已过 "
                f"{_fmt_duration(player.seclusion_elapsed())}），心无旁骛。\n"
                f"想出来就发送 /结束闭关 结算。"
            )
        return (
            f"🧭 你正在游历（已过 "
            f"{_fmt_duration(player.explore_elapsed())}）。\n"
            f"想收工就发送 /结束探索 结算。"
        )

    # ------------------------------------------------------------------
    # 基础指令
    # ------------------------------------------------------------------

    @gate_session(_SESSION_GATE)
    @filter.command("菜单", alias={"帮助", "指令", "help"})
    async def cmd_menu(self, event: AstrMessageEvent):
        """指令菜单（/菜单 /帮助 /指令 /help 四条等价）。

        【为什么菜单要带「下一步建议」】
        新手打开菜单想知道的是「我现在该按什么」，不是「有哪些指令」。
        所以 render_menu 会根据角色状态（修为满了没、装备穿齐没、
        血够不够）在最上面给出唯一一条建议。没角色的人也能看菜单 ——
        不然他不知道该先 /报名。
        """
        player = self.store.get(*self._scope(event))
        async for _chunk in self._reply(
            event, render_menu(player, self.item_db)
        ):
            yield _chunk

    @gate_session(_SESSION_GATE)
    @filter.command("简介", alias={"玩法", "介绍"})
    async def cmd_intro(self, event: AstrMessageEvent):
        """玩法简介：讲清这游戏是怎么玩的。"""
        player = self.store.get(*self._scope(event))
        async for _chunk in self._reply(
            event, render_intro(player, self.item_db)
        ):
            yield _chunk

    @gate_session(_SESSION_GATE)
    @filter.command("报名")
    async def register(self, event: AstrMessageEvent):
        """创建角色：进入三步捏脸流程。"""
        group_id, qq = self._scope(event)
        existing = self.store.get(group_id, qq)
        if existing is not None:
            async for _chunk in self._reply(
                event,
                f"你已经报过名了，{existing.name}。\n"
                f"发送 /属性 查看你的面板。\n"
                f"想换名字可以发 /改名。"
            ):
                yield _chunk
            return

        # 开一个捏脸会话
        #
        # origin_msg_id 必须记下来：on_creation_message 是全局拦截器，
        # 它和本方法会**同时**被激活（filter 都通过），且本方法先执行。
        # 不记的话，拦截器紧接着就会把「/报名」这条消息本身当成名字提交，
        # 玩家会看到「✅ 角色名「报名」」这种结果。
        self.creation.start(group_id, qq, origin_msg_id=self._msg_id(event))
        async for _chunk in self._reply(
            event,
            "🎭 角色创建 · 第 1 步 / 共 3 步\n"
            "请发送你的角色名（2~12 个字，不能是纯数字）\n"
            f"（{self.creation_timeout:.0f} 秒内回复有效）"
        ):
            yield _chunk
        # 本条消息到此为止，别再流给 on_creation_message
        # （process_stage 的 handler 循环会检查 event.is_stopped()）
        event.stop_event()

    @gate_session(_SESSION_GATE)
    @filter.command("改名")
    async def rename(self, event: AstrMessageEvent, new_name: str = ""):
        """修改角色名，例如 /改名 独孤求败。"""
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        if not new_name:
            async for _chunk in self._reply(event, "用法：/改名 新名字"):
                yield _chunk
            return

        from .character import validate_name

        ok, err = validate_name(new_name)
        if not ok:
            async for _chunk in self._reply(event, err):
                yield _chunk
            return

        group_id, _qq = self._scope(event)
        name = clean_name(new_name)
        if name != player.name and self._name_taken(group_id, name):
            async for _chunk in self._reply(event, f"本群已经有玩家叫「{name}」了，换一个吧。"):
                yield _chunk
            return

        old = player.name
        player.name = name
        await self._commit()
        async for _chunk in self._reply(event, f"✅ 改名成功：{old} → {name}"):
            yield _chunk

    # ------------------------------------------------------------------
    # v2 捏脸流程：全局消息拦截
    #
    # /报名 之后玩家会连发「名字」「图片」「确认」三条消息，
    # 它们都不是指令，所以要用 event_message_type(ALL) 兜住。
    # 命中会话才处理，否则直接 return 放行给正常指令流水线。
    # ------------------------------------------------------------------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_creation_message(self, event: AstrMessageEvent):
        """捏脸流程的消息拦截器。"""
        group_id, qq = self._scope(event)

        # 会话不存在 -> 放行（这是绝大多数情况，尽早返回）
        sess = self.creation.get(group_id, qq)
        if sess is None:
            return

        # 双保险 1：这就是「/报名」那条消息本身 -> 放行。
        # 同一条消息会先跑 register（建会话）再跑本拦截器；不排掉的话
        # 会把指令名当成角色名。register 末尾的 stop_event() 是第一道防线，
        # 这里是第二道（不依赖框架行为，框架改了也还在）。
        if sess.origin_msg_id and sess.origin_msg_id == self._msg_id(event):
            return

        # 双保险 2：玩家在捏脸过程中发了**别的指令**（/菜单、/属性…）
        # -> 放行给指令流水线，别把「菜单」当成角色名。
        # 必须读消息段的原始文本：event.get_message_str() 里的 `/` 已被框架
        # 剥掉，用它判断会分不出「/菜单」和「菜单」。
        # 「/取消」是例外 —— 它是流程自己的退出词，交给下面的 is_cancel 处理。
        _raw = self._raw_text(event).strip()
        if _raw.startswith("/"):
            _bare_raw = _raw.lstrip("/!！.").strip()
            if not (_bare_raw and is_cancel(_bare_raw)):
                return

        # 会话存在但群未激活 -> 放弃会话（不响应）
        allowed, _reason = self.acl.check_session(group_id, qq)
        if not allowed:
            self.creation.drop(group_id, qq)
            return

        text = ""
        try:
            text = (event.get_message_str() or "").strip()
        except Exception:  # noqa: BLE001
            text = ""

        # 去掉可能存在的唤醒前缀（/ 之类），让「/取消」也能识别
        bare = text.lstrip("/!！.").strip() if text else ""

        # 玩家想放弃
        if bare and is_cancel(bare):
            self.creation.drop(group_id, qq)
            async for _chunk in self._reply(event, "已取消创建。想重新开始时发送 /报名。"):
                yield _chunk
            return

        # ---------------- 第 1 步：收名字 ----------------
        if sess.step == STEP_NAME:
            if not bare:
                async for _chunk in self._reply(event, "请发送文字作为你的角色名。"):
                    yield _chunk
                return
            ok, payload = self.creation.submit_name(group_id, qq, bare)
            if not ok:
                async for _chunk in self._reply(event, payload):
                    yield _chunk
                return
            async for _chunk in self._reply(
                event,
                f"✅ 角色名「{payload}」\n\n"
                f"🎭 角色创建 · 第 2 步 / 共 3 步\n"
                f"请发一张图片作为你的角色形象，"
                f"或发送「跳过」使用默认形象。"
            ):
                yield _chunk
            return

        # ---------------- 第 2 步：收形象图 ----------------
        if sess.step == STEP_AVATAR:
            if bare and is_skip(bare):
                self.creation.submit_avatar(group_id, qq, "")
                async for _chunk in self._reply(event, self._creation_preview(sess)):
                    yield _chunk
                return

            ok, payload = await self._save_avatar(event, group_id, qq)
            if not ok:
                async for _chunk in self._reply(event, payload):
                    yield _chunk
                return
            self.creation.submit_avatar(group_id, qq, payload)
            async for _chunk in self._reply(
                event, "✅ 形象已保存\n\n" + self._creation_preview(sess)
            ):
                yield _chunk
            return

        # ---------------- 第 3 步：确认 ----------------
        if sess.step == STEP_CONFIRM:
            if bare and is_confirm(bare):
                player = self._finalize_creation(sess, event)
                self.creation.drop(group_id, qq)
                await self._commit()
                async for _chunk in self._reply(
                    event,
                    f"🎉 道友，{player.name}，你已踏上道途。\n"
                    f"{player.realm_label} ｜ 气血 {player.hp} ｜ 灵石 {player.gold}\n"
                    f"发送 /修炼 开始你的第一次打坐，"
                    f"或 /历练 出门闯荡。",
                ):
                    yield _chunk
                return

            if bare and is_skip(bare):
                # 在确认页说「跳过」= 重选形象
                self.creation.back_to_avatar(group_id, qq)
                async for _chunk in self._reply(event, "好的，请重新发一张图片。"):
                    yield _chunk
                return

            async for _chunk in self._reply(
                event,
                "回复「确认」创建角色，回复「取消」放弃。\n"
                "想换形象就再发一张图片。",
            ):
                yield _chunk
            # 玩家在确认页又发了图 -> 直接换图
            ok, payload = await self._save_avatar(event, group_id, qq)
            if ok:
                self.creation.submit_avatar(group_id, qq, payload)
                async for _chunk in self._reply(event, "✅ 形象已更新\n\n" + self._creation_preview(sess)):
                    yield _chunk
            return

    def _creation_preview(self, sess) -> str:
        """拼角色预览卡片。"""
        avatar_line = "已上传自定义形象" if sess.avatar else "使用默认形象"
        return (
            "🎭 角色创建 · 第 3 步 / 共 3 步\n"
            f"┌─────────────────────┐\n"
            f"│ 名字：{sess.name}\n"
            f"│ 形象：{avatar_line}\n"
            f"│ 境界：炼气期 1 转\n"
            f"│ 气血 100 ｜ 攻击 10\n"
            f"│ 防御 5   ｜ 灵石 50\n"
            f"└─────────────────────┘\n"
            f"回复「确认」创建，回复「取消」放弃。"
        )

    def _finalize_creation(self, sess, event: AstrMessageEvent) -> Player:
        """把捏脸结果写成正式存档。"""
        group_id, qq = sess.group_id, sess.qq
        player, _created = self.store.get_or_create(
            group_id, qq, sess.name or event.get_sender_name()
        )
        player.name = sess.name or player.name
        if sess.avatar:
            player.avatar = sess.avatar
        player.created_at = time.time()
        return player

    @gate_session(_SESSION_GATE)
    @filter.command("属性", alias={"面板", "属性面板"})
    async def profile(self, event: AstrMessageEvent):
        """查看自己的角色面板（/属性 /面板 /属性面板 三条等价）。"""
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        item_db = self.item_db
        turns = player.turns
        mult = player.stat_mult()

        # ---- 属性拆解（v3.4：装备已改为百分比） ----
        # 旧写法是 `基础 × 乘区 + 装备绝对值`，v3.4 起装备走百分比，
        # 所以面板改成 `基础 × 乘区 × (1 + 装备%) × (1 + 特殊%)`。
        # 这里把每一层都显示出来，方便自己核对数值、玩家也能看懂加成来源。
        b_atk = player_mod_base_atk(turns)
        b_def = player_mod_base_def(turns)
        b_hp = player_mod_base_hp(turns)
        eq_atk_pct = player.equip_pct("atk_pct", item_db) * 100
        eq_def_pct = player.equip_pct("def_pct", item_db) * 100
        eq_hp_pct = player.equip_pct("hp_pct", item_db) * 100
        sp_pct = player.special_pct() * 100

        qi_now, qi_need = realm_mod.qi_progress(turns, player.exp)

        def _stat_parts(base: int, eq_pct: float) -> str:
            """属性拆解串，如 `120 × 1.00 × 1.50（装备）`。

            单独抽出来是为了给「气血」复用 —— 气血那行要显示 `当前/上限`，
            不能直接用 _stat_line，但括号里的拆解是同一套。
            """
            parts = f"{base} × {mult:.2f}"
            if eq_pct:
                parts += f" × {1 + eq_pct / 100:.2f}（装备）"
            if sp_pct:
                parts += f" × {1 + sp_pct / 100:.2f}（特殊）"
            return parts

        def _stat_line(label: str, base: int, eq_pct: float, final: int) -> str:
            """拼一行属性：基础 × 乘区 × 装备%，末段有特殊装备才显示。"""
            return f"{label}：{final}　（{_stat_parts(base, eq_pct)}）"

        # v3.5：VIP 称号。付费玩家买的不只是每天几枚天道令，
        # 还有「我在支持这个群」的身份感 —— 挂个标识，成本一行代码。
        # 非 VIP 时 title_of 返回空串，不显示这一行。
        _vip_title = vip_mod.title_of(player.qq)

        lines = [
            f"📋 {player.name} 的道途"
            + (f"　【{_vip_title}】" if _vip_title else ""),
            f"境界：{player.realm_label}"
            + (f"　（转生 {player.ascensions} 次）" if player.ascensions else ""),
            f"修为：{qi_now}/{qi_need}"
            + ("　（已圆满，可 /突破）" if qi_need <= 0 else ""),
            "",
            f"气血：{player.hp}/{player.max_hp(item_db)}"
            f"　（{_stat_parts(b_hp, eq_hp_pct)}）",
            _stat_line("攻击", b_atk, eq_atk_pct, player.atk(item_db)),
            _stat_line("防御", b_def, eq_def_pct, player.defense(item_db)),
            f"灵石：{player.gold}",
        ]

        # ---- v3.1：战力 / 穿甲 / 套装 ----
        # 战力只用于面板对比与排行榜，不参与任何战斗计算。
        power = battle.player_power(player, item_db)
        pen = player.penetration()
        eq_count = player.equipped_count()
        lines.append(f"战力：{power}")

        set_txt = f"（{eq_count}/{SET_BONUS_SLOTS}）"
        if player.has_full_set():
            set_txt += f"　五行俱全：增伤 +{int(SET_DAMAGE_BONUS * 100)}%"
        lines.append(f"穿甲：{pen * 100:.0f}%　套装：{set_txt}")

        # ---- v3.6：下面这三行单独一条发 ----
        #
        # 老板原话：「我指的战绩就是完整的这个
        #   秘境最深：尚未闯过（发送 /秘境 开始）
        #   秘典：未装配　（装配位 1）
        #   战绩：历练胜 2　道消 1」
        # 在他眼里这三行是**一组**「进度 / 战绩」，要跟主面板分开成一条。
        # 注意别只拆「战绩」那一行 —— 那样等于把前两行并进了主面板。
        tail_lines: list[str] = []

        if player.dungeon_best > 0:
            tail_lines.append(f"秘境最深：第 {player.dungeon_best} 层")
        else:
            tail_lines.append("秘境最深：尚未闯过（发送 /秘境 开始）")

        # 已装配的秘典
        if player.equipped_scriptures:
            equipped_txt = "、".join(
                f"{n}{player.learned_tier(n)}阶" for n in player.equipped_scriptures
            )
            tail_lines.append(
                f"秘典：{equipped_txt}"
                f"　（{len(player.equipped_scriptures)}/{player.equip_slots}）"
            )
        else:
            tail_lines.append(f"秘典：未装配　（装配位 {player.equip_slots}）")

        tail_lines.append(
            f"战绩：历练胜 {player.total_kills}　道消 {player.total_deaths}"
        )

        # ---- 【特殊】栏（有帝兵才显示）----
        #
        # 老板要求：「无帝兵装备时不显示【特殊】栏」。
        # render_special_slot 在没帝兵时返回 None，这里自然就不加了。
        _spec_block = special_mod.render_special_slot(player)
        if _spec_block:
            lines.append("")
            lines.append(_spec_block)

        # ---- 状态行 ----
        status = []
        if player.is_resting():
            # v3.6：改叫「疗伤中」—— 以前叫「闭关疗伤中」，
            # 现在「闭关」是正式的挂机状态，两个词撞在一起玩家会看懵
            status.append(f"疗伤中，剩余 {_fmt_duration(player.rest_remain())}")
        if player.is_in_seclusion():
            status.append(
                f"闭关中，已过 {_fmt_duration(player.seclusion_elapsed())}"
            )
        if player.is_exploring():
            status.append(f"游历中，已过 {_fmt_duration(player.explore_elapsed())}")
        hunt_cd = player.hunt_remain(self.cooldown)
        if hunt_cd > 0:
            status.append(f"历练冷却 {_fmt_duration(hunt_cd)}")
        cult_cd = player.cultivate_remain(self.cultivate_cooldown)
        if cult_cd > 0:
            status.append(f"修炼冷却 {_fmt_duration(cult_cd)}")
        if status:
            lines.append("")
            lines.append("状态：" + "　｜　".join(status))

        # ---- 发送：一条「图文」（形象 + 面板）→ 一条「进度战绩」 ----
        #
        # 老板要求：
        #   1. 「一个消息框包括图片和文字，图片在文字上面」——
        #      所以走 chain_result 拼成**同一条**，拆成两条发在 QQ 里
        #      是两个气泡，观感很怪；
        #   2. 「秘境最深 / 秘典 / 战绩」这三行单独一条。
        _avatar_path = self._avatar_abs_path(player.avatar)
        _image = None
        if _avatar_path is not None and _avatar_path.exists():
            _image = str(_avatar_path)

        # 面板整块发出（single=True）—— 别被 180 字的分段阈值切成三四条
        async for _chunk in self._reply(
            event, "\n".join(lines), single=True, image=_image
        ):
            yield _chunk

        # 进度 / 战绩三行，单独一条
        async for _chunk in self._reply(event, "\n".join(tail_lines)):
            yield _chunk

    @gate_session(_SESSION_GATE)
    @filter.command("背包", alias={"储物袋"})
    async def inventory(self, event: AstrMessageEvent):
        """查看储物袋与已装备的法宝。"""
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        if not player.inventory:
            async for _chunk in self._reply(
                event, "储物袋空空如也。出门闯荡吧 —— /历练"
            ):
                yield _chunk
            return

        lines = ["🎒 储物袋"]
        for item_name, count in sorted(player.inventory.items()):
            item = get_item(item_name)
            if item and item.get("kind") == "equipment":
                slot = SLOT_LABELS.get(item["slot"], item["slot"])
                lines.append(f"· {item_name} ×{count}（{slot}）")
            elif item and item.get("effect") == "relic":
                lines.append(f"· {item_name} ×{count}（秘典残页）")
            else:
                lines.append(f"· {item_name} ×{count}")

        if player.equipment:
            lines.append("\n已装备")
            for slot, item_name in player.equipment.items():
                lines.append(f"· {SLOT_LABELS.get(slot, slot)}：{item_name}")

        if player.equipped_scriptures:
            lines.append("\n已装配秘典")
            for name in player.equipped_scriptures:
                lines.append(f"· {name}（{player.learned_tier(name)} 阶）")

        async for _chunk in self._reply(event, "\n".join(lines)):
            yield _chunk

    # ------------------------------------------------------------------
    # 战斗指令
    # ------------------------------------------------------------------

    @gate_session(_SESSION_GATE)
    @filter.command("历练", alias={"打怪"})
    async def hunt(self, event: AstrMessageEvent):
        """外出历练：随机遭遇一只妖兽并自动战斗。（别名 /打怪）"""
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        # 休整中不能历练（死亡只产生时间成本，不扣资源）
        if player.is_resting():
            async for _chunk in self._reply(event, self._resting_hint(player)):
                yield _chunk
            return

        # v3.6：挂机中（闭关 / 游历）不能外出历练
        if player.is_idle():
            async for _chunk in self._reply(event, self._idle_hint(player)):
                yield _chunk
            return

        # 冷却：从存档读时间戳，重启不清零
        remain = player.hunt_remain(self.cooldown)
        if remain > 0:
            async for _chunk in self._reply(
                event,
                f"⏳ 你刚历练归来，需要调息\n剩余时间：{_fmt_duration(remain)}",
            ):
                yield _chunk
            return

        monster = battle.spawn_monster(player.turns, player.ascensions)
        elite = battle.roll_elite(self.elite_chance)
        result = battle.fight(
            player, monster, self.item_db, self.inventory_limit, elite=elite,
            # 历练已降级为「物品与灵石来源」，修为只给零头（见 EXP_SCALE_FIELD）
            exp_scale=battle.EXP_SCALE_FIELD,
        )
        player.last_hunt_ts = time.time()
        _growth = self._apply_death(player, result)
        await self._commit()
        async for _chunk in self._reply(
            event, self._format_battle(player, result, _growth)
        ):
            yield _chunk

    @gate_session(_SESSION_GATE)
    @filter.command("秘境", alias={"闯关"})
    async def dungeon(self, event: AstrMessageEvent):
        """闯入秘境：无限层自动连闯，失败即止。（别名 /闯关）

        设计要点（与老板确认过）：
          - 难度是**固定成长曲线**，与玩家转数无关。转数越高自然打得越远。
          - 奖励只看层数，首通重奖、重刷衰减。
          - 失败**不计死亡**、不掉转、不进入休整。
          - 每日一次。
          - 从历史最高层往回退 2 层开打，跳过已碾压的层。
        """
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        # v3.6：挂机中（闭关 / 游历）不能闯秘境
        if player.is_idle():
            async for _chunk in self._reply(event, self._idle_hint(player)):
                yield _chunk
            return

        # 每日一次
        if not dungeon_mod.can_run_today(player):
            nxt = dungeon_mod.start_layer_for(player)
            async for _chunk in self._reply(
                event,
                "🌙 今日秘境已闭。\n"
                f"明日再来时将从第 {nxt} 层继续（你的最深纪录："
                f"第 {player.dungeon_best} 层）。",
            ):
                yield _chunk
            return

        # 秘境是试炼，不是历练 —— 休整中也能进（不消耗体力）
        before_best = int(player.dungeon_best or 0)
        result = dungeon_mod.run_dungeon(
            player, self.item_db, self.inventory_limit
        )
        dungeon_mod.mark_ran(player)
        await self._commit()

        async for _chunk in self._reply(
            event, self._format_dungeon(player, result, before_best)
        ):
            yield _chunk

    def _format_dungeon(
        self, player: Player, result, before_best: int
    ) -> str:
        """把秘境战果拼成一条消息。

        排版原则：先给结论（打到第几层），再给奖励，最后给逐层明细。
        """
        lines = ["🏯 秘境试炼"]

        if result.cleared <= 0:
            # 一层没通：起始层就失败了
            lines.append(
                f"你在第 {result.start_layer} 层便败下阵来。"
            )
            if result.failed_layer:
                lines.append(f"（第 {result.failed_layer} 层 · 未通过）")
            lines.append("")
            lines.append("秘境不会伤及道基 —— 掉转、伤势一概不留。")
            if before_best > 0:
                lines.append(f"你的最深纪录仍是第 {before_best} 层。")
            else:
                lines.append("先在 /修炼 或 /历练 上多下些功夫再来吧。")
            return "\n".join(lines)

        lines.append(
            f"本次自第 {result.start_layer} 层起，"
            f"连破 {result.cleared} 层，止步于第 {result.reached_layer} 层"
        )
        if result.failed_layer:
            lines.append(f"（第 {result.failed_layer} 层 · 未通过）")
        elif result.capped:
            lines.append(f"（已达单次上限 {dungeon_mod.DUNGEON_MAX_LAYERS_PER_RUN} 层）")

        lines.append("")
        lines.append(f"修为 +{result.qi_gain}　灵石 +{result.gold_gain}")
        if result.first_clears:
            lines.append(
                f"其中首通 {result.first_clears} 层（🆕 全额），"
                f"重刷 {result.cleared - result.first_clears} 层"
                f"（↻ {int(dungeon_mod.DUNGEON_RERUN_MULT * 100)}% 收益）"
            )
        # 【v3.5】天道令按**整趟**发（有首通就给 1 枚），不是按层 ——
        # 按层发的话玩家从第 5 层连破 20 层能一次拿 20 枚，5 天凑齐帝兵。
        if result.tokens:
            lines.append(f"⚜️ 首通奖励：天道令 +{result.tokens}")
        if result.items:
            lines.append("获得：" + "、".join(result.items))
        else:
            lines.append("此行未获物品（首通且层数为 3 的倍数才掉落）")
        # 里程碑装备（首通 30/35/40 层给的七/八/九品）单独列一行 ——
        # 它和普通掉落不是一个来源，混在一起玩家看不出这是「爬到这一层」的回报
        if result.milestone_items:
            lines.append("🏆 里程碑：" + "、".join(result.milestone_items))

        # 历史纪录
        if result.is_new_record:
            lines.append("")
            lines.append(f"🎉 新纪录！秘境最深推进到第 {result.best_layer} 层")
        else:
            lines.append(f"最深纪录：第 {result.best_layer} 层")

        lines.append("")
        lines.append("—— 逐层战报 ——")
        lines.extend(result.logs)

        return "\n".join(lines)

    @gate_session(_SESSION_GATE)
    @filter.command("寻敌", alias={"挑战"})
    async def challenge(self, event: AstrMessageEvent, monster_name: str = ""):
        """指定对手历练，例如 /寻敌 史莱姆。（别名 /挑战）"""
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        if not monster_name:
            async for _chunk in self._reply(
                event, "用法：/寻敌 妖兽名\n可寻敌：" + "、".join(MONSTER_BY_NAME)
            ):
                yield _chunk
            return

        monster = MONSTER_BY_NAME.get(monster_name)
        if monster is None:
            names = "、".join(MONSTER_BY_NAME)
            async for _chunk in self._reply(event, f"没有这种妖兽。可寻敌：{names}"):
                yield _chunk
            return

        if player.is_resting():
            async for _chunk in self._reply(event, self._resting_hint(player)):
                yield _chunk
            return

        # v3.6：挂机中（闭关 / 游历）不能外出寻敌
        if player.is_idle():
            async for _chunk in self._reply(event, self._idle_hint(player)):
                yield _chunk
            return

        # 境界限制优先于冷却：这是硬性规则，先告知玩家
        if monster["level"] > player.turns + 5:
            async for _chunk in self._reply(
                event,
                f"{monster_name} 是 Lv.{monster['level']} 的妖兽，"
                f"你现在 {player.realm_label}，去送死吗？"
            ):
                yield _chunk
            return

        remain = player.hunt_remain(self.cooldown)
        if remain > 0:
            async for _chunk in self._reply(
                event,
                f"⏳ 你刚历练归来，需要调息\n剩余时间：{_fmt_duration(remain)}",
            ):
                yield _chunk
            return

        result = battle.fight(
            player, monster, self.item_db, self.inventory_limit,
            # 寻敌同历练：修为只给零头
            exp_scale=battle.EXP_SCALE_FIELD,
        )
        player.last_hunt_ts = time.time()
        _growth = self._apply_death(player, result)
        await self._commit()
        async for _chunk in self._reply(
            event, self._format_battle(player, result, _growth)
        ):
            yield _chunk

    def _apply_death(self, player: Player, result: battle.BattleResult) -> list[str]:
        """战斗结算的统一收口：道消疗伤 + 特殊装备成长推进。

        【为什么把帝兵成长挂在这里】
        历练和寻敌两条路径都要推进成长，如果各自写一遍，
        以后加第三条路径（秘境/活动）就会漏。收口在一个方法里，
        新增战斗入口只要调它就行。
        """
        if result.outcome == battle.OUTCOME_LOSE:
            player.start_rest(self.death_rest_seconds, item_db=self.item_db)

        # 帝兵成长：只有胜利才算「弑杀」，败北不涨（也不掉 —— 老板定的）
        #
        # ⚠️ 必须用 result.won，**不要写 outcome == OUTCOME_WIN**。
        # 战斗结果有五档，其中三档都算赢：
        #   crush（碾压）/ win（胜利）/ narrow（惨胜）
        # 只认 OUTCOME_WIN 会把碾压和惨胜全漏掉 —— 而碾压恰恰是最常见的结果，
        # 于是「打了一晚上一次没涨」。这是端到端验证抓出来的，
        # 纯单元测试（直接调 advance_passives）永远发现不了。
        kills = 1 if result.won else 0
        try:
            return special_mod.advance_passives(
                player, kills=kills, died=result.outcome == battle.OUTCOME_LOSE,
            )
        except Exception as exc:  # 成长系统不该拖垮战斗结算
            logger.warning(f"[textrpg] 特殊装备成长结算失败：{exc!r}")
            return []

    @gate_session(_SESSION_GATE)
    @filter.command("恢复", alias={"复活"})
    async def revive(self, event: AstrMessageEvent, choice: str = ""):
        """闭关疗伤中：选择立即恢复的方式。（别名 /复活）"""
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        if not player.is_resting():
            async for _chunk in self._reply(event, "你道途顺畅，无需疗伤。"):
                yield _chunk
            return

        gold_cost = self.death_revive_gold
        potion = self._find_heal_item(player)

        # 没带参数 -> 展示三个选项
        if not choice:
            lines = [
                f"🩹 闭关疗伤中，剩余 {_fmt_duration(player.rest_remain())}",
                "",
                "选择恢复方式（回复数字）：",
                f"1. 继续疗伤 —— 免费，等待剩余时间结束",
                f"2. 服下丹药 —— 消耗 1 颗{potion or '回气丹'}"
                + ("" if potion else "（储物袋里没有）"),
                f"3. 灵石续命 —— 花费 {gold_cost} 灵石"
                + ("" if player.gold >= gold_cost else f"（你有 {player.gold}，不够）"),
            ]
            async for _chunk in self._reply(event, "\n".join(lines)):
                yield _chunk
            return

        pick = choice.strip()

        if pick == "1":
            async for _chunk in self._reply(
                event,
                f"好的，继续闭关疗伤。剩余 {_fmt_duration(player.rest_remain())}\n"
                f"结束前可发送 /属性 查看状态，但不能历练。",
            ):
                yield _chunk
            return

        if pick == "2":
            if not potion:
                async for _chunk in self._reply(
                    event, "储物袋里没有回气丹，去 /坊市 买几颗吧。"
                ):
                    yield _chunk
                return
            player.remove_item(potion)
            player.revive_now(self.item_db)
            await self._commit()
            async for _chunk in self._reply(
                event,
                f"🧪 你服下 {potion}，伤势立刻痊愈！\n"
                f"当前气血：{player.hp}/{player.max_hp(self.item_db)}\n"
                f"现在可以继续 /历练 了。",
            ):
                yield _chunk
            return

        if pick == "3":
            if player.gold < gold_cost:
                async for _chunk in self._reply(
                    event, f"灵石不足，需要 {gold_cost}，你只有 {player.gold}。"
                ):
                    yield _chunk
                return
            player.gold -= gold_cost
            player.revive_now(self.item_db)
            await self._commit()
            async for _chunk in self._reply(
                event,
                f"💰 支付 {gold_cost} 灵石，伤势痊愈！\n"
                f"当前气血：{player.hp}/{player.max_hp(self.item_db)}\n"
                f"剩余灵石：{player.gold}",
            ):
                yield _chunk
            return

        async for _chunk in self._reply(event, "请回复 1 / 2 / 3 选择恢复方式。"):
            yield _chunk

    def _find_heal_item(self, player: Player) -> str | None:
        """找一个回血类药水，优先最便宜的。"""
        for name in HEAL_ITEMS:
            if player.inventory.get(name, 0) > 0:
                return name
        return None

    def _format_battle(
        self, player: Player, result: battle.BattleResult,
        growth_lines: list[str] | None = None,
    ) -> str:
        """把战斗结果拼成一条消息。

        growth_lines：特殊装备（帝兵）本次的成长提示，由 `_apply_death` 产出。
        显式传参而不是塞在 player 上，避免「状态藏在对象里」的隐式耦合。
        """
        lines = [f"⚔️ 遭遇 {result.monster_name}"]
        max_hp = player.max_hp(self.item_db)

        if result.skills_used:
            lines.append("施展：" + "、".join(result.skills_used))

        if result.won:
            lines.append(f"✅ {result.label}！用了 {result.turns} 回合")
            lines.append(f"获得修为 {result.exp_gain}，灵石 {result.gold_gain}")
            if result.drop:
                # 掉落物带品质，高亮一下稀有度
                base, q = split_quality(result.drop)
                if q != "common":
                    lines.append(f"掉落：{result.drop} ✨")
                else:
                    lines.append(f"掉落：{result.drop}")
            elif result.drop_lost:
                lines.append("掉落了物品，但储物袋已满，没能拿到。")
            if result.leveled_up:
                lines.append(
                    f"⬆️ 精进！连进 {result.levels_gained} 转，"
                    f"当前 {player.realm_label}，气血已回满"
                )
        elif result.outcome == battle.OUTCOME_LOSE:
            lines.append(f"💀 道消。你在第 {result.turns} 回合倒下了")
            # 没有惩罚，只提示疗伤
            rest = _fmt_duration(player.rest_remain())
            lines.append("")
            lines.append(f"你需闭关疗伤 {rest}。")
            lines.append("疗伤期间不能历练，但可以查看储物袋、用丹药、买卖东西。")
            lines.append("发送 /恢复 可以选择立即痊愈的方式。")
        else:
            lines.append("🤝 势均力敌，双方都没能压倒对方")
            lines.append(f"修为 +{result.exp_gain}（安慰奖）")

        if result.outcome != battle.OUTCOME_LOSE:
            lines.append(f"当前气血：{result.hp_after}/{max_hp}")

        # 特殊装备成长（帝兵）—— 放最后，避免打断战斗叙事的节奏
        for gl in (growth_lines or []):
            lines.append(f"☄️ {gl}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 装备指令
    # ------------------------------------------------------------------

    @gate_session(_SESSION_GATE)
    @filter.command("装备", alias={"祭炼"})
    async def equip(self, event: AstrMessageEvent, item_name: str):
        """祭炼（穿戴）一件法宝，例如 /装备 青锋剑。

        名字解析规则（v3.4）：
          * 玩家可以只写基础名（`青锋剑`）—— 会自动匹配身上任意品质的那件
          * 也可以写全名（`青锋剑[稀]`）—— 精确匹配
          * 旧名（`铁剑`）照样认（走 ITEM_ALIASES 映射）

        找不到「同基础名」的装备时按「你没有」处理，不报「不存在」——
        因为对玩家来说两者的可操作结论一样，区分反而让人困惑。
        """
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        item = get_item(item_name)
        if item is None or item.get("kind") != "equipment":
            async for _chunk in self._reply(event, f"储物袋里没有可祭炼的「{item_name}」。"):
                yield _chunk
            return

        # ---- 定位储物袋里真正的名字（可能是带品质后缀的）----
        #
        # 【为什么需要这一步】掉落/坊市产出的装备名是**带品质后缀**的
        # （`青锋剑[稀]`），而玩家输入十有八九是基础名（`青锋剑`）。
        # 直接 inventory.get(item_name) 会因为名字不完全相等而查不到，
        # 玩家明明有装备却被告知「你没有」—— 这是最招骂的一类 bug。
        #
        # 匹配逻辑统一放在 player.find_inventory_name（品质高者优先：
        # 玩家说「装上剑」时显然想装好的那件）。
        actual_name = player.find_inventory_name(item_name)
        if actual_name is None:
            async for _chunk in self._reply(event, f"你没有「{item_name}」。"):
                yield _chunk
            return

        # ---- 品阶门槛校验（v3.4 新增）----
        #
        # 老板定的「按品阶设转数门槛」：一品 1 转一路到九品 68 转。
        # 没有这道闸，1 转新手可以直接穿上九品毕业装，
        # 十品体系辛苦搭的成长曲线会被一次性跳过。
        #
        # 判定走 items.can_equip —— **不要在这里手写 turns >= req_turns**。
        # 理由：帝兵是「无阶位限制」的例外（它没有 req_turns 字段），
        # 手写比较会把帝兵一起拦掉。can_equip 内部已经处理了这个例外，
        # 两处各写一遍迟早分叉。
        if not can_equip(actual_name, player.turns):
            req = req_turns_of(actual_name)
            tier = tier_rank_of(actual_name)
            need = req - player.turns
            async for _chunk in self._reply(
                event,
                f"以你如今 {player.turns} 转的修为，还压不住 {tier} 品法宝的灵性。\n"
                f"需再精进 {need} 转（{req} 转方可祭炼），强行认主必遭反噬。",
            ):
                yield _chunk
            return

        slot = item["slot"]
        slot_label = SLOT_LABELS.get(slot, slot)
        old = player.equipment.get(slot)

        player.remove_item(actual_name)
        player.equipment[slot] = actual_name

        # 换装后气血上限可能变化，做一次钳制
        max_hp = player.max_hp(self.item_db)
        if player.hp > max_hp:
            player.hp = max_hp

        await self._commit()

        msg = f"已祭炼 {actual_name}（{slot_label}）"
        if old:
            player.add_item(old, limit=self.inventory_limit)
            await self._commit()
            msg += f"\n换下的 {old} 已放回储物袋"
        async for _chunk in self._reply(event, msg):
            yield _chunk

    @gate_session(_SESSION_GATE)
    @filter.command("卸下")
    async def unequip(self, event: AstrMessageEvent, slot_name: str):
        """卸下某个部位的法宝，例如 /卸下 法宝（也可用 武器 / 护甲 / 饰品）。"""
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        # 支持中文部位名、修仙别名和英文 key
        slot = SLOT_ALIASES.get(slot_name)
        if slot is None and slot_name in SLOT_LABELS:
            slot = slot_name

        if slot is None:
            usable = "、".join(SLOT_LABELS.values())
            async for _chunk in self._reply(
                event, f"没有这个部位。可用：{usable}"
            ):
                yield _chunk
            return

        item_name = player.equipment.get(slot)
        if not item_name:
            async for _chunk in self._reply(event, f"{SLOT_LABELS[slot]}上没有法宝。"):
                yield _chunk
            return

        if not player.add_item(item_name, limit=self.inventory_limit):
            async for _chunk in self._reply(event, "储物袋已满，先清理一下再卸下。"):
                yield _chunk
            return

        del player.equipment[slot]
        max_hp = player.max_hp(self.item_db)
        if player.hp > max_hp:
            player.hp = max_hp

        await self._commit()
        async for _chunk in self._reply(event, f"已卸下 {item_name}，放回储物袋。"):
            yield _chunk

    # ------------------------------------------------------------------
    # 特殊装备（帝兵）指令
    # ------------------------------------------------------------------

    @gate_session(_SESSION_GATE)
    @filter.command("特殊")
    async def cmd_special(self, event: AstrMessageEvent, action: str = ""):
        """【特殊】栏：查看 / 认主 / 卸下帝兵。

        用法：
            /特殊            查看当前帝兵与成长进度
            /特殊 认主 <id>   把一件帝兵认主（穿戴）
            /特殊 卸下        卸下帝兵（成长进度**保留**）
            /特殊 列表        看当前可获取的帝兵

        【为什么是「认主」不是「装备」】
        帝兵不占 5 个普通部位，走的是独立【特殊】栏，
        语义上和「穿装备」是两回事（无阶位限制、可成长、全服限定）。
        用不同的词，玩家一眼就知道这不是普通装备操作。
        """
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        action = (action or "").strip()

        # ---- /特殊 列表 ----
        #
        # 【v3.5 改动】原先是列「天道兵库」（读 lottery_pool）。
        # 抽奖废弃后改读 redeem_preview() —— 展示成
        # 「进度条 + 可兑换清单」，玩家能看见确定的进度（今天 73/100）。
        # 这比「再抽一次试试看」有粘性得多，也是换掉抽奖的核心理由。
        if action in ("列表", "图鉴", "池", "兑换"):
            async for _chunk in self._reply(
                event, special_mod.redeem_preview(player)
            ):
                yield _chunk
            return

        # ---- /特殊 卸下 ----
        if action == "卸下":
            if not getattr(player, "special", ""):
                async for _chunk in self._reply(event, "你身上没有特殊法宝。"):
                    yield _chunk
                return
            spec = special_mod.find_special(player.special)
            name = spec.get("name") if spec else player.special
            # 成长进度**保留** —— 帝兵认主后成长属于你自己，
            # 卸下只是暂时不用，不该清零（清零会让玩家不敢换装）
            player.special = ""
            await self._commit()
            async for _chunk in self._reply(
                event,
                f"已卸下 {name}。\n"
                f"成长进度已保留（当前 +{float(player.special_growth_pct or 0):.1f}%），"
                f"重新认主后继续累积。",
            ):
                yield _chunk
            return

        # ---- /特殊 认主 <id> ----
        if action in ("认主", "装备", "祭炼"):
            async for _chunk in self._reply(
                event, "用法：/特殊 认主 <帝兵 id>（可用 /特殊 列表 查看）"
            ):
                yield _chunk
            return

        if action.startswith("认主") or action.startswith("装备"):
            spec_id = action[2:].strip()
            if not spec_id:
                async for _chunk in self._reply(
                    event, "用法：/特殊 认主 <帝兵 id>（可用 /特殊 列表 查看）"
                ):
                    yield _chunk
                return
            async for _chunk in self._equip_special(event, player, spec_id):
                yield _chunk
            return

        # ---- /特殊（无参数）看展示 ----
        if not getattr(player, "special", ""):
            async for _chunk in self._reply(
                event,
                "你尚未得帝兵认主。\n"
                "帝兵不占法宝位、无境界门槛，还能随弑杀成长 ——\n"
                "以 100 枚天道令兑换可得。发 /特殊 列表 看兑换进度。",
            ):
                yield _chunk
            return

        rendered = special_mod.render_special_slot(player)
        if rendered is None:
            async for _chunk in self._reply(event, "特殊法宝数据异常，请找主人看看。"):
                yield _chunk
            return
        async for _chunk in self._reply(event, rendered):
            yield _chunk

    async def _equip_special(self, event, player, spec_id: str):
        """把一件帝兵认主（穿戴）。抽成独立方法，方便以后从别处调用。"""
        spec = special_mod.find_special(spec_id)
        if spec is None:
            async for _chunk in self._reply(
                event, f"天道之上没有「{spec_id}」这样的兵刃。"
            ):
                yield _chunk
            return

        # 【v3.5 改动：认主前必须已兑换】
        #
        # 帝兵从「抽奖白得」改成「100 枚天道令兑换」之后，
        # `/特殊 认主` 不能再「白认」—— 否则整个天道令体系被一行指令绕过。
        #
        # 判据用 `special_owned`（永久流水）而不是 `player.special`：
        # 后者会被 `/特殊 卸下` 清空，用它会导致「卸下后就再也装不回去」。
        #
        # 原来那段 `unique + owner` 的「全服限定」校验已删除 ——
        # 帝兵去唯一化后 `unique` 恒为 False，那段逻辑永不触发，是死代码。
        if not special_mod.owns_special(player, spec_id):
            async for _chunk in self._reply(
                event,
                f"你尚未兑换 {special_mod.special_display_name(spec)}。\n"
                f"帝兵需以 100 枚天道令兑换 —— 发送 /特殊 列表 查看兑换进度。\n"
                f"（天道令来自：每日 /签到 +1 枚、秘境首通 +1 枚）",
            ):
                yield _chunk
            return

        old = getattr(player, "special", "") or ""
        player.special = spec_id
        await self._commit()

        # 换装后气血上限可能变化，钳制一下
        max_hp = player.max_hp(self.item_db)
        if player.hp > max_hp:
            player.hp = max_hp

        msg = f"{spec.get('name')} 认你为主。"
        if old and old != spec_id:
            old_spec = special_mod.find_special(old)
            msg += f"\n（已卸下 {old_spec.get('name') if old_spec else old}）"
        rendered = special_mod.render_special_slot(player)
        if rendered:
            msg += "\n\n" + rendered
        async for _chunk in self._reply(event, msg):
            yield _chunk

    # ------------------------------------------------------------------
    # 天道令：签到 / 兑换（v3.5 新增）
    # ------------------------------------------------------------------
    #
    # 【这套东西解决什么】
    # 帝兵的获取方式从「天道抽奖」改成「攒 100 枚天道令兑换」。
    # 抽奖废弃的理由见 special.py 模块头注释第 5 条 ——
    # 一句话：**确定的进度条比概率更有粘性**。
    #
    # 【产出的两个口子】
    #   /签到        每天 1 枚（跨零点重置）
    #   秘境首通      单趟 1 枚（在 dungeon.run_dungeon 里发，不在这里）

    @gate_session(_SESSION_GATE)
    @filter.command("签到", alias={"打卡"})
    async def cmd_sign_in(self, event: AstrMessageEvent):
        """每日签到，领 1 枚天道令。

        【为什么判定用日期字符串而不是时间戳】
        玩家要的是「每天一次」，不是「距上次满 24 小时」。
        23:59 签完，00:01 就该能再签一次。用时间戳会变成
        「要等满 24 小时」，玩家会觉得被坑。
        （和 /秘境 的每日判定同一套写法，直接复用 dungeon.today_str。）
        """
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        today = dungeon_mod.today_str()
        if str(getattr(player, "sign_in_date", "") or "") == today:
            have = special_mod.token_count(player)
            async for _chunk in self._reply(
                event,
                f"今日已签到，明天再来。\n"
                f"天道令：{have} / {special_mod.TOKEN_COST}",
            ):
                yield _chunk
            return

        # ---- 天道令：基础 + VIP 加成 ----
        #
        # 【为什么加成挂在这里】
        # 签到本来就有「一天一次」的保护（sign_in_date 跨零点重置），
        # VIP 加成挂进来就**自动继承**这层防刷 —— 一行防刷代码都不用写。
        # 见 vip.py 模块头注释的详细论证。
        base = special_mod.TOKEN_FROM_SIGNIN
        bonus = vip_mod.daily_token_bonus(player.qq)
        gained = base + bonus

        player.sign_in_date = today
        granted = special_mod.grant_token(player, gained, self.inventory_limit)
        await self._commit()

        have = special_mod.token_count(player)
        lines = [
            f"📅 签到成功（{today}）",
            f"获得 天道令 ×{granted}",
        ]
        if bonus:
            lines.append(
                f"（含【{vip_mod.title_of(player.qq)}】额外 {bonus} 枚）"
            )
        lines.append(f"天道令：{have} / {special_mod.TOKEN_COST}")

        # 到期前 3 天提醒续费 —— 避免「悄悄过期」的失落感，也提升续费率
        _left = vip_mod.expiring_soon(player.qq)
        if _left:
            lines.append(f"⏳ 你的 VIP 还有 {_left} 天到期。")

        if have >= special_mod.TOKEN_COST:
            lines.append("")
            lines.append("天道令已满，发送 /特殊 列表 兑换帝兵。")
        else:
            lines.append(f"（还差 {special_mod.TOKEN_COST - have} 枚可换帝兵）")
        async for _chunk in self._reply(event, "\n".join(lines)):
            yield _chunk

    @gate_session(_SESSION_GATE)
    @filter.command("兑换")
    async def cmd_redeem(self, event: AstrMessageEvent, spec_id: str = ""):
        """以 100 枚天道令兑换一件帝兵。

        用法：
            /兑换              看兑换进度（同 /特殊 列表）
            /兑换 <帝兵id>      执行兑换，例如 /兑换 dibing_000

        【扣款顺序】先校验 → 再扣令 → 最后写归属（都在 special.redeem 里）。
        这个顺序保证任何一步失败都不会出现「令扣了但装备没到手」。
        """
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        spec_id = (spec_id or "").strip()
        if not spec_id:
            async for _chunk in self._reply(
                event, special_mod.redeem_preview(player)
            ):
                yield _chunk
            return

        ok, msg, spec = special_mod.redeem(player, spec_id)
        if not ok:
            async for _chunk in self._reply(event, msg):
                yield _chunk
            return

        # 先钳气血再落盘 —— 兑换后全属性变高，上限跟着变。
        # （顺序别反了：先 commit 再钳，这次钳的值就要等下次操作才落盘。）
        try:
            max_hp = player.max_hp(self.item_db)
            if player.hp > max_hp:
                player.hp = max_hp
        except Exception:  # noqa: BLE001 - 面板取不到不该挡兑换
            pass
        await self._commit()

        out = f"⚜️ {msg}"
        rendered = special_mod.render_special_slot(player)
        if rendered:
            out += "\n\n" + rendered
        async for _chunk in self._reply(event, out):
            yield _chunk

    # ------------------------------------------------------------------
    # VIP（赞助者）指令（v3.5 新增）
    # ------------------------------------------------------------------
    #
    # 【两条硬约束，写在代码最前面】
    #   1. VIP **只能由主人（天道）发放** —— 无自助购买、无卡密、
    #      无消费自动升级。发放权只在老板手上。
    #   2. VIP **不碰战斗数值** —— 只加速「攒天道令 → 换帝兵」，
    #      给时间不给战力。加战力会让免费玩家变成陪玩，小社区经不起。
    #
    # 【为什么用「精确指令」而不是 /vip 一堆子命令】
    # 老板的要求：发 VIP 要**一步到位**，不要敲一长串参数。所以主入口是
    # 两条独立短指令，一条一个等级，名字本身就是「精确」的：
    #
    #     /激活vip <QQ>    普通 VIP（10 元/月），默认 1 个月
    #     /至尊vip <QQ>    至尊 VIP（30 元/月），默认 1 个月
    #
    # 【为什么 /激活vip 不会被已有的 /激活 吞掉 —— 查过源码】
    # `astrbot/core/star/filter/command.py:202` 的匹配条件是：
    #
    #     message_str.startswith(f"{full_cmd} ") or message_str == full_cmd
    #
    # 即**命令名后面必须跟空格，或者整串完全相等**。
    # `/激活vip 123` 既不等于 `激活`，也不以 `激活 ` 开头 —— 不会误触。
    #
    # 这条必须查源码确认，不能凭感觉：如果框架用的是裸 `startswith(cmd)`，
    # `/激活vip 123` 会被 `/激活` 吃掉，参数变成 "vip 123"，
    # 玩家的 QQ 号就发不出去了。这是个会静默失效的坑。
    #
    # 【斜杠能不能省 —— 能，但那是 AstrBot 的全局机制】
    # 插件侧注册的名字**本来就不含斜杠**：`wake_prefix`（默认 `["/"]`）
    # 在匹配前就被剥掉了（`core/pipeline/waking_check/stage.py:130`）。
    # 所以：
    #   * 群里：`/激活vip 123`，或者 `@机器人 激活vip 123`
    #   * 私聊 / 被 @ 时：`激活vip 123` 也可以（此时 wake_prefix 为空）
    # 群里直接发「激活vip 123」且不 @ 机器人 —— **不会触发**，
    # 因为 AstrBot 要先确认这条消息是在跟机器人说话。
    # 这是平台层的唤醒规则，插件改不了（也不该绕）。

    @gate_session(_SESSION_GATE)
    @filter.command("激活vip")
    async def cmd_vip_normal(self, event: AstrMessageEvent, qq: str = ""):
        """发放普通 VIP（10 元/月，默认 1 个月）。仅天道。

        用法：/激活vip <QQ号>
        """
        async for _chunk in self._vip_grant_flow(
            event, qq, vip_mod.TIER_NORMAL, 1, "",
            usage=(
                "用法：/激活vip <QQ号>\n"
                "例：/激活vip 12345678\n"
                f"（普通 VIP {vip_mod.TIER_PRICES[vip_mod.TIER_NORMAL]} 元/月，"
                f"默认 1 个月）"
            ),
        ):
            yield _chunk

    @gate_session(_SESSION_GATE)
    @filter.command("至尊vip")
    async def cmd_vip_supreme(self, event: AstrMessageEvent, qq: str = ""):
        """发放至尊 VIP（30 元/月，默认 1 个月）。仅天道。

        用法：/至尊vip <QQ号>
        """
        async for _chunk in self._vip_grant_flow(
            event, qq, vip_mod.TIER_SUPREME, 1, "",
            usage=(
                "用法：/至尊vip <QQ号>\n"
                "例：/至尊vip 12345678\n"
                f"（至尊 VIP {vip_mod.TIER_PRICES[vip_mod.TIER_SUPREME]} 元/月，"
                f"默认 1 个月）"
            ),
        ):
            yield _chunk

    async def _vip_grant_flow(self, event, qq, tier, months, note, usage: str = ""):
        """发放 VIP 的统一流程 —— 三条入口共用。

        【为什么必须抽出来】
        `/激活vip` `/至尊vip` `/vip 开通` 做的是同一件事
        （校验权限 → 发放 → 公告），只是参数来源不同。
        抽成一条流程，就不会出现「某个入口忘了校验权限」这种漏洞 ——
        权限检查只有一处，改也只改一处。
        """
        # 权限：所有发放入口都只给天道
        if not self._is_admin(event):
            async for _chunk in self._reply(
                event,
                "此令只应天道亲传。\n"
                "（VIP 只能由主人发放 —— 无自助购买、无卡密）\n"
                "发送 /vip 可以查看价目与自己的状态。",
            ):
                yield _chunk
            return

        qq = str(qq or "").strip()
        if not qq:
            async for _chunk in self._reply(
                event, usage or "用法：/激活vip <QQ号>"
            ):
                yield _chunk
            return

        ok, why, rec = vip_mod.grant(qq, months, tier, note)
        if not ok:
            async for _chunk in self._reply(event, f"发放失败：{why}"):
                yield _chunk
            return

        async for _chunk in self._reply(
            event, self._vip_bless_text(qq, tier, rec, why)
        ):
            yield _chunk

    @staticmethod
    def _vip_bless_text(qq: str, tier: str, rec: dict, extra: str = "") -> str:
        """「天道垂青」公告。

        【为什么包装成世界观动作，而不是「已开通 VIP」】
        付费玩家买到的社交价值翻倍（全群都看见），免费玩家看到的是
        「有人受天道眷顾」而不是「有人充钱了」——
        前者是世界观，后者是商业化，观感差别很大。
        """
        bonus = vip_mod.TIER_TOKEN_BONUS.get(tier, 0)
        price = vip_mod.TIER_PRICES.get(tier, 0)
        lines = [
            "⚜️ 天道垂青",
            f"　道友「{qq}」以诚心供奉，自今日起受天道眷顾。",
            f"　尊号：{vip_mod.tier_label(tier)}（{price} 元/月）",
            f"　每日签到额外得天道令 {bonus} 枚。",
            f"　有效期至 {vip_mod.format_date(rec.get('expire_ts', 0))}。",
        ]
        if extra:
            lines.append(f"　（{extra}）")
        lines.append("")
        lines.append("（此乃天道回馈 —— 只加速攒令，不影响战斗数值。）")
        return "\n".join(lines)

    @gate_session(_SESSION_GATE)
    @filter.command("vip", alias={"赞助"})
    async def cmd_vip(self, event: AstrMessageEvent, action: str = ""):
        """VIP（赞助者）：价目 / 查询 / 列表 / 撤销。

        用法：
            /vip                      价目表 + 自己的状态（所有人）
            /vip 查询 <QQ>             查某人的状态（所有人）
            /vip 列表                  列有效赞助者（仅天道）
            /vip 撤销 <QQ>             立即失效（仅天道）
            /vip 开通 <QQ> <月数> [normal|supreme] [备注]（仅天道）

        发放的主入口是 `/激活vip <QQ>` 与 `/至尊vip <QQ>`（各 1 个月）；
        `/vip 开通` 保留给「多月份 / 要写备注」的场景。
        """
        parts = (action or "").split()
        sub = parts[0] if parts else ""

        # ---- 价目表 + 自己的状态：所有人可用 ----
        #
        # 老板要求 `/vip` 一眼能看到「每个 vip 价格 + 是否激活」。
        if sub in ("", "价目", "价格", "价目表"):
            async for _chunk in self._reply(
                event, self._vip_price_text(str(event.get_sender_id()))
            ):
                yield _chunk
            return

        # ---- /vip 查询 [QQ]：所有人可用 ----
        if sub in ("查询", "查", "状态"):
            target = parts[1] if len(parts) > 1 else str(event.get_sender_id())
            async for _chunk in self._reply(event, self._vip_status_text(target)):
                yield _chunk
            return

        # ---- 以下全部仅天道 ----
        if not self._is_admin(event):
            async for _chunk in self._reply(
                event,
                "此令只应天道亲传。\n"
                "（VIP 只能由主人发放 —— 无自助购买、无卡密）\n"
                "发送 /vip 可以查看价目与自己的状态。",
            ):
                yield _chunk
            return

        # ---- /vip 列表 ----
        if sub in ("列表", "名单", "list"):
            rows = vip_mod.list_vips()
            if not rows:
                async for _chunk in self._reply(event, "当前没有有效的 VIP。"):
                    yield _chunk
                return
            lines = [f"⚜️ 天道眷顾者（{len(rows)} 人）"]
            for qq, rec, left in rows:
                line = (
                    f"　{qq}　{vip_mod.tier_label(rec.get('tier'))}"
                    f"　剩 {left} 天"
                    f"（至 {vip_mod.format_date(rec.get('expire_ts', 0))}）"
                )
                if rec.get("note"):
                    line += f"　备注：{rec['note']}"
                lines.append(line)
            async for _chunk in self._reply(event, "\n".join(lines)):
                yield _chunk
            return

        # ---- /vip 开通 <QQ> <月数> [等级] [备注] ----
        #
        # 主入口是 /激活vip /至尊vip（各 1 个月）。这条留给多月份 / 备注。
        if sub in ("开通", "发放", "赐福", "add"):
            usage = (
                "用法：/vip 开通 <QQ> <月数> [normal|supreme] [备注]\n"
                "例：/vip 开通 12345678 3 supreme 微信0919\n"
                "（只发 1 个月用 /激活vip 或 /至尊vip 更快）"
            )
            if len(parts) < 3:
                async for _chunk in self._reply(event, usage):
                    yield _chunk
                return

            qq, months_raw = parts[1], parts[2]
            tier = vip_mod.TIER_NORMAL
            note_parts: list[str] = []
            if len(parts) > 3:
                maybe = parts[3].lower()
                alias = {
                    "普通": vip_mod.TIER_NORMAL,
                    "至尊": vip_mod.TIER_SUPREME,
                }
                if maybe in (vip_mod.TIER_NORMAL, vip_mod.TIER_SUPREME):
                    tier = maybe
                    note_parts = parts[4:]
                elif maybe in alias:
                    tier = alias[maybe]
                    note_parts = parts[4:]
                else:
                    note_parts = parts[3:]
            note = " ".join(note_parts)

            async for _chunk in self._vip_grant_flow(
                event, qq, tier, months_raw, note, usage=usage
            ):
                yield _chunk
            return

        # ---- /vip 撤销 <QQ> ----
        if sub in ("撤销", "取消", "revoke", "del"):
            if len(parts) < 2:
                async for _chunk in self._reply(event, "用法：/vip 撤销 <QQ>"):
                    yield _chunk
                return
            target = parts[1]
            ok, why = vip_mod.revoke(target)
            if ok:
                async for _chunk in self._reply(
                    event, f"已撤销 {target} 的 VIP，立即失效。"
                ):
                    yield _chunk
            else:
                async for _chunk in self._reply(event, f"撤销失败：{why}"):
                    yield _chunk
            return

        async for _chunk in self._reply(event, self._vip_help_text()):
            yield _chunk

    @staticmethod
    def _vip_price_text(qq: str) -> str:
        """价目表 + 某人当前是否激活。

        老板要求 `/vip` 一眼看到「每个 vip 价格 + 是否激活」，
        所以这一段把两档价目和自己的状态放在同一条回复里。
        """
        lines = ["⚜️ VIP（赞助者）· 价目", ""]
        for tier in (vip_mod.TIER_NORMAL, vip_mod.TIER_SUPREME):
            price = vip_mod.TIER_PRICES.get(tier, 0)
            bonus = vip_mod.TIER_TOKEN_BONUS.get(tier, 0)
            lines.append(
                f"　{vip_mod.tier_label(tier)}　{price} 元/月"
                f"　每日签到额外 +{bonus} 枚天道令"
            )
        lines.append("")

        qq = str(qq or "").strip()
        tier = vip_mod.vip_tier(qq)
        rec = vip_mod.record(qq) or {}
        if tier:
            lines.append(
                f"你的状态：✅ 已激活 · {vip_mod.tier_label(tier)}"
                f"　剩余 {vip_mod.days_left(qq)} 天"
                f"（至 {vip_mod.format_date(rec.get('expire_ts', 0))}）"
            )
        elif rec:
            lines.append(
                f"你的状态：⌛ 已到期"
                f"（{vip_mod.format_date(rec.get('expire_ts', 0))}）"
                f"　累计赞助 {rec.get('total_months', 0)} 个月"
            )
        else:
            lines.append("你的状态：❌ 未激活")

        lines.append("")
        lines.append("VIP 只能由主人（天道）发放，无自助购买。")
        lines.append("（VIP 只加速攒天道令，不影响战斗数值）")
        return "\n".join(lines)

    def _vip_status_text(self, qq: str) -> str:
        """渲染**某人**的 VIP 状态。含已过期的历史记录（提示续费）。"""
        qq = str(qq or "").strip()
        tier = vip_mod.vip_tier(qq)
        rec = vip_mod.record(qq) or {}

        if not tier:
            if rec:
                return (
                    f"「{qq}」的赞助已于 "
                    f"{vip_mod.format_date(rec.get('expire_ts', 0))} 到期。\n"
                    f"（累计赞助 {rec.get('total_months', 0)} 个月）\n"
                    f"续费请联系天道。"
                )
            return (
                f"「{qq}」当前没有 VIP 状态。\n"
                "VIP 是赞助回馈：每日签到额外得天道令，不影响战斗数值。\n"
                "（VIP 只能由主人（天道）发放，无自助购买）"
            )

        bonus = vip_mod.TIER_TOKEN_BONUS.get(tier, 0)
        price = vip_mod.TIER_PRICES.get(tier, 0)
        return (
            f"⚜️ 尊号：{vip_mod.tier_label(tier)}（{price} 元/月）\n"
            f"每日签到额外得天道令 {bonus} 枚\n"
            f"剩余 {vip_mod.days_left(qq)} 天"
            f"（至 {vip_mod.format_date(rec.get('expire_ts', 0))}）\n"
            f"累计赞助 {rec.get('total_months', 0)} 个月\n"
            "（VIP 只加速攒天道令，不影响战斗数值）"
        )

    @staticmethod
    def _vip_help_text() -> str:
        normal_cmd = vip_mod.GRANT_COMMANDS[vip_mod.TIER_NORMAL]
        supreme_cmd = vip_mod.GRANT_COMMANDS[vip_mod.TIER_SUPREME]
        n_price = vip_mod.TIER_PRICES[vip_mod.TIER_NORMAL]
        s_price = vip_mod.TIER_PRICES[vip_mod.TIER_SUPREME]
        return (
            "⚜️ VIP（赞助者）\n"
            "\n"
            "【价目】\n"
            f"　普通 VIP　{n_price} 元/月"
            f"　每日签到额外 +{vip_mod.TIER_TOKEN_BONUS[vip_mod.TIER_NORMAL]} 枚天道令\n"
            f"　至尊 VIP　{s_price} 元/月"
            f"　每日签到额外 +{vip_mod.TIER_TOKEN_BONUS[vip_mod.TIER_SUPREME]} 枚天道令\n"
            "\n"
            "【查询】所有人可用\n"
            "　/vip　　　　　　价目 + 自己的状态\n"
            "　/vip 查询 <QQ>　 查某人的状态\n"
            "\n"
            "【发放】仅天道\n"
            f"　{normal_cmd} <QQ>　　发普通 VIP（1 个月）\n"
            f"　{supreme_cmd} <QQ>　　发至尊 VIP（1 个月）\n"
            "　/vip 开通 <QQ> <月数> [normal|supreme] [备注]　多月份用\n"
            "　/vip 列表　　　　　列有效赞助者\n"
            "　/vip 撤销 <QQ>　　 立即失效\n"
            "\n"
            "VIP 不碰战斗数值 —— 给时间，不给战力。\n"
            "（加成随签到发放：当天不签到，当天加成拿不到）"
        )

    @gate_session(_SESSION_GATE)
    @filter.command("使用")
    async def use_item(self, event: AstrMessageEvent, item_name: str):
        """服用丹药，例如 /使用 小回气丹。"""
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        item = get_item(item_name)
        if item is None or item.get("kind") != "consumable":
            async for _chunk in self._reply(event, f"「{item_name}」不是可服用的物品。"):
                yield _chunk
            return

        # 秘典残页要用 /修行 消化，不能当丹药吃
        if item.get("effect") == "relic":
            async for _chunk in self._reply(
                event,
                f"「{item_name}」是秘典残页，不能直接服用。\n"
                f"用 /修行 <秘典名> 把它修成大道。",
            ):
                yield _chunk
            return

        # 【v3.5】天道令是兑换凭证，不是丹药。
        # 不拦的话玩家会以为它是某种能吃的道具，白白浪费一天产出。
        if item.get("effect") == "token":
            async for _chunk in self._reply(
                event,
                f"「{item_name}」是天道令，不能服用。\n"
                f"攒够 {special_mod.TOKEN_COST} 枚可兑换一件帝兵 —— "
                f"发送 /特殊 列表 查看进度。",
            ):
                yield _chunk
            return

        if player.inventory.get(item_name, 0) < 1:
            # 玩家输入的大多是基础名（`小回气丹`），而储物袋里存的是
            # 带品质后缀的完整名（`小回气丹[普]`）。这里统一走解析，
            # 否则「明明有丹药却被告知没有」。
            _actual = player.find_inventory_name(item_name)
            if _actual is None:
                async for _chunk in self._reply(event, f"你没有「{item_name}」。"):
                    yield _chunk
                return
            item_name = _actual

        max_hp = player.max_hp(self.item_db)
        if item["effect"] == "heal":
            if player.hp >= max_hp:
                async for _chunk in self._reply(event, "气血已满，不用浪费丹药。"):
                    yield _chunk
                return
            healed = min(item["value"], max_hp - player.hp)
            player.hp += healed
            player.remove_item(item_name)
            await self._commit()
            async for _chunk in self._reply(
                event, f"恢复了 {healed} 点气血。当前气血：{player.hp}/{max_hp}"
            ):
                yield _chunk
        elif item["effect"] == "exp":
            player.exp += item["value"]
            player.remove_item(item_name)
            levels = battle._apply_level_up(player, self.item_db)
            await self._commit()
            msg = f"获得 {item['value']} 点修为。"
            if levels:
                msg += f"\n⬆️ 精进！当前 {player.realm_label}"
            async for _chunk in self._reply(event, msg):
                yield _chunk
        elif item["effect"] == "tribulation":
            async for _chunk in self._reply(
                event,
                "渡劫丹不用手动服用。\n"
                "渡天劫时（/突破）它会自动生效，每颗能让你多扛一道雷。",
            ):
                yield _chunk
        else:
            async for _chunk in self._reply(event, "这颗丹药暂时无法服用。"):
                yield _chunk

    @gate_session(_SESSION_GATE)
    @filter.command("出售")
    async def sell(self, event: AstrMessageEvent, item_name: str):
        """出售储物袋物品换灵石，例如 /出售 史莱姆凝胶。"""
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        # 名字解析：输入可能是基础名，储物袋里存的是带品质后缀的完整名。
        # 卖掉时**优先卖品质最低的**（prefer_quality=False）——
        # 这是唯一与「装备/使用」相反的例外：卖东西时玩家显然想先出手
        # 那件最不值钱的，把好的留下。
        if player.inventory.get(item_name, 0) < 1:
            _actual = player.find_inventory_name(item_name, prefer_quality=False)
            if _actual is None:
                async for _chunk in self._reply(event, f"储物袋里没有「{item_name}」。"):
                    yield _chunk
                return
            item_name = _actual

        item = get_item(item_name)
        if item is None:
            async for _chunk in self._reply(event, f"「{item_name}」无法出售。"):
                yield _chunk
            return

        # 【v3.5】天道令不能被卖掉 —— 100 枚 = 一件帝兵，
        # 误卖一枚就是白攒一天。放在名字解析**之后**，
        # 这样带品质后缀的变体也拦得住。
        # 数据层的 price=0 是第二道保险（万一这里漏了，卖掉也只值 0 灵石，
        # 玩家会立刻发现不对）。
        if item.get("effect") == "token":
            async for _chunk in self._reply(
                event,
                f"天道令是兑换帝兵的凭证，不能出售。\n"
                f"（攒够 {special_mod.TOKEN_COST} 枚可换一件帝兵，"
                f"发送 /特殊 列表 查看进度）",
            ):
                yield _chunk
            return

        # 已装备的法宝不允许卖，避免误操作
        if item_name in player.equipment.values():
            async for _chunk in self._reply(event, f"「{item_name}」正穿在身上，先卸下再卖。"):
                yield _chunk
            return

        price = sell_price(item_name)
        player.remove_item(item_name)
        player.gold += price
        await self._commit()
        async for _chunk in self._reply(event, f"卖出 {item_name}，获得 {price} 灵石。当前灵石：{player.gold}"):
            yield _chunk

    # ------------------------------------------------------------------
    # 成长指令
    # ------------------------------------------------------------------

    @gate_session(_SESSION_GATE)
    @filter.command("修炼")
    async def cultivate(self, event: AstrMessageEvent):
        """修炼：主动打坐，立刻获得修为。冷却 5 分钟。"""
        if not self.train_enabled:
            async for _chunk in self._reply(event, "本群的修行功能未开放。"):
                yield _chunk
            return

        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        # v3.6：挂机中（闭关 / 游历）不能打坐
        if player.is_idle():
            async for _chunk in self._reply(event, self._idle_hint(player)):
                yield _chunk
            return

        remain = player.cultivate_remain(self.cultivate_cooldown)
        if remain > 0:
            async for _chunk in self._reply(
                event,
                f"🧘 刚打坐过，气息未平\n"
                f"距离下次修炼：{_fmt_duration(remain)}\n"
                f"（想长时间修行就用 /闭关）",
            ):
                yield _chunk
            return

        gain, levels = battle.cultivate(player, self.item_db)
        player.last_cultivate_ts = time.time()
        await self._commit()

        msg = f"🧘 你静心打坐，修为 +{gain}。"
        if levels:
            msg += f"\n⬆️ 精进！连进 {levels} 转，当前 {player.realm_label}"
        qi_now, qi_need = realm_mod.qi_progress(player.turns, player.exp)
        msg += f"\n修为：{qi_now}/{qi_need}"
        msg += f"\n下次可修炼：{_fmt_duration(self.cultivate_cooldown)}后"
        async for _chunk in self._reply(event, msg):
            yield _chunk

    @gate_session(_SESSION_GATE)
    @filter.command("闭关")
    async def seclusion(self, event: AstrMessageEvent):
        """闭关：进入挂机状态，收益按挂机时长结算，最多累计 8 小时。

        v3.6：闭关是**状态**，不是冷却技能。
          - 闭关期间不能修炼 / 历练 / 寻敌 / 秘境（探索与之互斥）
          - 想出来时发送 /结束闭关 结算（再发一次 /闭关 也行，兼容老习惯）
          - 出关后没有冷却，可以立刻再闭关

        与「修炼」的区别：修炼是主动一次性的小收益，闭关是持续挂机的大收益，
        但有走火入魔的风险（时长越长概率越高，入魔则收益减半）。
        """
        if not self.train_enabled:
            async for _chunk in self._reply(event, "本群的修行功能未开放。"):
                yield _chunk
            return

        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        if player.is_resting():
            async for _chunk in self._reply(event, self._resting_hint(player)):
                yield _chunk
            return

        # 互斥：正在游历就先收工
        if player.is_exploring():
            async for _chunk in self._reply(
                event,
                f"🧭 你正在游历（已过 "
                f"{_fmt_duration(player.explore_elapsed())}），一心不能二用。\n"
                f"先发送 /结束探索 收工，再来闭关。",
            ):
                yield _chunk
            return

        # 已经在闭关：再发一次 = 出关结算（保留老玩家的操作习惯）
        if player.is_in_seclusion():
            msg = await self._settle_seclusion(player)
            async for _chunk in self._reply(event, msg):
                yield _chunk
            return

        # 进入闭关
        player.train_start_ts = time.time()
        await self._commit()
        async for _chunk in self._reply(
            event,
            "🕯️ 你盘膝入定，开始闭关。\n"
            "收益按挂机时长结算，最多累计 8 小时。\n"
            "闭关期间不能修炼、历练、寻敌、秘境。\n"
            "想出来时发送 /结束闭关 即可结算。",
        ):
            yield _chunk

    @gate_session(_SESSION_GATE)
    @filter.command("结束闭关", alias={"出关"})
    async def end_seclusion(self, event: AstrMessageEvent):
        """结束闭关并结算。（别名 /出关）"""
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        if not player.is_in_seclusion():
            async for _chunk in self._reply(
                event, "你并没有在闭关。发送 /闭关 可以开始挂机修行。"
            ):
                yield _chunk
            return

        msg = await self._settle_seclusion(player)
        async for _chunk in self._reply(event, msg):
            yield _chunk

    async def _settle_seclusion(self, player: Player) -> str:
        """结算闭关并拼好文案，返回消息文本（调用方负责 yield）。

        【为什么收口成一个方法】
        /闭关（再发一次）和 /结束闭关 两条入口都要结算，
        文案各写一遍迟早会改歪一边。结算逻辑只留这里一份。
        """
        result = battle.settle_training(player, self.item_db)
        await self._commit()

        if result.exp_gain <= 0:
            return "🕯️ 你收功出关。\n闭关不足 6 分钟，尚无所获。再多待一会儿吧。"

        msg = f"🕯️ 你出关了。闭关 {result.hours} 小时，修为 +{result.exp_gain}。"
        if result.capped:
            msg += "\n（已达 8 小时上限，超出部分不计入）"
        if result.deviation:
            msg += "\n⚠️ 走火入魔！你气息紊乱，收益减半。"
        if result.leveled_up:
            msg += f"\n⬆️ 精进！连进 {result.levels_gained} 转，当前 {player.realm_label}"
        return msg

    @gate_session(_SESSION_GATE)
    @filter.command("探索")
    async def explore(self, event: AstrMessageEvent):
        """探索：进入游历挂机状态，产出少量修为与各类物品（含大道秘典）。

        v3.6：和闭关一样是状态型挂机 ——
          - 游历期间不能修炼 / 历练 / 寻敌 / 秘境（闭关与之互斥）
          - 想收工时发送 /结束探索 结算（再发一次 /探索 也行）
          - 归来后没有冷却，可以立刻再出门
          - 至少游历 30 分钟才有收益
        """
        if not self.train_enabled:
            async for _chunk in self._reply(event, "本群的游历功能未开放。"):
                yield _chunk
            return

        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        if player.is_resting():
            async for _chunk in self._reply(event, self._resting_hint(player)):
                yield _chunk
            return

        # 互斥：正在闭关就先出关
        if player.is_in_seclusion():
            async for _chunk in self._reply(
                event,
                f"🕯️ 你正在闭关（已过 "
                f"{_fmt_duration(player.seclusion_elapsed())}），心无旁骛。\n"
                f"先发送 /结束闭关 出关，再出门游历。",
            ):
                yield _chunk
            return

        # 已经在游历：再发一次 = 收工结算（保留老玩家的操作习惯）
        if player.is_exploring():
            msg = await self._settle_explore(player)
            async for _chunk in self._reply(event, msg):
                yield _chunk
            return

        # 进入游历
        player.explore_start_ts = time.time()
        await self._commit()
        async for _chunk in self._reply(
            event,
            "🧭 你踏上了游历之路。\n"
            "收益按挂机时长结算，至少游历 30 分钟，最多累计 8 小时。\n"
            "游历期间不能修炼、闭关、历练、寻敌、秘境。\n"
            "想收工时发送 /结束探索。",
        ):
            yield _chunk

    @gate_session(_SESSION_GATE)
    @filter.command("结束探索", alias={"收工", "结束游历"})
    async def end_explore(self, event: AstrMessageEvent):
        """结束游历并结算。（别名 /收工）"""
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        if not player.is_exploring():
            async for _chunk in self._reply(
                event, "你并没有在外游历。发送 /探索 可以出门挂机。"
            ):
                yield _chunk
            return

        msg = await self._settle_explore(player)
        async for _chunk in self._reply(event, msg):
            yield _chunk

    async def _settle_explore(self, player: Player) -> str:
        """结算游历并拼好文案，返回消息文本（调用方负责 yield）。"""
        result = battle.settle_explore(player, self.item_db, self.inventory_limit)
        await self._commit()

        if result.hours <= 0:
            return "🧭 你刚出门就回来了，什么也没找到。至少游历 30 分钟。"

        lines = [f"🧭 你游历了 {result.hours} 小时"]
        if result.capped:
            lines.append("（已达 8 小时上限，超出部分不计入）")
        lines.append(f"修为 +{result.qi_gain}")
        if result.gold_gain:
            lines.append(f"灵石 +{result.gold_gain}")
        if result.relics:
            lines.append("拾得秘典残页：" + "、".join(result.relics))
        if result.items:
            lines.append("所得：" + "、".join(result.items))
        for ev in result.events:
            lines.append(f"✨ {ev}")
        if not (result.items or result.relics or result.gold_gain
                or result.events):
            lines.append("除此之外一无所获。")
        lines.append("")
        lines.append("发送 /修行 可以把残页修成大道秘典。")
        return "\n".join(lines)

    @gate_session(_SESSION_GATE)
    @filter.command("突破")
    async def breakthrough(self, event: AstrMessageEvent):
        """突破大境界 / 渡天劫飞升。"""
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        if player.is_resting():
            async for _chunk in self._reply(event, self._resting_hint(player)):
                yield _chunk
            return

        turns = player.turns
        qi = player.exp

        # ---- 情况一：大乘九转圆满 + 修为足够 -> 渡天劫 ----
        if realm_mod.can_tribulation(turns, qi):
            # 防误触：第一次只做提示，要求玩家再发一次确认。
            # 用「待应劫」标记区分两次调用 —— 没有它的话第二次仍然走
            # 这个分支，永远卡在提示上，飞升链路永远触发不了。
            if not getattr(player, "_tribulation_ready", False):
                player._tribulation_ready = True
                await self._commit()
                async for _chunk in self._reply(
                    event,
                    "⚡ 你已至大乘九转圆满，天劫将至。\n"
                    "记住：天劫不是打赢，是**抗住**——你的气血就是护盾。\n"
                    f"当前气血 {player.hp}/{player.max_hp(self.item_db)}。\n"
                    "准备好就再发一次 /突破 应劫。",
                ):
                    yield _chunk
                return

            # 第二次：真的应劫
            player._tribulation_ready = False
            result = battle.tribulate(player, self.item_db)

            if result.ascended:
                battle.ascend(player, self.item_db)
                await self._commit()
                async for _chunk in self._reply(
                    event,
                    f"🌩️ 九道天雷尽数抗下（{result.survived}/{result.total}）。\n"
                    f"劫云散尽，天门洞开 —— 你飞升了。\n\n"
                    f"转生第 {player.ascensions} 世，重归 {player.realm_label}。\n"
                    f"基础全属性 ×1.05（累计 ×{realm_mod.ascension_mult(player.ascensions):.2f}）\n"
                    f"灵石、法宝、秘典、大道皆随你入轮回。",
                ):
                    yield _chunk
                return

            # 渡劫失败
            await self._commit()
            if result.survived >= 5:
                tail = "你被劫雷震落境界，但道基未毁。"
            else:
                tail = "劫雷贯体，你重伤坠落，险些道消。"
            async for _chunk in self._reply(
                event,
                f"🌩️ 天劫未渡（抗住 {result.survived}/{result.total} 道）。\n"
                f"{tail}\n"
                f"境界跌落 {result.turns_lost} 转，当前 {player.realm_label}。\n"
                f"需休整 {_fmt_duration(result.rest_seconds)}。\n"
                "提示：满血应劫、备好渡劫丹，方能扛过九道。",
            ):
                yield _chunk
            return

        if realm_mod.is_max_turn(turns):
            need = realm_mod.ASCENSION_QI - qi
            async for _chunk in self._reply(
                event,
                f"你已是大乘九转圆满，但修为不足。\n"
                f"飞升需累计修为 {realm_mod.ASCENSION_QI}，当前 {qi}（还差 {need}）。",
            ):
                yield _chunk
            return

        # ---- 情况二：可以突破大境界 ----
        if realm_mod.can_breakthrough(turns, qi):
            from .scriptures import equip_slots

            old_label = player.realm_label
            # 突破推进一转（九转 -> 下一境界一转）
            player.level = min(realm_mod.MAX_TURNS, turns + 1)
            # 修为是累计里程碑，突破不清零
            player.breakthroughs += 1
            player.full_heal(self.item_db)
            await self._commit()

            new_slots = equip_slots(player.breakthroughs)
            async for _chunk in self._reply(
                event,
                f"🎉 突破成功！{old_label} → {player.realm_label}\n"
                f"你已踏入 {player.realm_name}，道基更固。\n"
                f"基础全属性 ×1.20（累计 ×{realm_mod.breakthrough_mult(player.breakthroughs):.2f}）\n"
                f"秘典装配位：{new_slots}",
            ):
                yield _chunk
            return

        # ---- 情况三：还不能突破，说明进度 ----
        need_turn = realm_mod.turn_in_realm(turns) == realm_mod.TURNS_PER_REALM
        if not need_turn:
            left = realm_mod.TURNS_PER_REALM - realm_mod.turn_in_realm(turns)
            async for _chunk in self._reply(
                event,
                f"突破需要先修满本境界九转。\n"
                f"你当前 {player.realm_label}，还差 {left} 转。\n"
                f"（通过 /修炼 /闭关 /历练 积累修为即可精进）",
            ):
                yield _chunk
            return

        idx = realm_mod.realm_index_of(turns)
        next_idx = min(idx + 1, len(realm_mod.REALMS) - 1)
        need_qi = realm_mod.REALMS[next_idx][2]
        async for _chunk in self._reply(
            event,
            f"你已修满 {player.realm_name} 九转，但修为不足。\n"
            f"突破需累计修为 {need_qi}，当前 {qi}（还差 {need_qi - qi}）。",
        ):
            yield _chunk

    # ------------------------------------------------------------------
    # 秘典指令
    # ------------------------------------------------------------------

    @gate_session(_SESSION_GATE)
    @filter.command("大道")
    async def scriptures_cmd(self, event: AstrMessageEvent):
        """查看大道秘典：已习得、残页、装配位。"""
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        lines = [f"📜 {player.name} 的大道", ""]

        if player.scriptures:
            lines.append("【已习得】")
            for name, tier in player.scriptures.items():
                sc = get_scripture(name)
                desc = sc["desc"] if sc else ""
                mark = "（已装配）" if name in player.equipped_scriptures else ""
                lines.append(f"· {name} {tier} 阶{mark}　{desc}")
        else:
            lines.append("【已习得】尚未习得任何大道")
            lines.append("　从 /探索 或历练中可获得秘典残页")

        # 残页存量
        relics = []
        for name in SCRIPTURE_NAMES:
            rn = relic_name(name)
            cnt = player.inventory.get(rn, 0)
            if cnt > 0:
                relics.append(f"· {rn} ×{cnt}")
        if relics:
            lines.append("")
            lines.append("【秘典残页】")
            lines.extend(relics)

        lines.append("")
        lines.append(
            f"【装配位】{len(player.equipped_scriptures)}/{player.equip_slots}"
            "（每次大境界突破 +1）"
        )
        if player.equipped_scriptures:
            lines.append("已装配：" + "、".join(player.equipped_scriptures))

        lines.append("")
        lines.append("发送 /修行 <秘典名> 提升阶数，/装配 <秘典名> 装配或卸下。")
        lines.append("可修行的大道：" + "、".join(SCRIPTURE_NAMES))
        async for _chunk in self._reply(event, "\n".join(lines)):
            yield _chunk

    @gate_session(_SESSION_GATE)
    @filter.command("修行")
    async def practice(self, event: AstrMessageEvent, name: str = ""):
        """修行大道：消耗秘典残页 + 修为提升一阶。"""
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        if not name:
            async for _chunk in self._reply(
                event,
                "用法：/修行 <秘典名>\n可修行：" + "、".join(SCRIPTURE_NAMES),
            ):
                yield _chunk
            return

        if not has_scripture(name):
            async for _chunk in self._reply(
                event,
                f"没有「{name}」这条大道。\n可修行：" + "、".join(SCRIPTURE_NAMES),
            ):
                yield _chunk
            return

        try:
            cur_tier = int(player.scriptures.get(name, 0) or 0)
        except (TypeError, ValueError):
            cur_tier = 0

        if cur_tier >= MAX_TIER:
            async for _chunk in self._reply(
                event, f"「{name}」已至 {MAX_TIER} 阶圆满，无法再进。"
            ):
                yield _chunk
            return

        target = cur_tier + 1
        rn = relic_name(name)
        need_relic = relic_cost(name, target)
        need_qi = qi_cost(name, target)

        have_relic = player.inventory.get(rn, 0)
        if have_relic < need_relic:
            async for _chunk in self._reply(
                event,
                f"修行「{name}」至 {target} 阶，需要 {rn} ×{need_relic}。\n"
                f"你只有 ×{have_relic}。\n"
                f"（残页可从 /探索 或历练中掉落）",
            ):
                yield _chunk
            return

        if player.exp < need_qi:
            async for _chunk in self._reply(
                event,
                f"修行「{name}」至 {target} 阶，需要修为 {need_qi}。\n"
                f"你当前修为 {player.exp}，还差 {need_qi - player.exp}。",
            ):
                yield _chunk
            return

        # 扣除并提升
        player.remove_item(rn, need_relic)
        player.exp -= need_qi
        player.scriptures[name] = target
        await self._commit()

        info = tier_info(name, target)
        tname = info.get("name", "") if info else ""
        tdesc = info.get("desc", "") if info else ""
        ttype = "主动" if info and info.get("type") == "active" else "被动"

        msg = (
            f"📜 你参悟「{name}」，已至 {target} 阶。\n"
            f"【{tname}】{ttype}技\n{tdesc}"
        )
        if target == 1:
            msg += f"\n\n发送 /装配 {name} 让它在你战斗中生效。"
        async for _chunk in self._reply(event, msg):
            yield _chunk

    @gate_session(_SESSION_GATE)
    @filter.command("装配")
    async def equip_scripture(self, event: AstrMessageEvent, name: str = ""):
        """装配或卸下大道秘典。"""
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        if not name:
            async for _chunk in self._reply(
                event,
                "用法：/装配 <秘典名>（已装配的再发一次即卸下）\n"
                "可装配：" + "、".join(SCRIPTURE_NAMES),
            ):
                yield _chunk
            return

        if not has_scripture(name):
            async for _chunk in self._reply(
                event, f"没有「{name}」这条大道。"
            ):
                yield _chunk
            return

        # 已装配 -> 卸下
        if name in player.equipped_scriptures:
            _ok, hint = player.unequip_scripture(name)
            await self._commit()
            async for _chunk in self._reply(event, hint):
                yield _chunk
            return

        ok, hint = player.equip_scripture(name)
        if not ok:
            async for _chunk in self._reply(event, hint):
                yield _chunk
            return

        # 展示装配后的效果
        learned = player.learned_tier(name)
        lines = [hint, f"「{name}」当前 {learned} 阶，战斗中自动生效："]
        for tier in range(1, learned + 1):
            info = tier_info(name, tier)
            if info:
                kind = "主动" if info.get("type") == "active" else "被动"
                lines.append(f"· {info.get('name', '')}（{kind}）{info.get('desc', '')}")
        await self._commit()
        async for _chunk in self._reply(event, "\n".join(lines)):
            yield _chunk

    @gate_session(_SESSION_GATE)
    @filter.command("排行榜")
    async def ranking(self, event: AstrMessageEvent):
        """查看本群道行排行榜，人数由配置决定。"""
        group_id, _ = self._scope(event)

        if group_id:
            players = self.store.players_in_group(group_id)
            title = "🏆 本群道行排行榜"
        else:
            # 私聊场景没有"本群"，退回全服榜
            players = self.store.all_players()
            title = "🏆 全服道行排行榜"

        if not players:
            async for _chunk in self._reply(event, "还没有人报名。/报名 成为第一个。"):
                yield _chunk
            return

        # 按转数降序，同转比修为
        top = sorted(
            players, key=lambda p: (p.turns, p.exp), reverse=True
        )[: self.ranking_size]

        lines = [title]
        medals = ["🥇", "🥈", "🥉"]
        for idx, player in enumerate(top):
            rank = medals[idx] if idx < 3 else f"{idx + 1}."
            asc = f"（转生{player.ascensions}）" if player.ascensions else ""
            lines.append(
                f"{rank} {player.name}　{player.realm_label}{asc}　"
                f"历练胜 {player.total_kills}"
            )
        async for _chunk in self._reply(event, "\n".join(lines)):
            yield _chunk

    # ------------------------------------------------------------------
    # 商店指令
    # ------------------------------------------------------------------

    @gate_session(_SESSION_GATE)
    @gate_session(_SESSION_GATE)
    @filter.command("坊市", alias={"商店"})
    async def shop(self, event: AstrMessageEvent):
        """查看坊市。（别名 /商店）"""
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        lines = ["🏪 坊市", f"你的灵石：{player.gold}", "", "【丹药】"]
        for name in SHOP_CONSUMABLES:
            item = get_item(name)
            effect = item.get("value", 0)
            label = EFFECT_LABELS.get(item.get("effect", ""), "特殊")
            lines.append(
                f"· {name}　{buy_price(name)} 灵石　（{label} {effect}）"
            )

        equip_stock = shop_stock(player.turns)
        if equip_stock:
            lines.append("")
            lines.append("【法宝】")
            for name in equip_stock:
                eq = get_item(name)
                slot = SLOT_LABELS.get(eq["slot"], eq["slot"])
                bonus = []
                if eq.get("atk"):
                    bonus.append(f"攻+{eq['atk']}")
                if eq.get("def"):
                    bonus.append(f"防+{eq['def']}")
                if eq.get("hp"):
                    bonus.append(f"气+{eq['hp']}")
                lines.append(
                    f"· {name}　{buy_price(name)} 灵石　"
                    f"（{slot} {' '.join(bonus)}）"
                )
        else:
            lines.append("")
            lines.append("【法宝】当前境界暂无可购买的法宝。")

        lines.append("")
        lines.append("发送 /购买 <物品名> 下单。")
        async for _chunk in self._reply(event, "\n".join(lines)):
            yield _chunk

    @gate_session(_SESSION_GATE)
    @filter.command("购买")
    async def buy(self, event: AstrMessageEvent, item_name: str):
        """从商店购买物品，例如 /购买 小型生命药水。"""
        player = self._require_player(event)
        if player is None:
            async for _chunk in self._reply(event, "你还没有角色，发送 /报名 创建一个。"):
                yield _chunk
            return

        item = get_item(item_name)
        if item is None:
            async for _chunk in self._reply(event, f"商店没有「{item_name}」这种商品。"):
                yield _chunk
            return

        # 装备要检查等级门槛
        if item.get("kind") == "equipment":
            if item_name not in shop_stock(player.level):
                async for _chunk in self._reply(
                    event,
                    f"你的境界还买不了「{item_name}」。"
                    f"发送 /商店 查看当前可买的法宝。"
                ):
                    yield _chunk
                return
        elif item_name not in SHOP_CONSUMABLES:
            async for _chunk in self._reply(event, f"「{item_name}」不在商店的货架上。"):
                yield _chunk
            return

        price = buy_price(item_name)
        if player.gold < price:
            async for _chunk in self._reply(event, f"灵石不足。需要 {price}，你只有 {player.gold}。"):
                yield _chunk
            return

        # 背包满时不能买新种类的物品
        if not player.add_item(item_name, limit=self.inventory_limit):
            async for _chunk in self._reply(event, "储物袋已满，先卖掉些东西再来买。"):
                yield _chunk
            return

        player.gold -= price
        await self._commit()
        async for _chunk in self._reply(
            event,
            f"购买成功：{item_name}，花费 {price} 灵石。"
            f"剩余灵石：{player.gold}"
        ):
            yield _chunk

    # ------------------------------------------------------------------
    # 管理指令
    # ------------------------------------------------------------------

    @gate_session(_SESSION_GATE)
    @gate_sensitive(_SENSITIVE_GATE)
    @filter.command("重置")
    async def reset(self, event: AstrMessageEvent):
        """清空玩家存档，仅主人可用。

        群聊里清空本群；私聊里清空「私聊存档池」。
        私聊不再清空全部群——那样一次误发就会毁掉所有群的数据。

        权限由 gate_sensitive 装饰器统一把关，这里不再重复判断。
        """
        group_id, qq = self._scope(event)

        if group_id:
            count = self.store.clear_group(group_id)
            await self._commit()
            async for _chunk in self._reply(event, f"已清空本群 {count} 名玩家的存档。"):
                yield _chunk
        else:
            # 私聊只清自己的私聊存档，不波及任何群
            removed = self.store.delete("", qq)
            await self._commit()
            if removed:
                msg = "已清空你的私聊存档。其他群的存档不受影响。"
            else:
                msg = "你还没有私聊存档。"
            async for _chunk in self._reply(event, msg):
                yield _chunk

    # ------------------------------------------------------------------
    # 群激活
    #
    # 注意：激活指令**不能**加 gate_session，
    # 否则未激活的群永远激活不了（鸡生蛋问题）。
    # 它的保护靠 gate_sensitive（仅主人可发）。
    # ------------------------------------------------------------------

    @gate_sensitive(_SENSITIVE_GATE)
    @filter.command("激活")
    async def activate(self, event: AstrMessageEvent):
        """在当前群激活文字 RPG，仅主人可用。"""
        group_id, qq = self._scope(event)

        if not group_id:
            async for _chunk in self._reply(
                event,
                "「激活」需要在群聊里发送，这样才知道要激活哪个群。",
            ):
                yield _chunk
            return

        # 取群名，纯粹为了日志和管理方便，取不到不影响功能
        group_name = ""
        try:
            group = await event.get_group(group_id)
            if group is not None:
                group_name = getattr(group, "group_name", "") or ""
        except Exception:  # noqa: BLE001 - 拿不到群名不影响激活
            group_name = ""

        fresh = await self.activation.activate(group_id, by=qq, name=group_name)
        label = f"{group_name}({group_id})" if group_name else group_id

        if fresh:
            msg = (
                f"已在 {label} 激活文字 RPG。\n"
                f"群友可以发送 /报名 开始游戏了。"
            )
        else:
            msg = f"{label} 已经处于激活状态，无需重复激活。"
        async for _chunk in self._reply(event, msg):
            yield _chunk

    @gate_sensitive(_SENSITIVE_GATE)
    @filter.command("取消激活")
    async def deactivate(self, event: AstrMessageEvent):
        """关闭当前群的文字 RPG，仅主人可用。"""
        group_id, _qq = self._scope(event)

        if not group_id:
            async for _chunk in self._reply(
                event, "「取消激活」需要在群聊里发送。"
            ):
                yield _chunk
            return

        removed = await self.activation.deactivate(group_id)
        if removed:
            msg = f"已取消 {group_id} 的激活。本群存档保留，重新激活后进度还在。"
        else:
            msg = f"{group_id} 本来就未激活。"
        async for _chunk in self._reply(event, msg):
            yield _chunk

    @gate_sensitive(_SENSITIVE_GATE)
    @filter.command("激活列表")
    async def activation_list(self, event: AstrMessageEvent):
        """查看已激活的群，仅主人可用（私聊查看最方便）。"""
        groups = self.activation.activated_groups()
        if not groups:
            msg = "当前没有任何已激活的群。"
        else:
            lines = [f"已激活 {len(groups)} 个群："]
            for gid in groups:
                info = self.activation.info(gid) or {}
                name = info.get("name") or ""
                who = info.get("by") or ""
                tail = f"（{name}）" if name else ""
                lines.append(f"  {gid}{tail} ← {who}")
            msg = "\n".join(lines)
        async for _chunk in self._reply(event, msg):
            yield _chunk

    # ------------------------------------------------------------------
    # 入群邀请守卫
    #
    # 只有主人的邀请才接受，其他人拉机器人进群一律拒绝。
    # 这样机器人不会被陌生人拉去各种群里刷屏。
    # ------------------------------------------------------------------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_invite(self, event: AstrMessageEvent):
        """拦截入群邀请 / 好友请求。"""
        raw = getattr(event, "raw_message", None)
        if raw is None:
            return
        try:
            post_type = raw.get("post_type")
        except AttributeError:
            return

        if post_type != "request":
            return

        req_type = raw.get("request_type")
        inviter = str(raw.get("user_id") or "")
        group_id = str(raw.get("group_id") or "")

        # 只处理加群邀请，好友请求暂不自动处理
        if req_type != "group":
            return

        allowed, reason = self.acl.check_invite(inviter)
        flag = raw.get("flag")

        # bot 属性只在 aiocqhttp 事件类上有，其他平台没有，必须保护
        bot = getattr(event, "bot", None)
        if bot is None:
            logger.warning(
                f"[textrpg] 当前平台无法自动处理入群邀请"
                f"（邀请人={inviter} 群={group_id}），请手动处理"
            )
            return

        if allowed:
            logger.info(
                f"[textrpg] 接受主人的入群邀请：邀请人={inviter} 群={group_id}"
            )
            try:
                await bot.set_group_add_request(
                    flag=flag, sub_type="invite", approve=True
                )
            except Exception as exc:  # noqa: BLE001 - 处理失败不能崩插件
                logger.error(f"[textrpg] 同意入群失败: {exc}")
            return

        logger.info(
            f"[textrpg] {reason}（邀请人={inviter} 群={group_id}），已自动拒绝"
        )
        try:
            await bot.set_group_add_request(
                flag=flag, sub_type="invite", approve=False,
                reason="未获授权，本机器人不接受邀请",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[textrpg] 拒绝入群失败: {exc}")

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def terminate(self):
        """插件卸载/停用时保存存档。"""
        await self._commit()
        logger.info("[textrpg] 插件已卸载，存档已保存")
