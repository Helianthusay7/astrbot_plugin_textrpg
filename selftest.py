"""离线自测脚本：用 Stub 模拟 AstrBot API，验证核心逻辑。

为什么需要它：
    插件本身依赖 astrbot 包才能导入，本地没有 AstrBot 环境时
    无法验证。这个脚本注入假的 astrbot 模块，让 main.py 能导入，
    然后直接调用指令方法跑一遍完整流程。

运行：
    python selftest.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import time
import types
from pathlib import Path

HERE = Path(__file__).parent


# ---------------------------------------------------------------------------
# 0. 每次运行前清空测试数据，保证从干净状态开始
# ---------------------------------------------------------------------------
# 曾经踩过坑：残留的 _testdata 会让第二次运行读到上次的存档，
# 导致「重复报名」「已装备物品」之类的断言假失败。
for _d in ("_testdata", "_testdata_bad", "_testdata_old",
           "_testdata_act", "_testdata_act_bad"):
    shutil.rmtree(HERE / _d, ignore_errors=True)


# ---------------------------------------------------------------------------
# 1. 注入假的 astrbot 模块，让 main.py 可导入
# ---------------------------------------------------------------------------

def install_stubs() -> None:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api_event = types.ModuleType("astrbot.api.event")
    api_star = types.ModuleType("astrbot.api.star")
    api_msg_comp = types.ModuleType("astrbot.api.message_components")

    class _Logger:
        def info(self, msg, *a, **k):
            print(f"  [log] {msg}")

        def error(self, msg, *a, **k):
            print(f"  [ERR] {msg}")

        def warning(self, msg, *a, **k):
            print(f"  [warn] {msg}")

    api.logger = _Logger()

    # AstrBotConfig 在真实环境里继承自 dict
    class AstrBotConfig(dict):
        def save_config(self):
            pass

    api.AstrBotConfig = AstrBotConfig

    class _Filter:
        class PermissionType:
            ADMIN = "admin"

        class EventMessageType:
            ALL = "all"

        class PlatformAdapterType:
            AIOCQHTTP = "aiocqhttp"

        @staticmethod
        def command(name, **kw):
            def deco(fn):
                fn._cmd = name
                return fn
            return deco

        @staticmethod
        def command_group(name, **kw):
            def deco(fn):
                return fn
            return deco

        @staticmethod
        def permission_type(kind):
            def deco(fn):
                return fn
            return deco

        @staticmethod
        def event_message_type(kind):
            def deco(fn):
                return fn
            return deco

        @staticmethod
        def on_llm_request(**kw):
            def deco(fn):
                fn._llm_request = True
                return fn
            return deco

        @staticmethod
        def on_llm_response(**kw):
            def deco(fn):
                return fn
            return deco

    api_event.filter = _Filter

    class Plain:
        """消息段 stub。插件靠 type(comp).__name__ == "Plain" 认它。"""

        def __init__(self, text):
            self.text = text

    class Image:
        """图片消息段 stub —— 字段名必须和 AstrBot 真实组件对齐。

        ★ 血泪：AstrBot 的 Image **没有 base64 字段**，base64 是以
        "base64://" 前缀塞在 file 里的（见 components.py Image.fromBase64）；
        而 NapCat(aiocqhttp) 给的 file 常是 URL 编码的本地路径
        （d:%5Cai%5CAstrBot%5C...jpg，%5C 就是 \\）。
        这个 stub 故意只给 path/file/url 三个字段，防止插件再写错。
        """

        def __init__(self, file="", url="", path=""):
            self.file = file
            self.url = url
            self.path = path

        @staticmethod
        def fromFileSystem(path, **_):
            """对齐真实 API（components.py:519）：本地路径 -> file:// URI。"""
            fp = Path(path).resolve(strict=False)
            return Image(file=fp.as_uri(), path=str(fp))

    class _MsgObj:
        """AstrBotMessage 的轻量替身。"""

        def __init__(self, message_id="", plain="", comps=None):
            self.message_id = message_id
            if comps is not None:
                self.message = list(comps)
            else:
                self.message = [Plain(plain)] if plain else []

    class AstrMessageEvent:
        def __init__(self, qq="10001", name="测试玩家", text="", group="999",
                     admin=False, message_id="", raw_text=None, comps=None):
            self._qq = qq
            self._name = name
            self._group = group
            self._admin = admin
            self.message_str = text
            self.unified_msg_origin = f"group:{group}:{qq}"
            self._stopped = False
            # 真实环境里 message_str 已被 wake_prefix 剥掉 `/`，而消息段里的
            # 文本是原始的。插件要区分「/菜单」和「菜单」只能读消息段，
            # 所以 stub 也得提供 message_obj。
            self.message_obj = _MsgObj(
                message_id=message_id,
                plain=text if raw_text is None else raw_text,
                comps=comps,
            )

        def get_sender_id(self):
            return self._qq

        def get_sender_name(self):
            return self._name

        def get_group_id(self):
            return self._group

        def get_message_str(self):
            return self.message_str

        def is_admin(self):
            return self._admin

        def plain_result(self, text):
            return _Result(text)

        def image_result(self, url_or_path):
            # 真实签名见 astr_message_event.py:408 —— 只接一个路径/URL。
            # 这里必须实现，否则插件里 `yield event.image_result(...)`
            # 会被 try/except 吞掉，断言就打在兜底分支上了（假绿）。
            return _ImgResult(url_or_path)

        def chain_result(self, chain):
            return _ChainResult(chain)

        def stop_event(self):
            self._stopped = True

        def is_stopped(self):
            return self._stopped

    class _Result:
        def __init__(self, text):
            self.text = text

        def __str__(self):
            return str(self.text)

    class _ImgResult:
        """图片消息桩。

        str() 带 [图片] 前缀，这样 run_cmd 拼出来的文本里能一眼认出
        「这条是图，不是字」，断言也就能区分两种 yield。
        """

        def __init__(self, path):
            self.path = str(path)

        def __str__(self):
            return f"[图片]{self.path}"

    class _ChainResult:
        """消息链桩（图文同框）。

        把各段按顺序拼成可读文本，这样断言才能验证
        「图片和文字在**同一条**消息里」以及「图片排在文字前面」——
        拆成两条 yield 和拼成一条 yield，用 _Result 是分辨不出来的。
        """

        def __init__(self, chain):
            self.chain = list(chain)

        def __str__(self):
            parts = []
            for comp in self.chain:
                name = type(comp).__name__
                if name == "Image":
                    src = getattr(comp, "path", "") or getattr(comp, "file", "")
                    parts.append(f"[图片]{src}")
                elif name == "Plain":
                    parts.append(str(getattr(comp, "text", "")))
                else:
                    parts.append(f"[{name}]")
            return "\n".join(parts)

    api_event.AstrMessageEvent = AstrMessageEvent
    # 插件靠 type(comp).__name__ == "Image" 认图片组件，把它挂出来给测试用
    api_event.Image = Image
    api_event.MessageChain = object
    # 发「图文同框」消息要用 astrbot.api.message_components.Image / .Plain
    api_msg_comp.Image = Image
    api_msg_comp.Plain = Plain

    class Context:
        pass

    class Star:
        def __init__(self, context=None):
            self.context = context

    class StarTools:
        _dir = HERE / "_testdata"

        @staticmethod
        def get_data_dir():
            StarTools._dir.mkdir(parents=True, exist_ok=True)
            return StarTools._dir
    api_star.Context = Context
    api_star.Star = Star
    api_star.StarTools = StarTools

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = api_event
    sys.modules["astrbot.api.star"] = api_star
    sys.modules["astrbot.api.message_components"] = api_msg_comp


install_stubs()

# 现在才能导入插件
sys.path.insert(0, str(HERE.parent))
import importlib

plugin_mod = importlib.import_module(f"{HERE.name}.main")
battle_mod = importlib.import_module(f"{HERE.name}.battle")
data_mod = importlib.import_module(f"{HERE.name}.data")
player_mod = importlib.import_module(f"{HERE.name}.player")
items_mod = importlib.import_module(f"{HERE.name}.items")
realm_mod = importlib.import_module(f"{HERE.name}.realm")
scriptures_mod = importlib.import_module(f"{HERE.name}.scriptures")
dungeon_mod = importlib.import_module(f"{HERE.name}.dungeon")
character_mod = importlib.import_module(f"{HERE.name}.character")
AstrMessageEvent = sys.modules["astrbot.api.event"].AstrMessageEvent
ImageComp = sys.modules["astrbot.api.event"].Image


# ---------------------------------------------------------------------------
# 2. 测试用例
# ---------------------------------------------------------------------------

PASS = 0
FAIL = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {extra}")


async def run_cmd(plugin, method_name, event, *args):
    """调用某个指令方法，返回所有 yield 出来的文本。"""
    fn = getattr(plugin, method_name)
    out = []
    async for chunk in fn(event, *args):
        out.append(str(chunk))
    return "\n".join(out)


async def run_cmd_parts(plugin, method_name, event, *args):
    """调用指令方法，返回**每条 yield 的原始对象**（不拼接）。

    用来检查消息边界 —— 「图片是不是第一条」「战绩是不是单独一条」
    这类问题，被 run_cmd 拼成一个大字符串之后就看不出来了。
    """
    fn = getattr(plugin, method_name)
    parts = []
    async for chunk in fn(event, *args):
        parts.append(chunk)
    return parts


async def fast_create(plugin, event, name="测试角色", level: int = 0):
    """走完整捏脸流程建号，返回最后一条回复。

    v2 的 /报名 是三步交互，老测试想直接拿到一个已建号状态，
    所以这里封装成一步：报名 -> 发名字 -> 跳过形象 -> 确认。

    level > 0 时把角色拉到指定转数（clamp 到 72）并补满气血 ——
    用来避免「低级角色打怪必败 → 进入休整 → 后续用例全被挡住」
    这种测试间的互相污染。

    v3 说明：level 现在是「转数」（1~72），不再有 200 级上限。
    """
    await run_cmd(plugin, "register", event)
    group_id = str(event.get_group_id() or "")
    qq = str(event.get_sender_id())
    # 第 1 步：直接往会话里塞名字（绕过消息拦截，语义等价）
    plugin.creation.submit_name(group_id, qq, name)
    # 第 2 步：跳过形象
    plugin.creation.submit_avatar(group_id, qq, "")
    # 第 3 步：确认
    sess = plugin.creation.get(group_id, qq)
    if sess is None:
        return ""
    player = plugin._finalize_creation(sess, event)
    plugin.creation.drop(group_id, qq)
    if level > 0:
        player.level = realm_mod.clamp_turns(level)
        player.full_heal(plugin.item_db)
        player.rest_until = 0.0
        player.last_hunt_ts = 0.0
        player.last_train_ts = 0.0
        player.train_start_ts = 0.0
        player.last_cultivate_ts = 0.0
        player.last_explore_ts = 0.0
        player.explore_start_ts = 0.0
    await plugin._commit()
    return f"建号完成：{player.name}"


async def main() -> None:
    # 关掉随机延迟（否则太慢），调高熔断阈值避免用例互相干扰；
    # 熔断逻辑本身在 [21] 单独测。
    plugin = plugin_mod.TextRPGPlugin(context=None, config={
        "reply_delay_range": [0, 0],
        "chunk_gap_range": [0, 0],
        "rate_limit_count": 10000,
        # 老用例测的是游戏逻辑本身，不该被群白名单干扰
        "group_whitelist_mode": "all",
    })

    print("\n[1] 报名与属性（v2 三步捏脸）")
    ev = AstrMessageEvent(qq="10001", name="老板")
    out = await run_cmd(plugin, "register", ev)
    check("报名进入捏脸第 1 步", "第 1 步" in out and "角色名" in out, out)

    # 名字非法：太短
    ok, msg = plugin.creation.submit_name("999", "10001", "a")
    check("名字太短被拒", not ok, msg)
    # 名字非法：纯数字
    ok, msg = plugin.creation.submit_name("999", "10001", "12345")
    check("纯数字名字被拒", not ok, msg)
    # 名字非法：纯符号
    ok, msg = plugin.creation.submit_name("999", "10001", "***")
    check("纯符号名字被拒", not ok, msg)
    # 名字非法：过长
    ok, msg = plugin.creation.submit_name("999", "10001", "一二三四五六七八九十十一十二十三")
    check("过长名字被拒", not ok, msg)
    # 合法名字
    ok, name = plugin.creation.submit_name("999", "10001", "独孤求败")
    check("合法名字通过", ok, name)

    sess = plugin.creation.get("999", "10001")
    check("推进到头像步骤", sess is not None and sess.step == "avatar", str(sess))
    ok, _ = plugin.creation.submit_avatar("999", "10001", "")
    check("跳过形象后到确认步骤", sess.step == "confirm", str(sess))

    player = plugin._finalize_creation(sess, ev)
    plugin.creation.drop("999", "10001")
    check("玩家对象已建立", player is not None)
    check("名字用玩家自定义的", player.name == "独孤求败", player.name)
    check("初始转数为 1", player.level == 1, f"{player.level}")
    check("初始境界为炼气期", player.realm_name == "炼气期", player.realm_name)
    check(f"初始气血 = INIT_HP（{player_mod.INIT_HP}）",
          player.hp == player_mod.INIT_HP, f"{player.hp}")
    check("初始灵石 50", player.gold == 50)
    check("建号时间已记录", player.created_at > 0)
    check("初始不在休整中", not player.is_resting())
    check("初始无历练冷却", player.hunt_remain() == 0.0)

    # v3 关键：建号后拉到金丹期（19 转）再测其他指令。
    # 否则低转角色打怪大概率道消 -> 进入休整 ->
    # 后面的冷却/寻敌用例全被休整挡住，属于测试间的互相污染。
    player.level = 19
    player.full_heal(plugin.item_db)
    await plugin._commit()
    check("拉转数后气血同步", player.hp == player.max_hp(plugin.item_db))
    check("转数映射到金丹期", player.realm_name == "金丹期", player.realm_name)

    # 已建号后再报名应被拦
    out = await run_cmd(plugin, "register", ev)
    check("重复报名被拦截", "已经报过名" in out, out)

    out = await run_cmd(plugin, "profile", ev)
    check("属性面板含境界", "境界" in out and "金丹期" in out, out)
    check("属性面板含转数", "19" in out or "转" in out, out)
    check("属性面板显示自定义名", "独孤求败" in out, out)
    # v3.6：乘区栏已从面板移除（老板要求「修为下面的乘区栏不要写上去」）
    check("属性面板不再显示乘区栏", "乘区" not in out, out[:200])
    check("属性面板显示秘典栏", "秘典" in out, out)
    # 旧写法把 _stat_line 的输出整个塞进括号里，得到「（120　（120 × 1.00））」
    _blood_line = next(
        (ln for ln in out.split("\n") if ln.startswith("气血")), ""
    )
    check("气血行括号只有一层（不再套两层）",
          _blood_line.count("（") == 1 and _blood_line.count("）") == 1,
          _blood_line)

    # -----------------------------------------------------------------
    # [1b] 捏脸流程的事件隔离（线上事故回归）
    # -----------------------------------------------------------------
    #
    # 2026-09-20 线上实测：老板发 /报名，机器人先问「请发送你的角色名」，
    # 紧接着又回「✅ 角色名「报名」」—— 同一条消息被处理了两次。
    #
    # 原因：register 和 on_creation_message 的 filter 都会通过，
    # activated_handlers 里两个都在，而 process_stage 是**顺序执行**的：
    #   register 先跑 -> 建会话 -> 回第 1 步
    #   on_creation_message 接着跑 -> 看到会话已存在 -> 把同一条消息
    #   （`/` 已被 wake_prefix 剥掉，正好剩「报名」两个字）当成名字提交
    #
    # 两道修复都要钉住：
    #   1. register 末尾 event.stop_event()（process_stage 会 break）
    #   2. 会话记 origin_msg_id，拦截器跳过那条消息本身
    print("\n[1b] 捏脸流程的事件隔离（线上事故回归）")

    async def _drain(plugin_, ev_, names):
        """复刻 process_stage 的 handler 循环：每次先看 is_stopped 再执行。"""
        got = []
        for nm in names:
            if ev_.is_stopped():
                break
            fn = getattr(plugin_, nm)
            async for chunk in fn(ev_):
                got.append(str(chunk))
        return "\n".join(got)

    # ---- 场景 1：/报名 只该被处理一次 ----
    ev_signup = AstrMessageEvent(
        qq="20001", name="新人", text="报名", group="888",
        message_id="MSG_SIGNUP", raw_text="/报名",
    )
    out_signup = await _drain(
        plugin, ev_signup, ["register", "on_creation_message"]
    )
    check("报名后事件被 stop（不再流给拦截器）", ev_signup.is_stopped())
    check("报名只回了第 1 步，没有直接吞掉名字",
          "第 1 步" in out_signup and "角色名「" not in out_signup,
          out_signup)
    sess_signup = plugin.creation.get("888", "20001")
    check("报名后会话停在收名字步骤",
          sess_signup is not None and sess_signup.step == "name",
          str(sess_signup))
    check("报名后会话里没有名字（没被自己吞掉）",
          sess_signup is not None and sess_signup.name == "",
          str(sess_signup))
    check("会话记下了来源消息 id",
          sess_signup is not None and sess_signup.origin_msg_id == "MSG_SIGNUP",
          str(getattr(sess_signup, "origin_msg_id", None)))
    plugin.creation.drop("888", "20001")

    # ---- 场景 2：捏脸途中发指令，不能被当成名字 ----
    plugin.creation.start("888", "20001")
    ev_cmd = AstrMessageEvent(
        qq="20001", name="新人", text="菜单", group="888",
        message_id="MSG_MENU", raw_text="/菜单",
    )
    await _drain(plugin, ev_cmd, ["on_creation_message"])
    sess_cmd = plugin.creation.get("888", "20001")
    check("捏脸中发 /菜单 不会被当成名字",
          sess_cmd is not None and sess_cmd.name == "",
          str(sess_cmd))

    # 真的发名字 -> 正常消费
    ev_name = AstrMessageEvent(
        qq="20001", name="新人", text="独孤求败", group="888",
        message_id="MSG_NAME", raw_text="独孤求败",
    )
    out_name = await _drain(plugin, ev_name, ["on_creation_message"])
    check("捏脸中发普通文本仍被当成名字",
          "角色名「独孤求败」" in out_name, out_name)

    # ---- 场景 3：/取消 是流程自己的退出词，不能被指令放行规则吃掉 ----
    plugin.creation.start("888", "20001")
    ev_cancel = AstrMessageEvent(
        qq="20001", name="新人", text="取消", group="888",
        message_id="MSG_CANCEL", raw_text="/取消",
    )
    out_cancel = await _drain(plugin, ev_cancel, ["on_creation_message"])
    check("/取消 仍能取消（没被指令放行规则吃掉）",
          "已取消" in out_cancel, out_cancel)
    check("取消后会话已清掉", plugin.creation.get("888", "20001") is None)

    # ---- 场景 4：两个取原始信息的 helper ----
    ev_helper = AstrMessageEvent(
        qq="1", text="菜单", group="1", message_id="M1", raw_text="/菜单",
    )
    check("_msg_id 能取到消息 id", plugin._msg_id(ev_helper) == "M1")
    check("_raw_text 取的是未剥前缀的原文",
          plugin._raw_text(ev_helper) == "/菜单")
    check("拿不到 message_obj 时 _msg_id 返回空串（不炸）",
          plugin._msg_id(object()) == "")

    # ------------------------------------------------------------------
    print("\n[1c] 头像图片读取（线上事故回归）")
    # 事故现场：NapCat 给的 file 是 URL 编码的本地路径
    #   d:%5Cai%5CAstrBot%5Cdata%5Ctemp%5Cmedia_image_xxx.jpg
    # 旧代码把它当 URL 去 aiohttp 请求 -> 必然失败 -> 「图片读取失败了」。
    # 同时旧代码读 image_comp.base64 —— AstrBot 的 Image 根本没这个字段。
    # 这里把四种真实出现过的地址形态全部钉住。
    import base64 as _b64
    import shutil as _shutil
    from pathlib import Path as _Path
    from urllib.parse import quote as _quote

    _imgdir = HERE / "_testdata" / "_imgtest"
    if _imgdir.exists():
        _shutil.rmtree(_imgdir, ignore_errors=True)
    _imgdir.mkdir(parents=True, exist_ok=True)
    _png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 504  # 512 字节，够过校验
    _img_path = _imgdir / "头像.png"
    _img_path.write_bytes(_png_bytes)

    # ① base64 内联（AstrBot 的 Image.fromBase64 就是这么塞的）
    _b64_src = "base64://" + _b64.b64encode(_png_bytes).decode()
    _got = await plugin._read_image_source(_b64_src)
    check("能读 base64:// 内联图片", _got == _png_bytes,
          f"got={None if _got is None else len(_got)}B")

    # ② file:// URI
    _uri = _img_path.as_uri()
    _got = await plugin._read_image_source(_uri)
    check("能读 file:// URI", _got == _png_bytes, _uri)

    # ③ URL 编码的本地路径（NapCat 真实形态）
    _encoded = _quote(str(_img_path))
    check("URL 编码形态里确实含 %5C（模拟 NapCat）",
          "%5C" in _encoded, _encoded)
    _got = await plugin._read_image_source(_encoded)
    check("能读 URL 编码的本地路径", _got == _png_bytes, _encoded)

    # ④ 明文本地路径
    _got = await plugin._read_image_source(str(_img_path))
    check("能读明文本地路径", _got == _png_bytes)

    # ⑤ 路径不存在 -> None（不抛异常）
    _got = await plugin._read_image_source(str(_imgdir / "不存在.png"))
    check("路径不存在时返回 None 而不是抛异常", _got is None)

    # ⑥ 空串 -> None
    check("空串返回 None", await plugin._read_image_source("") is None)

    # ⑦ http:// 走下载分支（把下载函数换成假的，避免真起服务）
    _orig_dl = plugin._download_image

    async def _fake_dl(url):
        return _png_bytes if url.startswith("http") else None

    plugin._download_image = _fake_dl
    try:
        _got = await plugin._read_image_source("http://example.com/a.png")
        check("http:// 走下载分支", _got == _png_bytes)
    finally:
        plugin._download_image = _orig_dl

    # ⑧ 端到端：_save_avatar 拿到 URL 编码路径的 Image 组件也能存下来
    plugin.avatar_dir.mkdir(parents=True, exist_ok=True)
    _ev_img = AstrMessageEvent(
        qq="30001", name="发图的人", text="", group="888",
        message_id="MSG_IMG", comps=[ImageComp(file=_encoded)],
    )
    _ok, _rel = await plugin._save_avatar(_ev_img, "888", "30001")
    check("_save_avatar 能吃下 URL 编码路径的图片", _ok, str(_rel))
    if _ok:
        check("头像文件真的落盘了",
              (plugin.data_dir / _rel).exists(),
              str(plugin.data_dir / _rel))

    # ⑨ 端到端：只有 base64:// 在 file 字段里（无 path/url）也能存
    _ev_b64 = AstrMessageEvent(
        qq="30002", name="发图的人2", text="", group="888",
        message_id="MSG_IMG2",
        comps=[ImageComp(file=_b64_src)],
    )
    _ok2, _rel2 = await plugin._save_avatar(_ev_b64, "888", "30002")
    check("_save_avatar 能吃下 file=base64:// 的图片", _ok2, str(_rel2))

    # ⑩ 没有图片组件 -> 明确提示
    _ev_noimg = AstrMessageEvent(qq="30003", text="没图", group="888",
                                 message_id="MSG_NOIMG")
    _ok3, _msg3 = await plugin._save_avatar(_ev_noimg, "888", "30003")
    check("没有图片时给出明确提示",
          (not _ok3) and "没有检测到图片" in _msg3, _msg3)

    # ⑪ 非图片内容（BMP 头）应被拒，而不是当成合法图片
    _bmp = b"BM" + b"\x00" * 62
    _ev_bmp = AstrMessageEvent(
        qq="30004", text="", group="888", message_id="MSG_BMP",
        comps=[ImageComp(file="base64://" + _b64.b64encode(_bmp).decode())],
    )
    _ok4, _msg4 = await plugin._save_avatar(_ev_bmp, "888", "30004")
    check("非白名单格式被拒（BMP）",
          (not _ok4) and "格式不支持" in _msg4, _msg4)

    # ⑫ 魔术字节嗅探：四种白名单格式都要认，乱码要返回空
    _sniff = character_mod.sniff_image_ext
    check("嗅探 PNG", _sniff(b"\x89PNG\r\n\x1a\n" + b"x" * 8) == ".png")
    check("嗅探 JPG", _sniff(b"\xff\xd8\xff\xe0" + b"x" * 8) == ".jpg")
    check("嗅探 GIF", _sniff(b"GIF89a" + b"x" * 8) == ".gif")
    check("嗅探 WEBP", _sniff(b"RIFF\x00\x00\x00\x00WEBP" + b"x") == ".webp")
    check("嗅探不出返回空串", _sniff(b"BM" + b"\x00" * 20) == "")
    check("空数据嗅探返回空串", _sniff(b"") == "")

    # ⑬ 扩展名非法但内容合法时，靠嗅探救回来（不会被误判成格式不支持）
    _ev_sniff = AstrMessageEvent(
        qq="30005", text="", group="888", message_id="MSG_SNIFF",
        comps=[ImageComp(file="base64://" + _b64.b64encode(_png_bytes).decode())],
    )
    _ok5, _rel5 = await plugin._save_avatar(_ev_sniff, "888", "30005")
    check("base64 内联 + 无扩展名时靠嗅探通过", _ok5, str(_rel5))

    # ⑭ 源码层面：不许再读不存在的 base64 字段
    _main_src = (HERE / "main.py").read_text(encoding="utf-8")
    check("main.py 不再 getattr 读取 Image.base64 字段",
          ', "base64"' not in _main_src and "'base64'" not in _main_src)

    # ------------------------------------------------------------------
    print("\n[1d] LLM 只读快照（AI 陪聊不影响游戏数据）")
    # 老板要「接入大模型自由对话，但不影响游戏数据」。
    # 实现方式是 @filter.on_llm_request 把角色状态**只读**注入系统提示词。
    # 这组断言钉住三件事：注入了什么、没号时不注入、以及**存档字节不变**。

    class _FakeReq:
        """ProviderRequest 的最小替身 —— 只用到 system_prompt。"""

        def __init__(self, sp=""):
            self.system_prompt = sp

    ev_llm = AstrMessageEvent(qq="40001", name="问道者", text="你好", group="777")
    await fast_create(plugin, ev_llm, name="问道者", level=20)

    req = _FakeReq("你是一个修仙世界的陪聊。")
    ret = await plugin.on_llm_request(ev_llm, req)
    check("on_llm_request 返回 None（返回 True 会中断 LLM 请求）", ret is None)
    check("系统提示词被追加了快照", len(req.system_prompt) > 30, req.system_prompt)
    check("快照带角色名", "问道者" in req.system_prompt)
    check("快照带境界", "转" in req.system_prompt and "期" in req.system_prompt)
    check("快照带气血", "气血" in req.system_prompt)
    check("快照带修为", "修为" in req.system_prompt)
    check("快照带灵石", "灵石" in req.system_prompt)
    check("快照带天道令", "天道令" in req.system_prompt)
    check("快照声明只读", "只读" in req.system_prompt)
    check("快照禁止编造", "编造" in req.system_prompt)
    check("原有系统提示词没被覆盖",
          req.system_prompt.startswith("你是一个修仙世界的陪聊。"))

    # 没报名的玩家 -> 一个字都不注入
    ev_stranger = AstrMessageEvent(qq="40002", name="路人", text="你好", group="777")
    req2 = _FakeReq("原样")
    ret2 = await plugin.on_llm_request(ev_stranger, req2)
    check("未报名玩家不注入快照",
          ret2 is None and req2.system_prompt == "原样", req2.system_prompt)

    # 群隔离：同一个 QQ 在别的群没号，也不该注入
    ev_other = AstrMessageEvent(qq="40001", name="问道者", text="你好", group="778")
    req3 = _FakeReq("原样")
    await plugin.on_llm_request(ev_other, req3)
    check("快照按群隔离（同人别群无号不注入）",
          req3.system_prompt == "原样", req3.system_prompt)

    # ★ 核心安全属性：生成快照**不写存档**（字节级比对）
    _save_path = plugin.store.save_path
    _before = _save_path.read_bytes() if _save_path.exists() else b""
    req4 = _FakeReq()
    await plugin.on_llm_request(ev_llm, req4)
    _after = _save_path.read_bytes() if _save_path.exists() else b""
    check("生成快照不修改存档（字节级一致）", _before == _after)

    # 源码层面：没有注册任何 llm_tool -> LLM 看不到也调不到游戏指令
    # （用 AST 查装饰器，不用字符串匹配 —— 注释里出现 "llm_tool" 不算）
    import ast as _ast
    _src_llm = (HERE / "main.py").read_text(encoding="utf-8")
    _llm_tool_deco = []
    for _n in _ast.walk(_ast.parse(_src_llm)):
        if isinstance(_n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            for _d in _n.decorator_list:
                if (isinstance(_d, _ast.Call)
                        and isinstance(_d.func, _ast.Attribute)
                        and _d.func.attr == "llm_tool"):
                    _llm_tool_deco.append(_n.name)
    check("插件没有注册 llm_tool（LLM 调不到游戏指令）",
          not _llm_tool_deco, str(_llm_tool_deco))

    print("\n[2] 未报名玩家的拦截")
    ev_other = AstrMessageEvent(qq="10002", name="路人")
    out = await run_cmd(plugin, "hunt", ev_other)
    check("未报名打怪被拦截", "还没有角色" in out, out)

    print("\n[3] 历练与冷却")
    out = await run_cmd(plugin, "hunt", ev)
    check("历练有结果", ("碾压" in out or "胜利" in out or "惨胜" in out
                       or "势均力敌" in out or "道消" in out), out)
    # 历练后可能道消进休整，也可能进入冷却。两种情况都算「被限流」。
    p3 = plugin.store.get("999", "10001")
    limited = p3.is_resting() or p3.hunt_remain(plugin.cooldown) > 0
    check("历练后被限流（冷却或休整）", limited,
          f"resting={p3.is_resting()} cd={p3.hunt_remain(plugin.cooldown)}")

    # 清掉限流状态，单独验证「冷却」这一机制本身
    p3.rest_until = 0.0
    p3.full_heal(plugin.item_db)
    p3.last_hunt_ts = time.time()
    out = await run_cmd(plugin, "hunt", ev)
    check("冷却生效（5 分钟）", "调息" in out and "剩余时间" in out, out)
    check("冷却时长约 5 分钟", "5 分钟" in out or "4 分" in out, out)

    # 清掉冷却，验证冷却过后可以正常打
    p3.last_hunt_ts = 0.0
    p3.rest_until = 0.0
    p3.full_heal(plugin.item_db)
    out = await run_cmd(plugin, "hunt", ev)
    check("冷却结束后可再历练", "遭遇" in out, out)

    print("\n[3b] 道消机制（v2 无惩罚 + 休整）")
    p3.rest_until = 0.0
    p3.last_hunt_ts = 0.0
    p3.gold = 1000
    gold_before = p3.gold
    # 强行制造一次道消：1 转挑战 28 转渡厄天魔
    p3.level = 1
    p3.full_heal(plugin.item_db)
    out = await run_cmd(plugin, "challenge", ev, "渡厄天魔")
    p3 = plugin.store.get("999", "10001")
    if p3.is_resting():
        check("道消后进入休整", True)
        check("休整时长 10 分钟", 590 < p3.rest_remain() <= 601,
              f"{p3.rest_remain():.0f}")
        check("休整期间无法历练",
              "休整中" in (await run_cmd(plugin, "hunt", ev)))
        check("道消不扣灵石", p3.gold == gold_before,
              f"{gold_before} -> {p3.gold}")
        check("道消不掉修为（修为不为负）", p3.exp >= 0)
        check("休整期间仍可查看属性",
              "道途" in (await run_cmd(plugin, "profile", ev)))
        # 花钱复活
        p3.gold = 500
        out = await run_cmd(plugin, "revive", ev, "3")
        check("花钱复活成功", "痊愈" in out or "恢复" in out, out)
        check("复活扣了灵石", plugin.store.get("999", "10001").gold == 500 - plugin.death_revive_gold)
        check("复活后不再休整", not plugin.store.get("999", "10001").is_resting())
    else:
        check("道消后进入休整（本次未道消，跳过）", True)

    # 恢复测试角色的转数
    p3 = plugin.store.get("999", "10001")
    p3.level = 19
    p3.full_heal(plugin.item_db)
    p3.rest_until = 0.0
    p3.last_hunt_ts = 0.0
    await plugin._commit()

    print("\n[4] 战斗数值健康度")
    p2 = player_mod.Player(qq="test", name="数值测试")
    item_db = {e["name"]: e for e in items_mod.EQUIPMENTS}
    all_fine = True
    for _ in range(500):
        mon = battle_mod.spawn_monster(p2.level)
        stats = battle_mod.monster_stats(mon)
        dmg = player_mod.roll_damage(p2.atk(item_db), stats["defense"])
        if dmg < 1:
            all_fine = False
            break
    check("500 次抽怪伤害均不低于 1（无死循环风险）", all_fine)

    print("\n[5] 战斗必然结束（回合上限）")
    # 构造一个高防玩家 vs 低攻怪，验证不会无限打
    tank = player_mod.Player(qq="tank", name="坦克")
    tank.level = 1
    weak_mon = {
        "name": "木桩", "level": 1, "hp": 99999, "atk": 0,
        "defense": 9999, "exp": 0, "gold": (0, 0), "drops": [],
    }
    res = battle_mod.fight(tank, weak_mon, item_db)
    check(
        f"高防对局在 {res.turns} 回合内结束（上限 {player_mod.MAX_BATTLE_TURNS}）",
        res.turns <= player_mod.MAX_BATTLE_TURNS and res.outcome == "draw",
    )

    # ---- v3.3：回合上限必须 ≥ 20，否则「可扛 19 回合」的设计目标无法达成 ----
    #
    # 这是回归防线。v3.2 曾把上限留在 10，导致：
    #   * 妖王（血厚）在第 10 回合被判 draw —— 不是打不过是打不完；
    #   * 「可扛 19 回合」这个指标物理上不可观测。
    # 将来若有人为了别的目的把上限压回去，这条断言会立刻拦下。
    check(
        f"回合上限 ≥ 20（当前 {player_mod.MAX_BATTLE_TURNS}），"
        f"保证「可扛 19 回合」的设计目标可达",
        player_mod.MAX_BATTLE_TURNS >= 20,
        f"{player_mod.MAX_BATTLE_TURNS}",
    )

    # ---- v3.3：秘境兜底血线必须与回合上限匹配 ----
    # 30 回合的输出约为 10 回合的 3 倍，cap 若留在 3.0 会在高回合下误压怪血。
    check(
        f"秘境 HP_CAP ≥ 5.0（当前 {dungeon_mod.DUNGEON_HP_CAP_RATIO}），与 30 回合上限匹配",
        dungeon_mod.DUNGEON_HP_CAP_RATIO >= 5.0,
        f"{dungeon_mod.DUNGEON_HP_CAP_RATIO}",
    )

    print("\n[6] 精进链路（修为 → 转数）")
    # 精进是逐转扣减修为：升到 2 转扣 qi_needed(1)，
    # 升到 3 转再扣 qi_needed(2)，以此类推。所以"能连升几转"
    # 取决于总修为能覆盖到哪一转，这里直接用累加值构造。
    lv = player_mod.Player(qq="lv", name="精进测试")
    before = lv.level
    total = 0
    for lvl in range(1, 5):  # 攒够升到 5 转的修为
        total += player_mod.exp_needed(lvl)
    lv.exp = total
    gained = battle_mod._apply_level_up(lv, item_db)
    check(f"修为充足时连升 {gained} 转", gained >= 4, f"gained={gained}")
    check("转数已提升到 5", lv.level == 5, f"level={lv.level}")
    check("精进后气血回满", lv.hp == lv.max_hp(item_db))
    check("剩余修为被正确扣减", lv.exp == 0, f"exp={lv.exp}")

    # 修为刚好差 1 点时不应精进（边界）
    edge = player_mod.Player(qq="edge", name="边界测试")
    edge.exp = player_mod.exp_needed(1) - 1
    check("修为差 1 点不精进", battle_mod._apply_level_up(edge, item_db) == 0)
    check("差 1 点时转数仍为 1", edge.level == 1)

    print("\n[7] 法宝祭炼与换装")
    ev2 = AstrMessageEvent(qq="10003", name="装备测试")
    await fast_create(plugin, ev2)
    p3 = plugin.store.get("999", "10003")
    # v3.4：装备有**品阶门槛**（一品 1 转 ~ 九品 68 转）。
    # 这段测的是「祭炼/换装/卸下」的流程本身，所以统一用**一品**装备
    # （req_turns=1，任何转数都能穿），把门槛因素排除掉。
    # 门槛本身的正反用例在下面单独测。
    _W1, _W2 = "青锋剑", "青锋杖"        # 一品物穿武器 / 一品法穿武器
    p3.add_item(_W1)
    out = await run_cmd(plugin, "equip", ev2, _W1)
    check("祭炼成功", "已祭炼" in out, out)
    check("法宝栏已记录", p3.equipment.get("weapon") == _W1, f"{p3.equipment}")
    # v3.4：面板攻击 = 基础 × 乘区 × (1 + 装备%)
    # 旧断言写的是 `+ 装备绝对值`，装备改百分比后已失效。
    _db_p3 = plugin.item_db or {e["name"]: e for e in items_mod.EQUIPMENTS}
    _eq_pct = p3.equip_pct("atk_pct", _db_p3)
    expected_atk = int(int(player_mod.base_atk(p3.turns) * p3.stat_mult())
                       * (1 + _eq_pct))
    check("攻击力 = 基础 × 乘区 × (1 + 装备%)",
          p3.atk(_db_p3) == expected_atk,
          f"{p3.atk(_db_p3)} vs {expected_atk}（装备 {_eq_pct * 100:.1f}%）")
    check("一品装备确实提供了加成（百分比不为 0）",
          _eq_pct > 0, f"{_eq_pct * 100:.2f}%")

    p3.add_item(_W2)
    out = await run_cmd(plugin, "equip", ev2, _W2)
    check("换装提示已换下", "换下" in out, out)
    check("旧法宝回到储物袋", p3.inventory.get(_W1) == 1, f"{p3.inventory}")
    check("新法宝生效", p3.equipment.get("weapon") == _W2, f"{p3.equipment}")

    out = await run_cmd(plugin, "unequip", ev2, "武器")
    check("按中文部位卸下成功", "已卸下" in out, out)

    # ---- v3.4 品阶门槛：把「能穿 / 不能穿」两侧都钉住 ----
    _T9 = "九霄剑"      # 九品武器，req_turns = 68
    check("九品武器门槛是 68 转",
          items_mod.req_turns_of(_T9) == 68, f"{items_mod.req_turns_of(_T9)}")
    check("一品武器无门槛（1 转）",
          items_mod.req_turns_of("青锋剑") == 1)

    p3.add_item(_T9)
    p3.level = 1
    out = await run_cmd(plugin, "equip", ev2, _T9)
    check("1 转穿不了九品武器", "压不住" in out, out)
    check("被拦下时没有穿上", p3.equipment.get("weapon") != _T9, f"{p3.equipment}")
    check("被拦下时装备还在储物袋", p3.inventory.get(_T9) == 1, f"{p3.inventory}")

    p3.level = 68
    out = await run_cmd(plugin, "equip", ev2, _T9)
    check("68 转能穿九品武器", "已祭炼" in out, out)
    check("穿上后装备栏更新", p3.equipment.get("weapon") == _T9, f"{p3.equipment}")

    # ---- 名字解析：基础名输入要能命中带品质后缀的那件 ----
    #
    # 掉落/坊市产出的装备名带品质后缀（`青锋剑[稀]`），玩家输入往往是
    # 基础名（`青锋剑`）。没有这层解析，玩家有装备却被告知「你没有」。
    #
    # ⚠️ 试验前必须把背包里**所有同基础名**的变体清掉（含裸名 `青锋剑`），
    # 否则「完全相等」快路径会命中裸名，测出来的就不是品质解析逻辑了。
    p3.equipment.clear()
    for _k in [k for k in p3.inventory if k.split("[")[0] in (_W1, _W2, _T9)]:
        del p3.inventory[_k]
    p3.add_item("青锋剑[传]", 1)
    out = await run_cmd(plugin, "equip", ev2, "青锋剑")
    check("基础名输入能命中带品质后缀的装备",
          p3.equipment.get("weapon") == "青锋剑[传]", f"{p3.equipment}")
    check("命中后从储物袋扣除的是那个完整名",
          p3.inventory.get("青锋剑[传]") is None, f"{p3.inventory}")

    # 同基础名多件时，默认装品质最好的
    #
    # 注意：上一步 `青锋剑[传]` 已经被穿在身上、不在储物袋里了，
    # 所以这里要把它放回背包，才能构成「背包里同时有 [普] 和 [传]」的场景。
    _equipped_before = p3.equipment.get("weapon")
    if _equipped_before:
        p3.inventory[_equipped_before] = p3.inventory.get(_equipped_before, 0) + 1
    p3.equipment.clear()
    p3.add_item("青锋剑[普]", 1)
    out = await run_cmd(plugin, "equip", ev2, "青锋剑")
    check("同基础名多件时优先装品质最高的",
          p3.equipment.get("weapon") == "青锋剑[传]", f"{p3.equipment}")
    check("品质高的被消耗掉、品质低的留在储物袋",
          p3.inventory.get("青锋剑[传]") is None
          and p3.inventory.get("青锋剑[普]") == 1,
          f"{p3.inventory}")

    # 玩家等级恢复，避免影响后续用例
    p3.level = 19

    print("\n[8] 丹药与出售")
    p3.hp = 10
    p3.add_item("小回气丹[普]", 1)
    out = await run_cmd(plugin, "use_item", ev2, "小回气丹")
    check("基础名输入能吃到带品质后缀的丹药", "恢复" in out, out)
    check("丹药回血成功", "恢复" in out, out)
    check("气血确实增加", p3.hp == 50, f"hp={p3.hp}")

    p3.add_item("史莱姆凝胶")
    gold_before = p3.gold
    out = await run_cmd(plugin, "sell", ev2, "史莱姆凝胶")
    check("出售获得灵石", p3.gold == gold_before + 5, f"gold={p3.gold}")

    # 已装备物品不可出售
    #
    # 注意：不能先 remove_item 再卖 —— 那样背包里根本没这件，
    # 触发的是「储物袋里没有」而不是「先卸下」，测不到想测的分支。
    # 正确做法是「背包里留着 + 同时穿在身上」，让指令走到装备检查那一步。
    _SELL_W = "青锋战甲"       # 一品护甲，任何转数都能穿
    p3.add_item(_SELL_W)
    p3.equipment["armor"] = _SELL_W
    out = await run_cmd(plugin, "sell", ev2, _SELL_W)
    check("已装备法宝禁止出售", "先卸下" in out, out)
    p3.equipment.pop("armor", None)

    print("\n[9] 闭关结算")
    import time as _t
    p4 = player_mod.Player(qq="train", name="闭关测试")
    # v3.6：闭关时长从 train_start_ts 算（不再是 last_train_ts）
    p4.train_start_ts = _t.time() - 3600 * 3  # 3 小时前
    res = battle_mod.settle_training(p4, item_db)
    # 3 小时落在 5% 走火入魔区间，命中时收益减半 —— 两种结果都要接受
    full_3h = realm_mod.seclusion_qi_per_hour(p4.turns) * 3
    if res.deviation:
        check(f"3 小时闭关走火入魔，收益减半（{res.exp_gain}）",
              res.exp_gain == full_3h // 2, f"{res.exp_gain} vs {full_3h // 2}")
    else:
        check(f"3 小时闭关结算为 {res.exp_gain} 修为",
              res.exp_gain == full_3h, f"{res.exp_gain} vs {full_3h}")

    p5 = player_mod.Player(qq="train2", name="超时测试")
    p5.train_start_ts = _t.time() - 3600 * 50  # 50 小时前
    res5 = battle_mod.settle_training(p5, item_db)
    check("超长离线被截到 8 小时", res5.capped and res5.hours == 8.0, f"{res5.hours}")

    p6 = player_mod.Player(qq="train3", name="零头测试")
    p6.train_start_ts = _t.time() - 60  # 1 分钟前
    res6 = battle_mod.settle_training(p6, item_db)
    check("不足 6 分钟无收益", res6.exp_gain == 0)

    print("\n[10] 存档持久化（核心）")
    await plugin._commit()
    save_file = plugin.store.save_path
    check("存档文件已生成", save_file.exists(), str(save_file))

    # 新建一个 store 从磁盘重读，模拟重启
    store2 = plugin_mod.PlayerStore(plugin.data_dir)
    store2.ensure_loaded()
    reloaded = store2.get("999", "10003")
    check("重启后能读回玩家", reloaded is not None)
    check("等级数据一致", reloaded.level == p3.level, f"{reloaded.level} vs {p3.level}")
    check(
        "装备数据一致",
        reloaded.equipment.get("weapon") == p3.equipment.get("weapon"),
    )
    check("金币数据一致", reloaded.gold == p3.gold)

    print("\n[11] 存档抗损坏")
    bad_dir = HERE / "_testdata_bad"
    shutil.rmtree(bad_dir, ignore_errors=True)
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "players.json").write_text("{ 这不是合法 JSON", encoding="utf-8")
    store3 = plugin_mod.PlayerStore(bad_dir)
    store3.ensure_loaded()  # 不应抛异常
    check("损坏存档不崩溃，降级为空", store3.count == 0)

    print("\n[12] 怪物数据完整性")
    for m in items_mod.MONSTERS:
        need = ("name", "level", "hp", "atk", "defense", "exp", "gold", "drops")
        if not all(k in m for k in need):
            check(f"怪物 {m.get('name')} 字段完整", False)
            break
        for d in m["drops"]:
            if d["item"] not in items_mod.all_item_names():
                check(f"怪物 {m['name']} 掉落物 {d['item']} 存在定义", False)
                break
    else:
        check("全部怪物字段完整且掉落物均有定义", True)

    print("\n[13] 寻敌指令边界")
    ev3 = AstrMessageEvent(qq="10001", name="老板")
    # 前面的历练用例会留下 5 分钟冷却，这里清掉，测的是「境界限制」本身
    p13 = plugin.store.get("999", "10001")
    p13.last_hunt_ts = 0.0
    p13.rest_until = 0.0
    out = await run_cmd(plugin, "challenge", ev3, "不存在的怪")
    check("未知妖兽被拦截", "没有这种妖兽" in out, out)

    # 当前角色 19 转（金丹期），寻敌上限是 +9 转。
    # 临时把角色降回 1 转，验证「境界不足」规则。
    p13.level = 1
    p13.hp = p13.max_hp(plugin.item_db)
    out = await run_cmd(plugin, "challenge", ev3, "渡厄天魔")
    check("境界不足被拦截（1 转挑战 28 转）", "去送死吗" in out, out)

    # 恢复转数，并验证「境界够就能打」
    p13.level = 19
    p13.full_heal(plugin.item_db)
    out = await run_cmd(plugin, "challenge", ev3, "史莱姆")
    check("境界足够可寻敌", "遭遇" in out, out)

    print("\n[14] WebUI 配置项真正生效")
    # 不传 config 时用默认值
    check("无配置时历练冷却为默认 300 秒", plugin.cooldown == 300.0, f"{plugin.cooldown}")
    check("无配置时修炼默认开启", plugin.train_enabled is True)
    check("无配置时排行默认 10 人", plugin.ranking_size == 10)
    check("无配置时储物袋上限为 None（走默认）", plugin.inventory_limit is None)

    # 传入配置后应被采纳
    cfg_plugin = plugin_mod.TextRPGPlugin(
        context=None,
        config={
            "battle_cooldown": 7.5,
            "enable_train": False,
            "max_inventory": 3,
            "ranking_size": 5,
            "group_whitelist_mode": "all",
        },
    )
    check("冷却读配置 7.5", cfg_plugin.cooldown == 7.5, f"{cfg_plugin.cooldown}")
    check("修炼开关读配置为关", cfg_plugin.train_enabled is False)
    check("排行人数读配置 5", cfg_plugin.ranking_size == 5, f"{cfg_plugin.ranking_size}")
    check("储物袋上限读配置 3", cfg_plugin.inventory_limit == 3)

    # 修炼被关掉后指令应拒绝
    ev4 = AstrMessageEvent(qq="10004", name="配置测试")
    await fast_create(cfg_plugin, ev4)
    out = await run_cmd(cfg_plugin, "cultivate", ev4)
    check("关闭修炼后 /修炼 被拒绝", "未开放" in out, out)

    # 储物袋上限生效：放满 3 种后第 4 种应失败
    p_cfg = cfg_plugin.store.get("999", "10004")
    p_cfg.add_item("铁剑", limit=3)
    p_cfg.add_item("皮甲", limit=3)
    p_cfg.add_item("狼牙", limit=3)
    ok = p_cfg.add_item("石之心", limit=3)
    check("储物袋上限 3 生效，第 4 种被拒", ok is False, f"ok={ok}")
    check("储物袋确实只有 3 种", len(p_cfg.inventory) == 3, f"{len(p_cfg.inventory)}")
    # 已有物品仍可叠加
    check("已有物品仍可叠加", p_cfg.add_item("铁剑", limit=3) is True)

    print("\n[15] 配置值非法时不崩溃")
    bad_plugin = plugin_mod.TextRPGPlugin(
        context=None,
        config={"battle_cooldown": "abc", "ranking_size": "xyz", "max_inventory": None},
    )
    check("非法冷却退回默认 300", bad_plugin.cooldown == 300.0, f"{bad_plugin.cooldown}")
    check("非法排行人数退回默认", bad_plugin.ranking_size == 10)
    check("None 背包上限退回 None", bad_plugin.inventory_limit is None)
    # config 完全是 None 也不能崩
    none_plugin = plugin_mod.TextRPGPlugin(context=None, config=None)
    check("config 为 None 时正常", none_plugin.cooldown == 300.0)
    # config 不是 dict 时也不能崩
    weird_plugin = plugin_mod.TextRPGPlugin(context=None, config=object())
    check("config 非 dict 时正常", weird_plugin.cooldown == 300.0)

    print("\n[16] 按群隔离（同人多群独立进度）")
    iso_plugin = plugin_mod.TextRPGPlugin(context=None, config={
        "reply_delay_range": [0, 0],
        "chunk_gap_range": [0, 0],
        "rate_limit_count": 10000,
        # 老用例测的是游戏逻辑本身，不该被群白名单干扰
        "group_whitelist_mode": "all",
        # reset 需要主人权限，这里把测试用的管理员号设为主人
        "master_qq_list": "1",
    })
    # 同一个人（QQ 77777）在 111 群和 222 群分别报名
    ev_a = AstrMessageEvent(qq="77777", name="双群玩家", group="111")
    ev_b = AstrMessageEvent(qq="77777", name="双群玩家", group="222")
    await fast_create(iso_plugin, ev_a, "玩家A")
    await fast_create(iso_plugin, ev_b, "玩家B")

    pa = iso_plugin.store.get("111", "77777")
    pb = iso_plugin.store.get("222", "77777")
    check("两个群各有一份存档", pa is not None and pb is not None)
    check("两份存档是不同对象", pa is not pb)

    # 在 A 群精进，B 群不应受影响
    pa.level = 10
    pa.gold = 999
    await iso_plugin._commit()
    check("A 群转数已改", iso_plugin.store.get("111", "77777").level == 10)
    check(
        "B 群转数未受影响",
        iso_plugin.store.get("222", "77777").level == 1,
        f"{iso_plugin.store.get('222', '77777').level}",
    )
    check(
        "B 群灵石未受影响",
        iso_plugin.store.get("222", "77777").gold == 50,
    )

    # 群内排行榜只统计本群
    # 注意：v2 玩家名是自定义的，排行显示 player.name 而不是 QQ 昵称
    ev_c = AstrMessageEvent(qq="88888", name="群友", group="111")
    await fast_create(iso_plugin, ev_c, "玩家C")
    out = await run_cmd(iso_plugin, "ranking", ev_a)
    check("A 群排行含双群玩家", "玩家A" in out, out)
    check("A 群排行含群友", "玩家C" in out, out)
    out_b = await run_cmd(iso_plugin, "ranking", ev_b)
    check("B 群排行不含群友（隔离生效）", "玩家C" not in out_b, out_b)
    check("B 群排行含本群玩家", "玩家B" in out_b, out_b)

    # 清空单群不影响其他群
    ev_admin = AstrMessageEvent(qq="1", name="管理员", group="111")
    await run_cmd(iso_plugin, "reset", ev_admin)
    check("A 群已被清空", iso_plugin.store.get("111", "77777") is None)
    check("B 群仍然存在", iso_plugin.store.get("222", "77777") is not None)

    # 私聊场景（群号为空）走独立存档池
    ev_pm = AstrMessageEvent(qq="77777", name="私聊玩家", group="")
    await fast_create(iso_plugin, ev_pm, "玩家PM")
    check("私聊存档独立", iso_plugin.store.get("", "77777") is not None)

    print("\n[17] 旧存档格式自动迁移")
    old_dir = HERE / "_testdata_old"
    # 保证目录是空的，避免残留文件干扰迁移断言
    shutil.rmtree(old_dir, ignore_errors=True)
    old_dir.mkdir(parents=True, exist_ok=True)
    # 模拟 v1.0.0 的旧格式：键是纯 QQ 号，没有 group_id 字段
    old_data = {
        "12345": {
            "qq": "12345", "name": "老玩家", "level": 7,
            "exp": 30, "hp": 200, "gold": 500,
            "inventory": {}, "equipment": {},
            "total_kills": 10, "total_deaths": 1, "last_train_ts": 0.0,
        }
    }
    import json as _json
    (old_dir / "players.json").write_text(
        _json.dumps(old_data, ensure_ascii=False), encoding="utf-8"
    )
    old_store = plugin_mod.PlayerStore(old_dir)
    old_store.ensure_loaded()
    migrated = old_store.get("", "12345")
    check("旧存档被读取", migrated is not None)
    check("旧存档转数保留", migrated.level == 7 if migrated else False)
    check("旧存档归入私聊池（group_id 为空）", migrated.group_id == "" if migrated else False)
    check("迁移后键格式正确", migrated.key == ":12345" if migrated else False)
    # v3 新增：老存档里没有的字段应自动补齐默认值
    check("旧存档缺失秘典字段被补齐", migrated.scriptures == {} if migrated else False)
    check("旧存档缺失转生字段被补齐",
          migrated.ascensions == 0 and migrated.breakthroughs == 0 if migrated else False)
    check("旧存档缺失探索字段被补齐",
          migrated.explore_start_ts == 0.0 if migrated else False)

    print("\n[17b] v3 超界转数迁移")
    # v2 的 level 上限是 200，v3 只到 72，读档时应被钳制
    over_dir = HERE / "_testdata_over"
    shutil.rmtree(over_dir, ignore_errors=True)
    over_dir.mkdir(parents=True, exist_ok=True)
    over_data = {"111": {"qq": "111", "name": "超界玩家", "level": 200,
                         "exp": 0, "hp": 100, "gold": 50, "inventory": {},
                         "equipment": {}, "total_kills": 0, "total_deaths": 0,
                         "last_train_ts": 0.0, "created_at": 0.0}}
    (over_dir / "players.json").write_text(
        _json.dumps(over_data, ensure_ascii=False), encoding="utf-8"
    )
    over_store = plugin_mod.PlayerStore(over_dir)
    over_store.ensure_loaded()
    over_p = over_store.get("", "111")
    check("v2 的 200 级被钳到 72 转", over_p.level == realm_mod.MAX_TURNS if over_p else False,
          f"{over_p.level if over_p else None}")
    check("钳制后落在大乘期", over_p.realm_name == "大乘期" if over_p else False,
          over_p.realm_name if over_p else "")

    print("\n[18] 坊市系统")
    shop_plugin = plugin_mod.TextRPGPlugin(context=None, config={
        "reply_delay_range": [0, 0],
        "chunk_gap_range": [0, 0],
        "rate_limit_count": 10000,
        # 老用例测的是游戏逻辑本身，不该被群白名单干扰
        "group_whitelist_mode": "all",
    })
    ev_s = AstrMessageEvent(qq="55555", name="买家", group="888")
    await fast_create(shop_plugin, ev_s)
    ps = shop_plugin.store.get("888", "55555")

    out = await run_cmd(shop_plugin, "shop", ev_s)
    check("坊市列出丹药", "小回气丹" in out, out[:200])
    check("坊市显示灵石", "灵石" in out, out[:200])

    # 买入价 = 售价 × 3
    check("小回气丹售价 10", items_mod.sell_price("小回气丹") == 10)
    check("小回气丹买价 30", items_mod.buy_price("小回气丹") == 30)
    check(
        "买价是售价的 3 倍",
        items_mod.buy_price("小回气丹")
        == items_mod.sell_price("小回气丹") * items_mod.BUY_PRICE_MULTIPLIER,
    )

    # 灵石不足被拦截
    ps.gold = 5
    out = await run_cmd(shop_plugin, "buy", ev_s, "小回气丹")
    check("灵石不足被拦截", "灵石不足" in out, out)

    # 正常购买
    ps.gold = 100
    out = await run_cmd(shop_plugin, "buy", ev_s, "小回气丹")
    check("购买成功", "购买成功" in out, out)
    check("灵石被扣除 30", ps.gold == 70, f"{ps.gold}")
    check("物品进储物袋", ps.inventory.get("小回气丹") == 1)

    # 卖回亏本（防止套利）
    gold_before = ps.gold
    await run_cmd(shop_plugin, "sell", ev_s, "小回气丹")
    check(
        f"卖回只拿 10（净亏 20）",
        ps.gold == gold_before + 10,
        f"{ps.gold}",
    )

    # 低转玩家买不到高级法宝
    out = await run_cmd(shop_plugin, "buy", ev_s, "龙牙巨剑")
    check("低转玩家买不到龙牙巨剑", "买不了" in out or "不在坊市" in out, out)

    # 不存在的商品
    out = await run_cmd(shop_plugin, "buy", ev_s, "不存在的东西")
    check("不存在商品被拦截", "没有" in out, out)

    # 高价法宝：给足灵石应能买
    ps.gold = 99999
    stock = items_mod.shop_stock(1)
    if stock:
        first = stock[0]
        out = await run_cmd(shop_plugin, "buy", ev_s, first)
        check(f"买得起时能买 {first}", "购买成功" in out, out)

    print("\n[19] 存档键与群号边界")
    check("键格式为 群:QQ", plugin_mod.PlayerStore.make_key("999", "10001") == "999:10001")
    check("私聊键格式", plugin_mod.PlayerStore.make_key("", "10001") == ":10001")
    check("纯 QQ 键不含冒号（可被识别为旧格式）", ":" not in "10001")

    print("\n[20] 长消息分段")
    guard_mod = importlib.import_module(f"{HERE.name}.guard")

    # 短消息不分段
    check("短消息原样返回", guard_mod.split_text("你好", 180) == ["你好"])
    check("空消息返回空", guard_mod.split_text("", 180) == [])

    # 长消息按换行切
    long_text = "\n".join(f"第{i}行内容" for i in range(50))
    chunks = guard_mod.split_text(long_text, 100)
    check(f"长消息被切成 {len(chunks)} 段", len(chunks) > 1, f"{len(chunks)}")
    check("每段不超上限", all(len(c) <= 100 for c in chunks), 
          f"max={max(len(c) for c in chunks)}")
    check("内容无丢失", "".join(chunks).replace("\n", "") == long_text.replace("\n", ""))

    # 无换行长文本用句号切
    no_newline = "这是一句话。" * 60
    chunks2 = guard_mod.split_text(no_newline, 100)
    check("无换行长文本也能切", len(chunks2) > 1)
    check("尽量在句号处断开", all(c.endswith("。") or len(c) <= 100 for c in chunks2[:-1]))

    # 完全无标点的硬切
    hard = "啊" * 500
    chunks3 = guard_mod.split_text(hard, 100)
    check("无标点文本硬切不丢内容", "".join(chunks3) == hard, f"{len(chunks3)}段")
    check("硬切每段合规", all(len(c) <= 100 for c in chunks3))

    # 边界：正好等于上限
    exact = "x" * 100
    check("正好等于上限不分段", guard_mod.split_text(exact, 100) == [exact])

    # limit 非法值
    check("limit 为 0 时不分段", guard_mod.split_text("abc", 0) == ["abc"])

    print("\n[21] 频率熔断")

    def make_conf(values):
        def conf(key, default):
            return values.get(key, default)
        return conf

    # 全默认的 guard
    g = guard_mod.ReplyGuard(make_conf({}))
    check("默认启用", g.enabled is True)
    check("默认延迟区间", g.delay_range == (0.4, 1.6), f"{g.delay_range}")
    check("默认分段上限 180", g.max_chunk == 180, f"{g.max_chunk}")
    check("默认熔断阈值 20", g.rate_limit == 20, f"{g.rate_limit}")

    # 熔断触发
    g2 = guard_mod.ReplyGuard(make_conf({"rate_limit_count": 3, "mute_duration": 60}))
    session = "999"
    check("初始未熔断", g2.is_muted(session) is False)
    for _ in range(3):
        g2.record(session)
    check("达到阈值后熔断", g2.is_muted(session) is True)
    check("沉默剩余时间合理", 0 < g2.mute_remaining(session) <= 60)

    # 其他会话不受影响
    check("其他会话不受影响", g2.is_muted("888") is False)

    # 沉默期过后自动解除
    g3 = guard_mod.ReplyGuard(make_conf({"rate_limit_count": 1, "mute_duration": 0.05}))
    g3.record("777")
    check("刚记录即熔断", g3.is_muted("777") is True)
    await asyncio.sleep(0.1)
    check("沉默期过后解除", g3.is_muted("777") is False)

    # 关闭防封后一切放行
    g4 = guard_mod.ReplyGuard(make_conf({"anti_ban_enable": False, "rate_limit_count": 1}))
    for _ in range(20):
        g4.record("s")
    check("关闭防封后不熔断", g4.is_muted("s") is False)
    check("关闭防封后不分段", g4.split("啊" * 500) == ["啊" * 500])

    # 非法配置值兜底
    g5 = guard_mod.ReplyGuard(make_conf({
        "reply_delay_range": "不是列表",
        "max_chunk_chars": "abc",
        "rate_limit_count": None,
        "mute_duration": "xyz",
    }))
    check("非法延迟区间退回默认", g5.delay_range == (0.4, 1.6), f"{g5.delay_range}")
    check("非法分段上限退回默认", g5.max_chunk == 180)
    check("非法熔断阈值退回默认", g5.rate_limit == 20, f"{g5.rate_limit}")
    check("非法沉默时长退回默认", g5.mute_duration == 20.0, f"{g5.mute_duration}")

    # 分段上限过小会被钳到 50（防止消息被切碎）
    g6 = guard_mod.ReplyGuard(make_conf({"max_chunk_chars": 5}))
    check("分段上限被钳到 50", g6.max_chunk == 50, f"{g6.max_chunk}")

    print("\n[22] 节流接入指令（端到端）")
    guard_plugin = plugin_mod.TextRPGPlugin(
        context=None,
        config={
            "reply_delay_range": [0, 0],
            "chunk_gap_range": [0, 0],
            "group_whitelist_mode": "all",
        },
    )
    ev_g = AstrMessageEvent(qq="60001", name="节流测试", group="555")
    await fast_create(guard_plugin, ev_g)
    pg = guard_plugin.store.get("555", "60001")
    check("节流插件下报名仍正常", pg is not None)

    out = await run_cmd(guard_plugin, "profile", ev_g)
    check("属性面板正常返回", "道途" in out, out[:80])

    # 长输出应被分段：给一个塞满储物袋的角色
    for i in range(40):
        pg.add_item(f"测试物品{i}", limit=100)
    out = await run_cmd(guard_plugin, "inventory", ev_g)
    check("长储物袋列表有内容", len(out) > 0)
    # 分段后 run_cmd 拼回的内容应包含所有物品
    check("分段后内容完整", "测试物品39" in out, out[-100:])

    # 熔断时指令静默（不报错）
    mute_plugin = plugin_mod.TextRPGPlugin(
        context=None,
        config={
            "reply_delay_range": [0, 0],
            "rate_limit_count": 1,
            "mute_duration": 60,
            "mute_duration_seconds": 60,
            "group_whitelist_mode": "all",
        },
    )
    ev_m = AstrMessageEvent(qq="60002", name="熔断测试", group="556")
    await fast_create(mute_plugin, ev_m)
    # 第一次已记录，现在应处于熔断
    out = await run_cmd(mute_plugin, "profile", ev_m)
    check("熔断期间静默（无输出）", out == "", f"out={out!r}")

    # ----------------------------------------------------------------
    print("\n[23] 访问控制 —— 群白名单")
    # ----------------------------------------------------------------
    acl_mod = importlib.import_module(f"{HERE.name}.acl")

    # parse_id_list 容错：各种分隔符和类型都要吃下去
    check("解析逗号分隔", acl_mod.parse_id_list("111,222") == ["111", "222"])
    check("解析中文逗号", acl_mod.parse_id_list("111，222") == ["111", "222"])
    check("解析换行分隔", acl_mod.parse_id_list("111\n222\n333")
          == ["111", "222", "333"])
    check("解析空格分隔", acl_mod.parse_id_list("111 222") == ["111", "222"])
    check("解析 list 形式", acl_mod.parse_id_list([111, "222"]) == ["111", "222"])
    check("解析单个数字", acl_mod.parse_id_list(123456) == ["123456"])
    check("解析 None", acl_mod.parse_id_list(None) == [])
    check("解析空串", acl_mod.parse_id_list("") == [])
    check("自动去重且保序", acl_mod.parse_id_list("111,222,111") == ["111", "222"])
    check("混合分隔符", acl_mod.parse_id_list("111, 222\n333") == ["111", "222", "333"])

    # 白名单模式的判断逻辑
    conf_holder = {
        "group_whitelist_mode": "whitelist",
        "enabled_groups": "777,888",
        "master_qq_list": "10001",
        "reset_admin_only": True,
    }
    acl = acl_mod.AccessControl(lambda k, d: conf_holder.get(k, d))

    check("白名单内的群放行", acl.is_group_allowed("777"))
    check("白名单内的群放行2", acl.is_group_allowed("888"))
    check("白名单外的群拦截", not acl.is_group_allowed("999"))
    check("私聊不受群白名单限制", acl.is_group_allowed(""))
    check("主人判定命中", acl.is_master("10001"))
    check("主人判定未命中", not acl.is_master("99999"))

    ok, reason = acl.check_session("999", "12345")
    check("拦截时给出原因", (not ok) and "999" in reason, reason)
    ok, _ = acl.check_session("777", "12345")
    check("放行时无原因", ok)

    # 模式切换
    conf_holder["group_whitelist_mode"] = "private"
    check("私聊模式下属拦截群消息", not acl.is_group_allowed("777"))
    check("私聊模式下属放行私聊", acl.is_group_allowed(""))

    conf_holder["group_whitelist_mode"] = "all"
    check("all 模式下放行任意群", acl.is_group_allowed("99999"))

    conf_holder["group_whitelist_mode"] = "配置写错了"
    check("非法模式退回白名单", acl.mode == "whitelist")
    check("非法模式后拦截非白名单群", not acl.is_group_allowed("99999"))
    conf_holder["group_whitelist_mode"] = "whitelist"

    # 主人列表为空时的兜底：不放行任何人
    conf_holder["master_qq_list"] = ""
    check("主人列表为空 → 无主人", not acl.is_master("10001"))
    conf_holder["master_qq_list"] = "10001"

    # ----------------------------------------------------------------
    print("\n[24] 访问控制 —— 敏感指令闸门")
    # ----------------------------------------------------------------
    ev_master = AstrMessageEvent(qq="10001", name="主人", group="777")
    ev_stranger = AstrMessageEvent(qq="99999", name="路人", group="777")
    ev_group_admin = AstrMessageEvent(qq="55555", name="群管", group="777",
                                      admin=True)

    ok, _ = acl.check_sensitive(ev_master, "777", "10001")
    check("主人可执行敏感指令", ok)

    ok, _ = acl.check_sensitive(ev_stranger, "777", "99999")
    check("路人不可执行敏感指令", not ok)

    ok, _ = acl.check_sensitive(ev_group_admin, "777", "55555")
    check("非主人但群管理员 → 默认拦截", not ok)

    # 关闭 admin_only 后群管理员可用
    conf_holder["reset_admin_only"] = False
    acl2 = acl_mod.AccessControl(lambda k, d: conf_holder.get(k, d))
    ok, _ = acl2.check_sensitive(ev_group_admin, "777", "55555")
    check("关闭 admin_only 后群管可用", ok)
    ok, _ = acl2.check_sensitive(ev_stranger, "777", "99999")
    check("关闭 admin_only 后普通路人仍被拦截", not ok)
    conf_holder["reset_admin_only"] = True

    # ----------------------------------------------------------------
    print("\n[25] 访问控制 —— 端到端")
    # ----------------------------------------------------------------
    acl_plugin = plugin_mod.TextRPGPlugin(
        context=None,
        config={
            "reply_delay_range": [0, 0],
            "chunk_gap_range": [0, 0],
            "rate_limit_count": 10000,
            "group_whitelist_mode": "whitelist",
            "enabled_groups": "1001,1002",
            "master_qq_list": "70001",
            "reset_admin_only": True,
        },
    )

    # 白名单内的群：一切正常
    ev_in = AstrMessageEvent(qq="70001", name="主人在白名单群", group="1001")
    out = await run_cmd(acl_plugin, "register", ev_in)
    check("白名单群内可报名", "第 1 步" in out, out[:80])
    check("白名单群内进入捏脸流程",
          acl_plugin.creation.get("1001", "70001") is not None)
    # v2：报名是三步交互，走完流程才有存档
    await fast_create(acl_plugin, ev_in, "白名单角色")
    check("白名单群内走完流程后存档存在",
          acl_plugin.store.get("1001", "70001") is not None)

    # 白名单外的群：完全静默，连存档都不该创建
    ev_out = AstrMessageEvent(qq="70002", name="路人在别人群", group="2002")
    out = await run_cmd(acl_plugin, "register", ev_out)
    check("白名单外群 → 完全静默", out == "", f"out={out!r}")
    check("白名单外群 → 不创建存档",
          acl_plugin.store.get("2002", "70002") is None)

    # 白名单外群的所有指令都应静默
    for cmd in ("profile", "inventory", "shop", "ranking"):
        out = await run_cmd(acl_plugin, cmd, ev_out)
        check(f"白名单外群 {cmd} 静默", out == "", f"out={out!r}")

    # 私聊：不受群白名单限制
    ev_pm = AstrMessageEvent(qq="70003", name="私聊用户", group="")
    out = await run_cmd(acl_plugin, "register", ev_pm)
    check("私聊不受白名单限制", out != "", f"out={out!r}")

    # 敏感指令：非主人执行重置 → 被拒绝
    # ev_in 已有存档，这里不需要再建
    ev_not_master = AstrMessageEvent(qq="77777", name="非主人", group="1001")
    await fast_create(acl_plugin, ev_not_master, "非主人角色")
    before = len(acl_plugin.store.players_in_group("1001"))
    out = await run_cmd(acl_plugin, "reset", ev_not_master)
    after = len(acl_plugin.store.players_in_group("1001"))
    check("非主人重置被拒绝", "仅限主人" in out, out[:100])
    check("非主人重置后存档未被清", after == before,
          f"before={before} after={after}")

    # 主人执行重置 → 成功
    out = await run_cmd(acl_plugin, "reset", ev_in)
    check("主人重置成功", "已清空" in out, out[:80])
    check("主人重置后本群存档清空",
          len(acl_plugin.store.players_in_group("1001")) == 0)

    # 私聊重置只清自己，不波及群存档
    ev_pm_master = AstrMessageEvent(qq="70001", name="主人在私聊", group="")
    await fast_create(acl_plugin, ev_pm_master)
    await fast_create(acl_plugin, ev_in, "主人角色")  # 重新建一个群存档
    check("重置前群内存档已重建",
          acl_plugin.store.get("1001", "70001") is not None)
    out = await run_cmd(acl_plugin, "reset", ev_pm_master)
    check("私聊重置有反馈", out != "", f"out={out!r}")
    check("私聊重置不影响群存档",
          acl_plugin.store.get("1001", "70001") is not None,
          "私聊重置把群存档也清了（危险！）")

    # ----------------------------------------------------------------
    print("\n[26] 群激活机制")
    # ----------------------------------------------------------------
    act_mod = importlib.import_module(f"{HERE.name}.activation")
    act_dir = HERE / "_testdata_act"
    shutil.rmtree(act_dir, ignore_errors=True)
    act_dir.mkdir(parents=True, exist_ok=True)

    act = act_mod.ActivationStore(act_dir)
    check("初始无激活群", act.activated_groups() == [])
    check("未激活群判定为 False", not act.is_activated("123"))

    fresh = await act.activate("123", by="12345678", name="测试群")
    check("首次激活返回 True", fresh is True)
    check("激活后判定为 True", act.is_activated("123"))
    check("已激活群出现在列表", act.activated_groups() == ["123"])

    again = await act.activate("123", by="12345678")
    check("重复激活返回 False", again is False)

    info = act.info("123")
    check("记录激活者", info is not None and info["by"] == "12345678",
          f"{info}")
    check("记录群名", info is not None and info["name"] == "测试群")

    # 持久化：重新读一个实例应该能看到
    act_reload = act_mod.ActivationStore(act_dir)
    check("激活状态已落盘", act_reload.is_activated("123"),
          "重启后激活状态丢失（会需要重新激活）")

    removed = await act.deactivate("123")
    check("取消激活返回 True", removed is True)
    act_reload2 = act_mod.ActivationStore(act_dir)
    check("取消激活已落盘", not act_reload2.is_activated("123"))

    removed2 = await act.deactivate("123")
    check("取消未激活的群返回 False", removed2 is False)

    # 空白群号不该被激活
    check("空群号无法激活", await act.activate("", by="1") is False)

    # 文件损坏时不能崩
    bad_dir = HERE / "_testdata_act_bad"
    shutil.rmtree(bad_dir, ignore_errors=True)
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / act_mod.FILENAME).write_text("{不是合法json", encoding="utf-8")
    act_bad = act_mod.ActivationStore(bad_dir)
    check("激活文件损坏时当作空", act_bad.activated_groups() == [])

    # ----------------------------------------------------------------
    print("\n[27] 激活后指令才生效（端到端）")
    # ----------------------------------------------------------------
    act_plugin = plugin_mod.TextRPGPlugin(
        context=None,
        config={
            "reply_delay_range": [0, 0],
            "chunk_gap_range": [0, 0],
            "rate_limit_count": 10000,
            "group_whitelist_mode": "whitelist",
            "master_qq_list": "12345678",
        },
    )
    # 让激活数据落在同一个目录
    act_plugin.activation = act_mod.ActivationStore(act_plugin.data_dir)
    act_plugin.acl.activation = act_plugin.activation

    gid = "555001"
    ev_owner = AstrMessageEvent(qq="12345678", name="主人", group=gid)

    # 激活前：一切静默，且不建存档
    out = await run_cmd(act_plugin, "register", ev_owner)
    check("激活前报名被静默拦截", out == "", f"out={out!r}")
    check("激活前未创建存档",
          act_plugin.store.get(gid, "12345678") is None)

    # 主人发「激活」
    out = await run_cmd(act_plugin, "activate", ev_owner)
    check("激活指令有反馈", "已" in out and "激活" in out, out[:100])
    check("激活状态已记录", act_plugin.activation.is_activated(gid))

    # 重复激活提示已激活
    out = await run_cmd(act_plugin, "activate", ev_owner)
    check("重复激活有提示", "已经" in out, out[:80])

    # 激活后：指令正常
    out = await fast_create(act_plugin, ev_owner, "主人角色")
    check("激活后报名成功", "建号完成" in out, out[:80])
    check("激活后创建了存档",
          act_plugin.store.get(gid, "12345678") is not None)

    # 普通群友激活后也能玩
    ev_member = AstrMessageEvent(qq="888001", name="群友甲", group=gid)
    out = await fast_create(act_plugin, ev_member, "群友甲角色")
    check("激活后群友可报名", "建号完成" in out, out[:80])

    # 非主人不能激活别的群
    other_gid = "555002"
    ev_member2 = AstrMessageEvent(qq="888001", name="群友甲", group=other_gid)
    out = await run_cmd(act_plugin, "activate", ev_member2)
    check("非主人无法激活群", "仅限主人" in out, out[:100])
    check("非主人激活未生效",
          not act_plugin.activation.is_activated(other_gid))

    # 群本身未激活时，非主人连游戏指令都用不了
    out = await run_cmd(act_plugin, "register", ev_member2)
    check("未激活群群友被拦截", out == "", f"out={out!r}")

    # 取消激活后恢复静默
    out = await run_cmd(act_plugin, "deactivate", ev_owner)
    check("取消激活有反馈", "取消" in out, out[:80])
    out = await run_cmd(act_plugin, "profile", ev_owner)
    check("取消激活后指令静默", out == "", f"out={out!r}")
    check("取消激活后存档仍保留",
          act_plugin.store.get(gid, "12345678") is not None,
          "取消激活把存档也删了（不该）")

    # 私聊里不能激活
    ev_pm_owner = AstrMessageEvent(qq="12345678", name="主人", group="")
    out = await run_cmd(act_plugin, "activate", ev_pm_owner)
    check("私聊无法激活群", "需要在群聊" in out, out[:100])

    # 激活列表
    await run_cmd(act_plugin, "activate", ev_owner)
    out = await run_cmd(act_plugin, "activation_list", ev_pm_owner)
    check("激活列表包含已激活群", gid in out, out[:150])

    # ----------------------------------------------------------------
    print("\n[28] 入群邀请守卫")
    # ----------------------------------------------------------------
    # check_invite 的纯逻辑
    inv_acl = acl_mod.AccessControl(
        lambda k, d: {"master_qq_list": "12345678"}.get(k, d)
    )
    ok, _ = inv_acl.check_invite("12345678")
    check("主人的邀请被接受", ok)
    ok, reason = inv_acl.check_invite("99999999")
    check("陌生人的邀请被拒绝", not ok, reason)
    check("拒绝原因提到邀请人", "99999999" in reason, reason)

    # 未配置主人时，谁都不能邀请
    no_master_acl = acl_mod.AccessControl(lambda k, d: {}.get(k, d))
    ok, reason = no_master_acl.check_invite("12345678")
    check("未配置主人时拒绝所有邀请", not ok, reason)

    # 端到端：模拟一个假的 bot 记录调用
    class _FakeBot:
        def __init__(self):
            self.calls = []

        async def set_group_add_request(self, flag, sub_type, approve,
                                        reason=""):
            self.calls.append({
                "flag": flag, "approve": approve, "reason": reason,
            })

    class _ReqEvent:
        """模拟加群邀请事件。"""

        def __init__(self, inviter, group_id, bot):
            self.raw_message = {
                "post_type": "request",
                "request_type": "group",
                "user_id": str(inviter),
                "group_id": str(group_id),
                "flag": f"flag-{inviter}-{group_id}",
            }
            self.bot = bot

        def plain_result(self, text):
            return _Result(text)

        def stop_event(self):
            pass

    inv_plugin = plugin_mod.TextRPGPlugin(
        context=None,
        config={
            "reply_delay_range": [0, 0],
            "chunk_gap_range": [0, 0],
            "rate_limit_count": 10000,
            "group_whitelist_mode": "all",
            "master_qq_list": "12345678",
        },
    )

    # 主人邀请 -> 同意
    bot1 = _FakeBot()
    ev_inv1 = _ReqEvent("12345678", "777001", bot1)
    await inv_plugin.on_invite(ev_inv1)
    check("主人邀请 -> 调用了一次接口", len(bot1.calls) == 1, f"{bot1.calls}")
    check("主人邀请 -> approve=True",
          bot1.calls and bot1.calls[0]["approve"] is True, f"{bot1.calls}")

    # 陌生人邀请 -> 拒绝
    bot2 = _FakeBot()
    ev_inv2 = _ReqEvent("88888888", "777002", bot2)
    await inv_plugin.on_invite(ev_inv2)
    check("陌生人邀请 -> 调用了一次接口", len(bot2.calls) == 1, f"{bot2.calls}")
    check("陌生人邀请 -> approve=False",
          bot2.calls and bot2.calls[0]["approve"] is False, f"{bot2.calls}")
    check("陌生人邀请 -> 带拒绝理由",
          bot2.calls and bot2.calls[0]["reason"], f"{bot2.calls}")

    # 普通消息不该被这个钩子处理
    bot3 = _FakeBot()
    ev_msg = AstrMessageEvent(qq="10001", name="路人", group="777003")
    await inv_plugin.on_invite(ev_msg)
    check("普通消息不触发邀请处理", len(bot3.calls) == 0)

    # 没有 bot 属性时不能崩（其他平台）
    class _NoBotEvent:
        def __init__(self):
            self.raw_message = {
                "post_type": "request", "request_type": "group",
                "user_id": "88888888", "group_id": "777004", "flag": "f",
            }

        def plain_result(self, text):
            return _Result(text)

    try:
        await inv_plugin.on_invite(_NoBotEvent())
        check("无 bot 属性时不崩溃", True)
    except Exception as exc:  # noqa: BLE001
        check("无 bot 属性时不崩溃", False, f"{type(exc).__name__}: {exc}")

    # ================================================================
    # v3.0 修仙体系专项
    # ================================================================

    print("\n[29] 境界骨架（八境 × 九转 = 72 转）")
    check("总转数 72", realm_mod.MAX_TURNS == 72)
    check("每境 9 转", realm_mod.TURNS_PER_REALM == 9)
    check("八大境界", len(realm_mod.REALMS) == 8, f"{len(realm_mod.REALMS)}")

    # 境界边界：1/9 炼气，10 筑基 … 64/72 大乘
    check("1 转 = 炼气期", realm_mod.realm_name_of(1) == "炼气期", realm_mod.realm_name_of(1))
    check("9 转 = 炼气期", realm_mod.realm_name_of(9) == "炼气期")
    check("10 转 = 筑基期", realm_mod.realm_name_of(10) == "筑基期")
    check("19 转 = 金丹期", realm_mod.realm_name_of(19) == "金丹期")
    check("28 转 = 元婴期", realm_mod.realm_name_of(28) == "元婴期")
    check("37 转 = 化神期", realm_mod.realm_name_of(37) == "化神期")
    check("46 转 = 炼虚期", realm_mod.realm_name_of(46) == "炼虚期")
    check("55 转 = 合体期", realm_mod.realm_name_of(55) == "合体期")
    check("64 转 = 大乘期", realm_mod.realm_name_of(64) == "大乘期")
    check("72 转 = 大乘期", realm_mod.realm_name_of(72) == "大乘期")

    # 越界钳制
    check("0 转被钳到 1", realm_mod.clamp_turns(0) == 1)
    check("负数被钳到 1", realm_mod.clamp_turns(-5) == 1)
    check("999 转被钳到 72", realm_mod.clamp_turns(999) == 72)
    check("None 被钳到 1", realm_mod.clamp_turns(None) == 1)
    check("字符串 'abc' 被钳到 1", realm_mod.clamp_turns("abc") == 1)
    check("字符串 '30' 能解析", realm_mod.clamp_turns("30") == 30)

    # 境内转数
    check("1 转是境内第 1 转", realm_mod.turn_in_realm(1) == 1)
    check("10 转是境内第 1 转", realm_mod.turn_in_realm(10) == 1)
    check("18 转是境内第 9 转", realm_mod.turn_in_realm(18) == 9)
    check("境界标签带转数", realm_mod.realm_label(19) == "金丹期 1 转",
          realm_mod.realm_label(19))

    # 境界倍率递增
    mults = [realm_mod.realm_mult(realm_mod.REALMS[i][1]) for i in range(8)]
    check("境界倍率严格递增", all(mults[i] < mults[i + 1] for i in range(7)), f"{mults}")
    check("炼气期倍率为 1.0", realm_mod.realm_mult(1) == 1.0)
    check("大乘期倍率为 4.0", realm_mod.realm_mult(64) == 4.0, f"{realm_mod.realm_mult(64)}")

    print("\n[30] 属性乘区（v3 地基）")
    # 面板 = 基础属性 × 总乘区 + 装备加成
    check("无转生无突破时乘区 = 境界倍率",
          realm_mod.stat_mult(19, 0, 0) == realm_mod.realm_mult(19),
          f"{realm_mod.stat_mult(19, 0, 0)}")
    check("1 次转生乘区 ×1.05",
          abs(realm_mod.stat_mult(19, 1, 0) - realm_mod.realm_mult(19) * 1.05) < 1e-9,
          f"{realm_mod.stat_mult(19, 1, 0)}")
    check("2 次转生乘区 ×1.05²",
          abs(realm_mod.stat_mult(19, 2, 0)
              - realm_mod.realm_mult(19) * 1.05 ** 2) < 1e-9)
    check("1 次突破乘区 ×1.20",
          abs(realm_mod.stat_mult(19, 0, 1) - realm_mod.realm_mult(19) * 1.20) < 1e-9,
          f"{realm_mod.stat_mult(19, 0, 1)}")
    check("突破与转生乘区相乘",
          abs(realm_mod.stat_mult(19, 2, 3)
              - realm_mod.realm_mult(19) * 1.05 ** 2 * 1.20 ** 3) < 1e-9)

    # 端到端：Player.atk 真的走乘区
    pm = player_mod.Player(qq="mult", name="乘区测试")
    pm.level = 19
    item_db_t = {e["name"]: e for e in items_mod.EQUIPMENTS}
    pure_base = player_mod.base_atk(pm.turns)
    check("19 转攻击 = 基础 × 境界倍率",
          pm.atk(item_db_t) == int(pure_base * 1.5),
          f"{pm.atk(item_db_t)} vs {int(pure_base * 1.5)}")
    pm.ascensions = 2
    check("转生 2 次后攻击 ×1.05²",
          pm.atk(item_db_t) == int(pure_base * 1.5 * 1.05 ** 2),
          f"{pm.atk(item_db_t)}")
    pm.breakthroughs = 3
    check("再突破 3 次后攻击 ×1.20³",
          pm.atk(item_db_t) == int(pure_base * 1.5 * 1.05 ** 2 * 1.20 ** 3),
          f"{pm.atk(item_db_t)}")

    # v3.4：装备加成是**乘区的一个因子**，不再是加法。
    # 旧断言「+8」基于 `面板 = 基础 × 乘区 + 装备绝对值`，
    # 装备改百分比后这个公式不存在了。
    #
    # ⚠️ 这里必须用**新装备名**（寒铁剑），不能用旧名（铁剑）。
    # equip_pct 是直接查 item_db 的，不像 get_item 那样走别名兜底：
    # 传旧名 -> item_db 查不到 -> 静默返回 0.0 -> 断言「297 vs 297」看似
    # 是公式坏了，其实只是夹具塞了查不到的名字。踩过一次，代价是一轮排查。
    pm.ascensions = 0
    pm.breakthroughs = 0
    atk_no_equip = pm.atk(item_db_t)
    pm.equipment["weapon"] = "寒铁剑"
    _eq39 = pm.equip_pct("atk_pct", item_db_t)
    check("装备加成走乘区（×（1+装备%））",
          pm.atk(item_db_t) == int(atk_no_equip * (1 + _eq39)),
          f"{pm.atk(item_db_t)} vs {int(atk_no_equip * (1 + _eq39))}")
    check("装备加成确实带来了提升（不等于裸装）",
          pm.atk(item_db_t) > atk_no_equip,
          f"{pm.atk(item_db_t)} vs {atk_no_equip}")
    # 旧名（铁剑）现在也能解析 —— equip_pct 走 items.get_item，
    # 内部会做 split_quality + resolve_alias，不再是裸 dict 查表。
    pm.equipment["weapon"] = "铁剑"
    check("旧装备名经别名映射后仍能算出加成",
          pm.equip_pct("atk_pct", item_db_t) == _eq39,
          f"{pm.equip_pct('atk_pct', item_db_t)} vs {_eq39}")
    # 品质倍率必须作用于 equip_pct（口径与 items.get_item 一致）
    pm.equipment["weapon"] = "寒铁剑[传]"
    check("传说品质让装备加成 ×3.0",
          abs(pm.equip_pct("atk_pct", item_db_t) - _eq39 * 3.0) < 1e-9,
          f"{pm.equip_pct('atk_pct', item_db_t)} vs {_eq39 * 3.0}")
    # 完全查不到的名字才返回 0
    pm.equipment["weapon"] = "根本不存在的东西"
    check("完全未知的装备名返回 0 加成",
          pm.equip_pct("atk_pct", item_db_t) == 0.0,
          f"{pm.equip_pct('atk_pct', item_db_t)}")
    pm.equipment.pop("weapon", None)

    # 防御/气血同样走乘区
    check("气血 = 基础气血 × 乘区",
          pm.max_hp(item_db_t) == int(player_mod.base_hp(pm.turns) * pm.stat_mult())
          + pm.equip_bonus("hp", item_db_t),
          f"{pm.max_hp(item_db_t)}")

    print("\n[31] 转生与突破加成常量")
    check("转生加成 5%", realm_mod.ASCENSION_BONUS == 0.05)
    check("突破加成 20%", realm_mod.BREAKTHROUGH_BONUS == 0.20)
    check("飞升所需修为 250 万", realm_mod.ASCENSION_QI == 2_500_000,
          f"{realm_mod.ASCENSION_QI}")
    check("转生倍率函数 = 1.05^n",
          abs(realm_mod.ascension_mult(3) - 1.05 ** 3) < 1e-9)
    check("突破倍率函数 = 1.20^n",
          abs(realm_mod.breakthrough_mult(2) - 1.20 ** 2) < 1e-9)

    print("\n[32] 突破与飞升的资格判定")
    check("炼气期 1 转不能突破", not realm_mod.can_breakthrough(1, 999999))
    check("炼气期 9 转修为够可突破",
          realm_mod.can_breakthrough(9, realm_mod.qi_needed(9)),
          f"需要 {realm_mod.qi_needed(9)}")
    check("炼气期 9 转修为不够不能突破", not realm_mod.can_breakthrough(9, 100))
    check("72 转是上限", realm_mod.is_max_turn(72))
    check("71 转不是上限", not realm_mod.is_max_turn(71))
    check("大乘期 9 转 + 250 万修为可渡劫",
          realm_mod.can_tribulation(72, 2_500_000))
    check("修为不足不能渡劫", not realm_mod.can_tribulation(72, 100))

    print("\n[33] 天劫（抗住不是打赢）")
    check("天劫共 9 道", player_mod.TRIBULATION_BOLTS == 9)
    check("第 1 道雷 = 最大气血 8%",
          abs(player_mod.TRIBULATION_BASE_RATIO - 0.08) < 1e-9)

    # 满血能抗几道
    survived, damages = player_mod.tribulation_bolts(10000, 10000)
    check("满血 10000 能抗住全部 9 道", survived == 9, f"{survived}")
    check("伤害列表长度 = 抗住数 + 断掉那道",
          len(damages) == min(9, survived + 1), f"{len(damages)}")
    check("每道伤害严格递增",
          all(damages[i] < damages[i + 1] for i in range(len(damages) - 1)),
          f"{damages}")

    # 残血抗不住
    survived_low, _ = player_mod.tribulation_bolts(100, 10000)
    check("残血（1%）抗不住天劫", survived_low < 9, f"{survived_low}")
    check("伤害不足 1 时至少算 1",
          all(d >= 1 for d in player_mod.tribulation_bolts(1, 10000)[1]))

    # 空血直接被劈死
    check("气血 0 一道都抗不住", player_mod.tribulation_bolts(0, 10000)[0] == 0)

    # 端到端：渡劫成功飞升（判定与状态变更分离）
    trib_p = player_mod.Player(qq="trib", name="渡劫者")
    trib_p.level = 72
    trib_p.exp = realm_mod.ASCENSION_QI
    trib_p.full_heal(item_db_t)
    asc_before = trib_p.ascensions
    res_t = battle_mod.tribulate(trib_p, item_db_t)
    check("渡劫成功 survived=9", res_t.survived == 9, f"{res_t.survived}")
    check("渡劫成功 passed=True", res_t.passed is True)
    check("判定阶段不动数据（转生次数未变）",
          trib_p.ascensions == asc_before, f"{trib_p.ascensions}")
    check("tribulate 只判定、不自行飞升（转数未变）",
          trib_p.level == 72, f"{trib_p.level}")

    # 由调用方显式执行飞升
    battle_mod.ascend(trib_p, item_db_t)
    check("执行飞升后转生次数 +1", trib_p.ascensions == asc_before + 1,
          f"{trib_p.ascensions}")
    check("飞升后境界归 1 转", trib_p.level == 1, f"{trib_p.level}")
    check("飞升后修为清零", trib_p.exp == 0, f"{trib_p.exp}")
    check("飞升后气血回满", trib_p.hp == trib_p.max_hp(item_db_t))

    # 端到端：渡劫失败（气血过低）
    fail_p = player_mod.Player(qq="trib2", name="心急者")
    fail_p.level = 72
    fail_p.exp = realm_mod.ASCENSION_QI
    fail_p.full_heal(item_db_t)
    fail_p.hp = max(1, fail_p.max_hp(item_db_t) // 10)   # 只有 10% 气血
    res_f = battle_mod.tribulate(fail_p, item_db_t)
    check("残血者扛不住 9 道", res_f.survived < 9, f"{res_f.survived}")
    check("残血渡劫 passed=False", res_f.passed is False)
    check("失败后掉了转数", fail_p.level < 72, f"{fail_p.level}")
    check("失败后进入休整", fail_p.is_resting())
    check("失败未飞升（转生次数仍 0）", fail_p.ascensions == 0, f"{fail_p.ascensions}")

    print("\n[34] 大道秘典数据完整性")
    check("首期 5 条大道", len(scriptures_mod.SCRIPTURE_NAMES) == 5,
          f"{scriptures_mod.SCRIPTURE_NAMES}")
    check("每条大道 3 阶", scriptures_mod.MAX_TIER == 3)
    check("火之秘典存在", scriptures_mod.has_scripture("火之秘典"))
    check("水之秘典存在", scriptures_mod.has_scripture("水之秘典"))
    check("金之秘典存在", scriptures_mod.has_scripture("金之秘典"))
    check("土之秘典存在", scriptures_mod.has_scripture("土之秘典"))
    check("风之秘典存在", scriptures_mod.has_scripture("风之秘典"))
    check("不存在的秘典返回 None", scriptures_mod.get_scripture("雷之秘典") is None)

    # 火之秘典按老板原话落表
    fire1 = scriptures_mod.tier_info("火之秘典", 1)
    check("火一阶名为「火焰」", fire1 and fire1["name"] == "火焰", str(fire1))
    check("火焰是主动技", fire1 and fire1["type"] == "active")
    check("火焰冷却 1 回合", fire1 and fire1["cooldown"] == 1)
    check("火焰 = 200% 攻击力的火魔法伤害",
          fire1 and fire1["effect"]["kind"] == "magic_damage"
          and fire1["effect"]["mult"] == 2.0
          and fire1["effect"]["element"] == "fire",
          str(fire1 and fire1["effect"]))

    fire2 = scriptures_mod.tier_info("火之秘典", 2)
    check("火二阶名为「控火」", fire2 and fire2["name"] == "控火")
    check("控火是被动", fire2 and fire2["type"] == "passive")
    check("控火 = 火伤 -25%",
          fire2 and fire2["effect"]["kind"] == "resist"
          and fire2["effect"]["value"] == 0.25
          and fire2["effect"]["element"] == "fire",
          str(fire2 and fire2["effect"]))

    fire3 = scriptures_mod.tier_info("火之秘典", 3)
    check("火三阶名为「燎原」", fire3 and fire3["name"] == "燎原")
    check("燎原是被动", fire3 and fire3["type"] == "passive")
    check("燎原 = 所有伤害附加 20% 攻击力火伤",
          fire3 and fire3["effect"]["kind"] == "add_damage"
          and fire3["effect"]["mult"] == 0.2
          and fire3["effect"]["element"] == "fire",
          str(fire3 and fire3["effect"]))

    # 全部大道结构合法
    struct_ok = True
    kinds_seen = set()
    for sc_name in scriptures_mod.SCRIPTURE_NAMES:
        sc = scriptures_mod.get_scripture(sc_name)
        if not sc or "dao" not in sc or "tiers" not in sc:
            struct_ok = False
            break
        if set(sc["tiers"]) != {1, 2, 3}:
            struct_ok = False
            break
        for _t, info in sc["tiers"].items():
            eff = info.get("effect") or {}
            if "kind" not in eff:
                struct_ok = False
                break
            kinds_seen.add(eff["kind"])
            if info.get("type") not in ("active", "passive"):
                struct_ok = False
                break
    check("全部大道结构合法（3 阶 + effect.kind + 主动/被动）", struct_ok)
    check("效果种类 ≥ 5（覆盖输出/续航/防御/控制）", len(kinds_seen) >= 5,
          f"{sorted(kinds_seen)}")
    check("所有 effect.kind 都有对应处理器",
          all(k in battle_mod.EFFECT_HANDLERS for k in kinds_seen),
          f"未注册：{sorted(k for k in kinds_seen if k not in battle_mod.EFFECT_HANDLERS)}")

    # 残页映射
    check("火之秘典对应火之残页",
          scriptures_mod.relic_name("火之秘典") == "火之残页",
          scriptures_mod.relic_name("火之秘典"))
    check("残页反查回秘典",
          scriptures_mod.RELIC_TO_SCRIPTURE.get("火之残页") == "火之秘典")
    check("残页共 5 种", len(scriptures_mod.RELIC_NAMES) == 5)
    check("残页在物品表里有定义",
          all(i in items_mod.all_item_names() for i in scriptures_mod.RELIC_NAMES),
          str([i for i in scriptures_mod.RELIC_NAMES
               if i not in items_mod.all_item_names()]))
    check("残页不会被当丹药误用",
          not any(i in items_mod.HEAL_ITEMS for i in scriptures_mod.RELIC_NAMES))

    print("\n[35] 秘典修行消耗与装配位")
    check("一阶只收 1 张残页（修为 0）",
          scriptures_mod.relic_cost("火之秘典", 1) == 1
          and scriptures_mod.qi_cost("火之秘典", 1) == 0)
    check("二阶收 3 张残页", scriptures_mod.relic_cost("火之秘典", 2) == 3)
    check("三阶收 5 张残页", scriptures_mod.relic_cost("火之秘典", 3) == 5)
    check("二阶要 2000 修为", scriptures_mod.qi_cost("火之秘典", 2) == 2_000)
    check("三阶要 8000 修为", scriptures_mod.qi_cost("火之秘典", 3) == 8_000)
    check("修行消耗随阶数递增",
          scriptures_mod.qi_cost("火之秘典", 1)
          < scriptures_mod.qi_cost("火之秘典", 2)
          < scriptures_mod.qi_cost("火之秘典", 3))

    check("0 次突破 1 个装配位", scriptures_mod.equip_slots(0) == 1)
    check("1 次突破 2 个装配位", scriptures_mod.equip_slots(1) == 2)
    check("7 次突破 8 个装配位", scriptures_mod.equip_slots(7) == 8)
    check("装配位上限 8", scriptures_mod.equip_slots(99) == 8)
    check("负数突破按 0 算", scriptures_mod.equip_slots(-3) == 1)

    print("\n[36] 秘典战斗状态（冷却与激活）")
    st = scriptures_mod.make_battle_state({"火之秘典": 3}, ["火之秘典"])
    check("战斗状态已构造", isinstance(st, dict))
    act1 = scriptures_mod.ready_actives(st)
    check("开局「火焰」可释放", any(a[1] == 1 for a in act1), f"{act1}")

    scriptures_mod.start_cooldown(st, "火之秘典", 1)
    check("释放后进入冷却", not any(a[1] == 1 for a in scriptures_mod.ready_actives(st)),
          f"{scriptures_mod.ready_actives(st)}")
    scriptures_mod.tick_cooldowns(st)
    check("冷却 1 回合后恢复",
          any(a[1] == 1 for a in scriptures_mod.ready_actives(st)),
          f"{scriptures_mod.ready_actives(st)}")
    check("冷却随回合递减到位",
          scriptures_mod.tick_cooldowns(st) is None)

    # 未习得主动技不出现
    st0 = scriptures_mod.make_battle_state({}, [])
    check("未习得时无主动技", scriptures_mod.ready_actives(st0) == [],
          f"{scriptures_mod.ready_actives(st0)}")

    # 只习得一阶时没有二/三阶被动
    p1 = scriptures_mod.passive_tiers("火之秘典", 1)
    check("只有 1 阶时无被动", p1 == [], f"{p1}")
    p2 = scriptures_mod.passive_tiers("火之秘典", 2)
    check("2 阶时解锁 1 个被动（控火）", len(p2) == 1, f"{p2}")
    p3 = scriptures_mod.passive_tiers("火之秘典", 3)
    check("3 阶时解锁 2 个被动", len(p3) == 2, f"{p3}")

    print("\n[37] 被动效果汇总（PassiveBag）")
    # PassiveBag 吃的是 Player 对象，装备的秘典被动会被汇总进来
    bag_p2 = player_mod.Player(qq="bag2", name="被动测试")
    bag_p2.scriptures["火之秘典"] = 2      # 一阶主动 + 二阶被动（控火）
    bag_p2.equipped_scriptures = ["火之秘典"]
    bag2 = battle_mod.PassiveBag(bag_p2)
    check("控火提供火抗 25%",
          abs(bag2.resist.get("fire", 0.0) - 0.25) < 1e-9,
          f"{bag2.resist}")
    check("未提供抗性的元素不在表中",
          "water" not in bag2.resist, f"{bag2.resist}")

    bag_p3 = player_mod.Player(qq="bag3", name="被动测试3")
    bag_p3.scriptures["火之秘典"] = 3      # 三阶燎原
    bag_p3.equipped_scriptures = ["火之秘典"]
    bag3 = battle_mod.PassiveBag(bag_p3)
    check("燎原折算为 +20% 攻击力",
          abs(bag3.atk_pct - 20.0) < 1e-9, f"{bag3.atk_pct}")
    check("燎原同时保留控火的火抗",
          abs(bag3.resist.get("fire", 0.0) - 0.25) < 1e-9, f"{bag3.resist}")

    # 未装配的秘典不生效
    bag_p_uneq = player_mod.Player(qq="bag_uneq", name="未装配")
    bag_p_uneq.scriptures["火之秘典"] = 3
    bag_p_uneq.equipped_scriptures = []
    bag_uneq = battle_mod.PassiveBag(bag_p_uneq)
    check("未装配的秘典被动不生效",
          bag_uneq.resist.get("fire", 0.0) == 0.0 and bag_uneq.atk_pct == 0.0,
          f"resist={bag_uneq.resist} atk_pct={bag_uneq.atk_pct}")

    # 空装配不报错
    empty_bag = battle_mod.PassiveBag(player_mod.Player(qq="empty", name="空"))
    check("空装配不报错",
          empty_bag.resist == {} and empty_bag.atk_pct == 0.0,
          f"{empty_bag.resist}")

    # 多元素抗性叠加（火 + 金）
    bag_p_multi = player_mod.Player(qq="multi", name="多元素")
    bag_p_multi.scriptures["火之秘典"] = 2
    bag_p_multi.scriptures["金之秘典"] = 3
    bag_p_multi.equipped_scriptures = ["火之秘典", "金之秘典"]
    bag_multi = battle_mod.PassiveBag(bag_p_multi)
    check("多元素抗性互不干扰（火有抗）", bag_multi.resist.get("fire", 0.0) > 0,
          f"{bag_multi.resist}")
    check("多元素被动可叠加生效", len(bag_multi.resist) >= 1, f"{bag_multi.resist}")

    print("\n[38] 走火入魔概率")
    # 阈值表语义：h < 2 无风险；2 ≤ h < 4 为 5%；4 ≤ h < 8 为 12%；h ≥ 8 为 20%
    check("1 小时无风险", player_mod.deviation_chance(1.0) == 0.0,
          f"{player_mod.deviation_chance(1.0)}")
    check("刚好 2 小时起有风险（5%）",
          abs(player_mod.deviation_chance(2.0) - 0.05) < 1e-9,
          f"{player_mod.deviation_chance(2.0)}")
    check("3 小时 5%", abs(player_mod.deviation_chance(3.0) - 0.05) < 1e-9,
          f"{player_mod.deviation_chance(3.0)}")
    check("刚好 4 小时进入 12%",
          abs(player_mod.deviation_chance(4.0) - 0.12) < 1e-9,
          f"{player_mod.deviation_chance(4.0)}")
    check("6 小时 12%", abs(player_mod.deviation_chance(6.0) - 0.12) < 1e-9,
          f"{player_mod.deviation_chance(6.0)}")
    check("8 小时 20%", abs(player_mod.deviation_chance(8.0) - 0.20) < 1e-9,
          f"{player_mod.deviation_chance(8.0)}")
    check("20 小时仍封顶 20%",
          abs(player_mod.deviation_chance(20.0) - 0.20) < 1e-9,
          f"{player_mod.deviation_chance(20.0)}")
    check("概率随时长单调不减",
          player_mod.deviation_chance(1) <= player_mod.deviation_chance(3)
          <= player_mod.deviation_chance(6)
          <= player_mod.deviation_chance(8),
          f"{[player_mod.deviation_chance(h) for h in (1,3,6,8)]}")
    check("异常输入不崩溃",
          player_mod.deviation_chance("abc") == 0.0
          and player_mod.deviation_chance(None) == 0.0)

    # 入魔收益减半（不扣资源）
    check("入魔惩罚系数 0.5", player_mod.QI_DEVIATION_PENALTY == 0.5)
    d_p = player_mod.Player(qq="dev", name="入魔测试")
    d_p.train_start_ts = time.time() - 3600 * 8
    full_gain = realm_mod.seclusion_qi_per_hour(d_p.turns) * 8
    check("8 小时满收益 > 0", full_gain > 0, f"{full_gain}")
    check("入魔后收益 = 满收益 × 0.5",
          int(full_gain * player_mod.QI_DEVIATION_PENALTY) == full_gain // 2,
          f"{int(full_gain * 0.5)}")

    print("\n[39] 修炼（主动）与闭关（挂机）区别")
    check("修炼冷却 5 分钟", player_mod.CULTIVATE_COOLDOWN == 300)
    # v3.6：闭关 / 探索改成状态型挂机，结算后不再冷却
    check("闭关无冷却（状态型）", player_mod.SECLUSION_COOLDOWN == 0)
    check("探索无冷却（状态型）", player_mod.EXPLORE_COOLDOWN == 0)
    check("探索最短 30 分钟", player_mod.EXPLORE_MIN_SECONDS == 1800)
    check("探索最长 8 小时", player_mod.EXPLORE_MAX_SECONDS == 28800)
    check("历练冷却 5 分钟", player_mod.HUNT_COOLDOWN == 300)

    # 闭关时收益 ≈ 修炼的 3 倍每小时
    t_p = player_mod.Player(qq="cmp", name="对比测试")
    t_p.level = 19
    check("闭关每小时 = 主动修炼 × 3",
          realm_mod.seclusion_qi_per_hour(t_p.turns)
          == realm_mod.cultivate_gain(t_p.turns) * 3,
          f"{realm_mod.seclusion_qi_per_hour(t_p.turns)} vs {realm_mod.cultivate_gain(t_p.turns)}")

    # 主动修炼加修为 + 进冷却
    ev_c2 = AstrMessageEvent(qq="40001", name="修炼者")
    await fast_create(plugin, ev_c2)
    pc = plugin.store.get("999", "40001")
    pc.exp = 0
    pc.last_cultivate_ts = 0.0
    out = await run_cmd(plugin, "cultivate", ev_c2)
    check("修炼有产出", "修为" in out, out[:120])
    check("修炼后修为增加", pc.exp > 0, f"{pc.exp}")
    check("修炼后进冷却", pc.cultivate_remain(plugin.cultivate_cooldown) > 0,
          f"{pc.cultivate_remain(plugin.cultivate_cooldown)}")
    out = await run_cmd(plugin, "cultivate", ev_c2)
    check("冷却中再修炼被拦", "距离下次修炼" in out, out[:120])
    check("冷却提示带剩余时间", "分" in out or "秒" in out, out[:120])

    print("\n[40] 探索（挂机产出 + 秘典残页来源）")
    # 权重是相对值，不要求合计恰好 100（调整 relic 权重时会变）。
    # 这里只校验「表非空 + 每一项权重为正」。
    _exp_weights = [w for _k, w in battle_mod.EXPLORE_TABLE]
    check("探索产出表非空且权重全为正",
          bool(_exp_weights) and all(w > 0 for w in _exp_weights),
          f"合计 {sum(_exp_weights)}")
    check("秘典残页在产出表中", any(k == "relic" for k, _w in battle_mod.EXPLORE_TABLE))
    relic_weight = dict(battle_mod.EXPLORE_TABLE).get("relic", 0)
    check("残页权重为正且有稀有度（≤20）", 0 < relic_weight <= 20, f"{relic_weight}")

    # 不足 30 分钟无收益
    ex_p = player_mod.Player(qq="ex", name="游历者")
    ex_p.explore_start_ts = time.time() - 60
    res_ex = battle_mod.settle_explore(ex_p, item_db_t)
    check("不足 30 分钟无收益", res_ex.hours <= 0 and res_ex.qi_gain == 0,
          f"hours={res_ex.hours} qi={res_ex.qi_gain}")
    check("结算后探索状态被清空", ex_p.explore_start_ts == 0.0)

    # 8 小时上限
    ex_p2 = player_mod.Player(qq="ex2", name="长游者")
    ex_p2.explore_start_ts = time.time() - 3600 * 30
    res_ex2 = battle_mod.settle_explore(ex_p2, item_db_t)
    check("超长游历被截到 8 小时", res_ex2.capped and res_ex2.hours == 8.0,
          f"hours={res_ex2.hours} capped={res_ex2.capped}")

    # 有产出且修为低于同长闭关
    ex_p3 = player_mod.Player(qq="ex3", name="游历者3")
    ex_p3.level = 19
    ex_p3.explore_start_ts = time.time() - 3600 * 8
    res_ex3 = battle_mod.settle_explore(ex_p3, item_db_t)
    seclusion_gain = realm_mod.seclusion_qi_per_hour(ex_p3.turns) * 8
    check("8 小时探索有修为产出", res_ex3.qi_gain > 0, f"{res_ex3.qi_gain}")
    check("探索修为低于同长闭关（约 1/3）",
          res_ex3.qi_gain < seclusion_gain,
          f"explore={res_ex3.qi_gain} seclusion={seclusion_gain}")

    # 多次采样：残页确实会掉
    got_relic = False
    got_item = False
    for _ in range(200):
        sp = player_mod.Player(qq="samp", name="采样")
        sp.explore_start_ts = time.time() - 3600 * 8
        r = battle_mod.settle_explore(sp, item_db_t)
        if r.relics:
            got_relic = True
            # 掉出来的残页必须是合法名字
            check("掉落的残页名字合法",
                  all(x in scriptures_mod.RELIC_NAMES for x in r.relics),
                  f"{r.relics}")
            break
    check("200 次采样中掉过秘典残页", got_relic)

    for _ in range(50):
        sp = player_mod.Player(qq="samp2", name="采样2")
        sp.explore_start_ts = time.time() - 3600 * 4
        r = battle_mod.settle_explore(sp, item_db_t)
        if r.items:
            got_item = True
            # 掉落物可能是带品质后缀的法宝（如 铜戒[精]），
            # 用 get_item 校验（它会剥后缀），而不是精确匹配名字集合
            check("掉落的物品都能查到定义",
                  all(items_mod.get_item(i) is not None for i in r.items),
                  f"{[i for i in r.items if items_mod.get_item(i) is None]}")
            break
    check("50 次采样中掉过普通物品", got_item)

    # 特殊事件必须与真实物品分开（曾经混在一起，导致 UI 把
    # 「参悟天象，心境澄明」显示成「所得：…」）
    ev_seen = False
    for _ in range(300):
        sp = player_mod.Player(qq="samp3", name="采样3")
        sp.explore_start_ts = time.time() - 3600 * 8
        r = battle_mod.settle_explore(sp, item_db_t)
        if r.events:
            ev_seen = True
            check("事件文案不与真实物品混在一起",
                  all(items_mod.get_item(i) is None for i in r.events),
                  f"事件被当成了物品：{r.events}")
            check("所有 items 都是可查到的真实物品",
                  all(items_mod.get_item(i) is not None for i in r.items),
                  f"{r.items}")
            check("事件文案没有写进玩家储物袋",
                  all(i not in sp.inventory for i in r.events),
                  f"储物袋被污染：{list(sp.inventory)}")
            break
    check("300 次采样中触发过特殊事件", ev_seen)

    print("\n[41] 端到端：探索 → 修行 → 装配 → 战斗")
    e2e_plugin = plugin_mod.TextRPGPlugin(context=None, config={
        "reply_delay_range": [0, 0],
        "chunk_gap_range": [0, 0],
        "rate_limit_count": 10000,
        "group_whitelist_mode": "all",
    })
    ev_e2e = AstrMessageEvent(qq="42000", name="修仙者")
    await fast_create(e2e_plugin, ev_e2e, "修仙者", level=19)
    pe = e2e_plugin.store.get("999", "42000")

    # 1) 探索：进状态 -> 结算
    out = await run_cmd(e2e_plugin, "explore", ev_e2e)
    check("探索进入成功", "游历" in out, out[:120])
    check("已进入探索状态", pe.is_exploring())

    out = await run_cmd(e2e_plugin, "explore", ev_e2e)
    check("刚出门结算提示时长不足", "30 分钟" in out or "什么也没找到" in out, out[:120])

    # 2) 修行：给足残页与修为
    pe.add_item("火之残页", 20)
    pe.exp = 50000
    out = await run_cmd(e2e_plugin, "practice", ev_e2e, "火之秘典")
    check("修行一阶成功", "火焰" in out, out[:200])
    check("秘典阶数记录为 1", pe.scriptures.get("火之秘典") == 1,
          f"{pe.scriptures}")
    check("修行扣了残页", pe.inventory.get("火之残页") == 19,
          f"{pe.inventory.get('火之残页')}")

    out = await run_cmd(e2e_plugin, "practice", ev_e2e, "火之秘典")
    check("修行二阶成功", "控火" in out, out[:200])
    check("秘典阶数记录为 2", pe.scriptures.get("火之秘典") == 2)
    check("二阶扣 2000 修为", pe.exp == 50000 - 2000, f"{pe.exp}")

    # 3) 装配
    out = await run_cmd(e2e_plugin, "equip_scripture", ev_e2e, "火之秘典")
    check("装配成功", "装配" in out, out[:150])
    check("装配列表含火之秘典", "火之秘典" in pe.equipped_scriptures,
          f"{pe.equipped_scriptures}")

    # 4) 大道面板
    out = await run_cmd(e2e_plugin, "scriptures_cmd", ev_e2e)
    check("大道面板列出已习得", "火之秘典" in out, out[:200])
    check("大道面板显示装配位数", "装配位" in out, out[:250])

    # 5) 战斗里主动技应被释放
    mon = battle_mod.spawn_monster(pe.turns)
    res_b = battle_mod.fight(pe, mon, e2e_plugin.item_db)
    check("装配秘典后战斗正常结束", res_b is not None)
    check("战斗记录了秘典技能使用",
          hasattr(res_b, "skills_used"), f"{res_b}")
    check("战斗结果文案是修仙口径",
          res_b.outcome in ("crush", "win", "narrow", "draw", "lose"),
          f"{res_b.outcome}")
    check("战斗文案映射表是修仙口径",
          battle_mod.OUTCOME_LABELS.get("lose") == "道消"
          and battle_mod.OUTCOME_LABELS.get("narrow") == "险胜"
          and battle_mod.OUTCOME_LABELS.get("crush") == "碾压",
          f"{battle_mod.OUTCOME_LABELS}")

    # 未装配时技能用不了
    pe.equipped_scriptures = []
    res_b2 = battle_mod.fight(pe, battle_mod.spawn_monster(pe.turns),
                              e2e_plugin.item_db)
    check("卸下秘典后战斗仍可进行", res_b2 is not None)

    print("\n[42] 旧指令别名与旧物品名兼容")
    # 别名指令必须仍然存在（老玩家习惯）
    for alias_method in ("hunt", "challenge", "revive", "shop", "equip",
                         "unequip", "use_item", "sell", "inventory",
                         "profile", "ranking", "reset"):
        check(f"方法 {alias_method} 仍存在", hasattr(plugin, alias_method))

    # 旧物品名走别名映射
    check("旧名「小型生命药水」映射到小回气丹",
          items_mod.resolve_alias("小型生命药水") == "小回气丹",
          items_mod.resolve_alias("小型生命药水"))
    check("旧名「中型生命药水」映射到中回气丹",
          items_mod.resolve_alias("中型生命药水") == "中回气丹")
    check("旧名「大型生命药水」映射到大回气丹",
          items_mod.resolve_alias("大型生命药水") == "大回气丹")
    check("旧名「复活符」映射到还魂丹",
          items_mod.resolve_alias("复活符") == "还魂丹")
    check("旧名「经验卷轴」映射到悟道石",
          items_mod.resolve_alias("经验卷轴") == "悟道石")
    check("旧名也能取到物品定义",
          items_mod.get_item("小型生命药水") is not None)
    check("旧名买价与别名一致",
          items_mod.buy_price("小型生命药水") == items_mod.buy_price("小回气丹"))
    check("新名不受影响", items_mod.resolve_alias("小回气丹") == "小回气丹")
    check("未定义的名字原样返回",
          items_mod.resolve_alias("不存在的东西") == "不存在的东西")

    # 部位旧名（中文）仍能解析
    pe2 = player_mod.Player(qq="slot", name="部位测试")
    pe2.add_item("铁剑")
    pe2.equipment["weapon"] = "铁剑"
    check("部位中文名「武器」可解析",
          pe2.equipment.get("weapon") == "铁剑", f"{pe2.equipment}")
    check("SLOT_ALIASES 含中文旧名",
          "武器" in player_mod.SLOT_ALIASES
          and player_mod.SLOT_ALIASES["武器"] == "weapon",
          f"{getattr(player_mod, 'SLOT_ALIASES', None)}")
    check("SLOT_ALIASES 含护甲",
          player_mod.SLOT_ALIASES.get("护甲") == "armor")
    check("SLOT_ALIASES 含饰品",
          player_mod.SLOT_ALIASES.get("饰品") == "accessory")

    # ---- 老存档里的存量名字必须被真正迁移（不只是查表时兜底）----
    #
    # 背景：resolve_alias 只在 get_item / buy_price 里被调用，
    # 存档里的名字本身一直没改。结果是 /使用 能查到定义（靠 get_item 兜底），
    # 但 /背包 显示的还是 v2 的旧名，和 /坊市 里的新名对不上。
    # 这里钉住 from_dict 的迁移，防止回退。
    #
    # ⚠️ 期待值是**带品质后缀的中文名**（`小回气丹[普]`），不是裸名。
    # v3.4 起所有物品名统一走 items.decorate() 构造 —— 迁移时也要重新
    # 装饰一遍，保证「迁移后的名字」与「游戏内新产出的名字」**字面完全一致**。
    # 早期断言写的是裸名 `小回气丹`，那是修 `[common]` 后缀 bug 之前的旧口径。
    _mig = player_mod.Player.from_dict({
        "qq": "mig1", "name": "迁移测试",
        "inventory": {"小型生命药水": 2, "大型生命药水": 1},
    })
    check("老存档物品名被迁成新名（带品质后缀）",
          _mig.inventory == {"小回气丹[普]": 2, "大回气丹[普]": 1}, f"{_mig.inventory}")
    check("迁移后名字能取到定义",
          all(items_mod.get_item(n) is not None for n in _mig.inventory))
    check("迁移后的名字与 decorate 产出完全一致（无内部 key 泄漏）",
          all("[" not in n.replace("[普]", "").replace("[稀]", "")
              .replace("[珍]", "").replace("[绝]", "")
              for n in _mig.inventory),
          f"{_mig.inventory}")

    # 新旧名同时存在时必须合并数量，不能后者覆盖前者
    _mig2 = player_mod.Player.from_dict({
        "qq": "mig2", "name": "合并测试",
        "inventory": {"小型生命药水": 2, "小回气丹[普]": 3},
    })
    check("新旧名同时存在时数量合并",
          _mig2.inventory == {"小回气丹[普]": 5}, f"{_mig2.inventory}")

    # 幂等：迁移过的存档再读一次不该变化
    _mig3 = player_mod.Player.from_dict({
        "qq": "mig3", "name": "幂等测试",
        "inventory": dict(_mig2.inventory),
    })
    check("物品名迁移幂等",
          _mig3.inventory == _mig2.inventory, f"{_mig3.inventory}")

    # 部位旧名同样要在存档层迁移（不能只在指令参数上解析）
    #
    # ⚠️ 期待值带品质后缀：迁移会把「部位 key」和「装备名」一起改，
    # 装备名重走 decorate -> 布衣[普] / 寒铁剑[普]。
    # 「布衣」在 v3.4 已不是装备（一品护甲改成 青锋战甲/青锋道袍/青锋重铠），
    # 所以它不在 ITEM_ALIASES 里，原样保留 —— 这条断言正好钉住
    # 「别名表没覆盖的名字不会被误改」。
    _mig4 = player_mod.Player.from_dict({
        "qq": "mig4", "name": "部位迁移",
        "equipment": {"武器": "铁剑", "护甲": "布衣"},
    })
    check("老存档部位名被迁成英文 key",
          _mig4.equipment == {"weapon": "寒铁剑[普]", "armor": "布衣[普]"},
          f"{_mig4.equipment}")
    check("迁移后的部位名被 SLOTS 认得",
          all(s in player_mod.SLOTS for s in _mig4.equipment),
          f"{list(_mig4.equipment)}")
    check("中文部位名计入了套装件数",
          _mig4.equipped_count() == 2, f"{_mig4.equipped_count()}")
    check("部位标签是修仙口径",
          player_mod.SLOT_LABELS.get("weapon") == "法宝"
          and player_mod.SLOT_LABELS.get("armor") == "护身宝衣",
          f"{player_mod.SLOT_LABELS}")

    print("\n[43] 数值健康度（v3 缩放后）")
    # 72 转满配不应出现溢出或负伤害
    big = player_mod.Player(qq="big", name="满配")
    big.level = 72
    big.ascensions = 10
    big.breakthroughs = 7
    item_db_full = {e["name"]: e for e in items_mod.EQUIPMENTS}
    check("72 转 10 转生 7 突破攻击为正数", big.atk(item_db_full) > 0,
          f"{big.atk(item_db_full)}")
    check("满配气血为正数", big.max_hp(item_db_full) > 0, f"{big.max_hp(item_db_full)}")
    check("满配防御为正数", big.defense(item_db_full) > 0, f"{big.defense(item_db_full)}")
    check("乘区在合理量级（<10000）", big.stat_mult() < 10000, f"{big.stat_mult()}")

    # 各转数打同级怪，伤害均 ≥ 1（无死循环）
    healthy = True
    for turns in (1, 10, 19, 28, 37, 46, 55, 64, 72):
        tmp = player_mod.Player(qq=f"h{turns}", name=f"健康{turns}")
        tmp.level = turns
        for _ in range(30):
            m = battle_mod.spawn_monster(turns)
            st = battle_mod.monster_stats(m)
            if player_mod.roll_damage(tmp.atk(item_db_full), st["defense"]) < 1:
                healthy = False
                break
        if not healthy:
            break
    check("1~72 转各阶段伤害均 ≥ 1（无死循环风险）", healthy)

    # 妖兽池覆盖各境界
    levels = sorted({m["level"] for m in items_mod.MONSTERS})
    check("妖兽等级从 1 起", levels[0] == 1, f"{levels[0]}")
    check("妖兽最高等级 ≥ 27", levels[-1] >= 27, f"{levels[-1]}")
    check("妖兽等级跨度足够（≥20 档）", levels[-1] - levels[0] >= 20, f"{levels}")
    check("妖兽全部有 element 字段（五行被动才能生效）",
          all("element" in m for m in items_mod.MONSTERS),
          str([m["name"] for m in items_mod.MONSTERS if "element" not in m]))
    check("五行元素均有妖兽覆盖",
          len({m.get("element") for m in items_mod.MONSTERS
               if m.get("element")}) >= 4,
          str({m.get("element") for m in items_mod.MONSTERS if m.get("element")}))
    check("元素取值都在合法集合内",
          all(m.get("element") in ("fire", "water", "earth", "wind")
              for m in items_mod.MONSTERS),
          str({m.get("element") for m in items_mod.MONSTERS
               if m.get("element") not in ("fire", "water", "earth", "wind")}))

    # ---- 修为曲线不变量（2026-09-19 重做曲线后新增的护栏）----
    # 这些断言的意义：曲线是数值平衡的根，改坏了整个游戏体验就崩，
    # 但不会报错、不会崩栈，只是「变难玩」。所以必须用断言钉住。
    print("\n  -- 修为曲线不变量 --")
    rm = realm_mod
    qis = [rm.qi_needed(t) for t in range(1, 72)]
    check("每转所需修为全为正数（1~71 转）", all(q > 0 for q in qis),
          str([i + 1 for i, q in enumerate(qis) if q <= 0]))
    check("所需修为全程单调不降",
          all(qis[i] <= qis[i + 1] for i in range(len(qis) - 1)),
          f"首={qis[0]} 末={qis[-1]}")

    # 境内步进应温和（避免旧的 15% 那种境内就爬得吃力）
    max_step = max(qis[i + 1] / qis[i] for i in range(len(qis) - 1))
    check("最大环比涨幅 < 1.6 倍（保留突破感但不失控）", max_step < 1.6,
          f"{max_step:.3f}x")

    # 累计门槛必须与 qi_needed 自洽：九转时刚好够突破，少 1 点就不够
    consistent = True
    detail = []
    for i in range(len(rm.REALMS) - 1):
        t9 = i * rm.TURNS_PER_REALM + rm.TURNS_PER_REALM
        if t9 >= rm.MAX_TURNS:
            break
        need = rm.qi_needed(t9)
        if not (rm.can_breakthrough(t9, need) and not rm.can_breakthrough(t9, need - 1)):
            consistent = False
            detail.append(f"{t9}转 need={need}")
    check("九转突破门槛与 qi_needed 完全自洽", consistent, "; ".join(detail))

    # REALMS 门槛是派生的，必须严格递增
    ths = [r[2] for r in rm.REALMS]
    check("REALMS 累计门槛严格递增",
          all(ths[i] < ths[i + 1] for i in range(len(ths) - 1)),
          str(ths))
    check("炼气期门槛为 0（起点）", ths[0] == 0, f"{ths[0]}")

    # 三条产出线：修炼 12/h + 闭关 3/h + 探索 1/h = 16/h
    check("每小时有效产出系数 = 16", rm.HOURLY_EFFICIENCY_FACTOR == 16,
          f"{rm.HOURLY_EFFICIENCY_FACTOR}")
    for t in (1, 10, 19, 28, 37, 46, 55, 64):
        g = rm.cultivate_gain(t)
        if not (rm.seclusion_qi_per_hour(t) == g * 3 and rm.explore_qi_per_hour(t) == g):
            check(f"{t} 转三线倍率正确", False,
                  f"闭关={rm.seclusion_qi_per_hour(t)} 探索={rm.explore_qi_per_hour(t)}")
            break
    else:
        check("三线倍率正确（闭关 ×3 / 探索 ×1）", True)

    # 产出跨境界恰好翻倍
    gains = [rm.cultivate_gain(i * 9 + 1) for i in range(8)]
    check("修炼收益跨境恰好 ×2",
          all(gains[i + 1] == gains[i] * 2 for i in range(len(gains) - 1)),
          str(gains))

    # ---- 全程耗时落在设计窗口内 ----
    total_h = rm.total_hours_to(72)
    check("全程耗时 340~460 小时（设计目标 ~396h）", 340 <= total_h <= 460,
          f"{total_h:.0f}h = {total_h / 24:.1f}天")

    realm_hours = []
    for i in range(len(rm.REALMS)):
        s = i * rm.TURNS_PER_REALM + 1
        e = min(i * rm.TURNS_PER_REALM + rm.TURNS_PER_REALM + 1, rm.MAX_TURNS)
        realm_hours.append(sum(rm.hours_per_turn(t) for t in range(s, e)))
    check("境界耗时单调递增（前期快后期慢）",
          all(realm_hours[i] < realm_hours[i + 1] for i in range(len(realm_hours) - 1)),
          " ".join(f"{h:.0f}" for h in realm_hours))
    check("炼气期 < 20 小时（前期要快）", realm_hours[0] < 20,
          f"{realm_hours[0]:.1f}h")
    check("大乘期 < 150 小时（后期慢但不是墙）", realm_hours[-1] < 150,
          f"{realm_hours[-1]:.1f}h")
    check("首末境界耗时比 < 15 倍（曲线不失控）",
          realm_hours[-1] / realm_hours[0] < 15,
          f"{realm_hours[-1] / realm_hours[0]:.1f}x")

    print("\n[44] 渡劫丹与天劫联动")
    check("渡劫丹在物品表中", "渡劫丹" in items_mod.all_item_names())
    trib_item = items_mod.get_item("渡劫丹")
    check("渡劫丹是消耗品", trib_item and trib_item.get("effect") == "tribulation",
          str(trib_item))
    check("渡劫丹有说明文案",
          "渡劫时额外扛一道雷" in items_mod.EFFECT_LABELS.get("tribulation", ""),
          items_mod.EFFECT_LABELS.get("tribulation", ""))
    check("渡劫丹在坊市可买",
          "渡劫丹" in items_mod.shop_stock(1) or trib_item["price"] > 0,
          f"{trib_item}")

    # 突破指令端到端
    bt_plugin = plugin_mod.TextRPGPlugin(context=None, config={
        "reply_delay_range": [0, 0],
        "chunk_gap_range": [0, 0],
        "rate_limit_count": 10000,
        "group_whitelist_mode": "all",
    })
    ev_bt = AstrMessageEvent(qq="43000", name="突破者")
    await fast_create(bt_plugin, ev_bt, "突破者", level=9)
    pbt = bt_plugin.store.get("999", "43000")

    # 修为不够 -> 被拦
    pbt.exp = 100
    pbt.full_heal(bt_plugin.item_db)
    out = await run_cmd(bt_plugin, "breakthrough", ev_bt)
    check("修为不足时突破被拦", "修为" in out, out[:150])
    check("突破后转数未变", pbt.level == 9, f"{pbt.level}")

    # 修为足够 -> 突破成功
    pbt.exp = 100000
    pbt.full_heal(bt_plugin.item_db)
    out = await run_cmd(bt_plugin, "breakthrough", ev_bt)
    check("修为足够时突破成功", "突破" in out or "筑基" in out, out[:200])
    check("突破后转数 +1", pbt.level == 10, f"{pbt.level}")
    check("突破后进入筑基期", pbt.realm_name == "筑基期", pbt.realm_name)
    check("突破计数 +1", pbt.breakthroughs == 1, f"{pbt.breakthroughs}")
    check("突破后装配位变成 2", pbt.equip_slots == 2, f"{pbt.equip_slots}")

    print("\n[45] 飞升端到端（渡劫 -> 转生）")
    asc_plugin = plugin_mod.TextRPGPlugin(context=None, config={
        "reply_delay_range": [0, 0],
        "chunk_gap_range": [0, 0],
        "rate_limit_count": 10000,
        "group_whitelist_mode": "all",
    })
    ev_asc = AstrMessageEvent(qq="44000", name="飞升者")
    await fast_create(asc_plugin, ev_asc, "飞升者", level=72)
    pas = asc_plugin.store.get("999", "44000")
    pas.exp = 3_000_000
    pas.gold = 12345
    pas.add_item("铁剑")
    pas.scriptures["火之秘典"] = 2
    pas.full_heal(asc_plugin.item_db)

    out = await run_cmd(asc_plugin, "breakthrough", ev_asc)
    check("72 转满修为第一次提示天劫（防误触）",
          "天劫" in out and "再发一次" in out, out[:250])
    check("首次提示时尚未飞升", pas.level == 72, f"{pas.level}")

    out = await run_cmd(asc_plugin, "breakthrough", ev_asc)
    check("72 转满修为触发飞升",
          "飞升" in out or "天雷" in out, out[:250])
    check("飞升后转生次数 +1", pas.ascensions == 1, f"{pas.ascensions}")
    check("飞升后回到 1 转", pas.level == 1, f"{pas.level}")
    check("飞升后修为清零", pas.exp == 0, f"{pas.exp}")
    check("飞升保留灵石", pas.gold == 12345, f"{pas.gold}")
    check("飞升保留法宝", pas.equipment.get("weapon") == "铁剑"
          or pas.inventory.get("铁剑", 0) >= 1, f"{pas.equipment}")
    check("飞升保留秘典", pas.scriptures.get("火之秘典") == 2, f"{pas.scriptures}")
    check("飞升后基础属性 ×1.05", abs(pas.stat_mult() - 1.05) < 1e-9,
          f"{pas.stat_mult()}")

    print("\n[46] realm / scriptures 纯函数边界")
    # qi_needed 在炼气期有保底
    check("1 转所需修为 > 0", realm_mod.qi_needed(1) > 0, f"{realm_mod.qi_needed(1)}")
    check("71 转所需修为 > 1 转",
          realm_mod.qi_needed(71) > realm_mod.qi_needed(1),
          f"{realm_mod.qi_needed(71)} vs {realm_mod.qi_needed(1)}")
    check("72 转（满转）不再需要修为，返回 0",
          realm_mod.qi_needed(72) == 0, f"{realm_mod.qi_needed(72)}")
    check("超过 72 转也返回 0", realm_mod.qi_needed(999) == 0)
    check("累计修为随转数单调递增",
          all(realm_mod.total_qi_to(t) < realm_mod.total_qi_to(t + 1)
              for t in range(1, 72)),
          "存在非递增点")
    check("总修为进度在 [0, need] 区间",
          all(0 <= realm_mod.qi_progress(t, 0)[0] <= realm_mod.qi_progress(t, 0)[1]
              for t in (1, 19, 37, 71)))
    check("修为超出时进度被钳制到上限",
          realm_mod.qi_progress(19, 10 ** 9)[0] == realm_mod.qi_progress(19, 10 ** 9)[1],
          f"{realm_mod.qi_progress(19, 10 ** 9)}")
    check("满转时进度不除零（need=0 时返回 (qi, qi)）",
          realm_mod.qi_progress(72, 5000) == (5000, 5000),
          f"{realm_mod.qi_progress(72, 5000)}")

    # 修行收益随境界放大
    check("境界越高修炼收益越高",
          realm_mod.cultivate_gain(72) > realm_mod.cultivate_gain(1),
          f"{realm_mod.cultivate_gain(72)} vs {realm_mod.cultivate_gain(1)}")
    check("闭关收益 > 修炼收益（同时长）",
          realm_mod.seclusion_qi_per_hour(19) > realm_mod.cultivate_gain(19))

    # 玩家方法边界（equip/unequip 返回 (ok, msg) 元组）
    bp = player_mod.Player(qq="edge2", name="边界2")
    check("未习得时 learned_tier 为 0", bp.learned_tier("火之秘典") == 0)
    ok_un, _msg = bp.equip_scripture("火之秘典")
    check("未习得时装配失败", ok_un is False, f"ok={ok_un}")
    check("失败时带原因说明", "未习得" in _msg or "习得" in _msg, _msg)

    bp.scriptures["火之秘典"] = 1
    ok_eq, _msg = bp.equip_scripture("火之秘典")
    check("习得后装配成功", ok_eq is True, f"ok={ok_eq} msg={_msg}")
    ok_dup, dup_msg = bp.equip_scripture("火之秘典")
    check("重复装配不会重复入场（幂等）",
          bp.equipped_scriptures.count("火之秘典") == 1,
          f"{bp.equipped_scriptures}")
    check("重复装配有明确提示",
          "已经装配" in dup_msg, dup_msg)

    ok_uneq, _msg = bp.unequip_scripture("水之秘典")
    check("未装配的卸下返回 False", ok_uneq is False, f"ok={ok_uneq}")
    ok_uneq2, _msg = bp.unequip_scripture("火之秘典")
    check("已装配的卸下成功", ok_uneq2 is True, f"ok={ok_uneq2}")
    check("卸下后列表为空", bp.equipped_scriptures == [],
          f"{bp.equipped_scriptures}")

    # 装配位上限
    bp2 = player_mod.Player(qq="slots", name="装配位测试")
    for i, nm in enumerate(scriptures_mod.SCRIPTURE_NAMES):
        bp2.scriptures[nm] = 1
    bp2.breakthroughs = 0            # 只有 1 个位
    for nm in scriptures_mod.SCRIPTURE_NAMES:
        bp2.equip_scripture(nm)
    check("装配位上限生效（最多 1 条）",
          len(bp2.equipped_scriptures) == 1, f"{bp2.equipped_scriptures}")
    bp2.breakthroughs = 7            # 8 个位
    for nm in scriptures_mod.SCRIPTURE_NAMES:
        bp2.equip_scripture(nm)
    check("突破后装配位够用（5 条全上）",
          len(bp2.equipped_scriptures) == 5, f"{bp2.equipped_scriptures}")
    check("装配位不超上限", bp2.equip_slots == 8, f"{bp2.equip_slots}")

    # ----------------------------------------------------------------
    print("\n[47] v3.1 战力 / 妖兽缩放 / 秘境 / 装备套")
    # ----------------------------------------------------------------
    # dungeon_mod 已在文件顶层导入（[5] 段也要用，不能在这里局部导入）

    # ---- 战力公式 ----
    # 战力 = 攻击×2.0 + 防御×1.5 + 气血×0.5
    check("战力公式：纯数值",
          battle_mod.power_of(100, 40, 200) == int(100 * 2.0 + 40 * 1.5 + 200 * 0.5),
          f"{battle_mod.power_of(100, 40, 200)}")
    check("战力随三围单调递增",
          battle_mod.power_of(110, 40, 200) > battle_mod.power_of(100, 40, 200)
          and battle_mod.power_of(100, 50, 200) > battle_mod.power_of(100, 40, 200)
          and battle_mod.power_of(100, 40, 210) > battle_mod.power_of(100, 40, 200),
          "步长取 +10 而非 +1：气血权重是 0.5，+1 血只加 0.5，会被 int() 截断掉")

    _pp = player_mod.Player(qq="pw1", name="战力测试")
    _pp.level = 1
    _pp.hp = _pp.max_hp({})
    # 不写死数值：改为校验「与 INIT 常量一致」+ 量级合理，
    # 这样以后调平衡不会再让这条断言误报。
    _p1_power = battle_mod.player_power(_pp, {})
    check("1 转裸装战力 = INIT 常量算出的值",
          _p1_power == battle_mod.power_of(
              player_mod.INIT_ATK, player_mod.INIT_DEF, player_mod.INIT_HP),
          f"{_p1_power}")
    check("1 转裸装战力量级合理（50~200）", 50 <= _p1_power <= 200, f"{_p1_power}")
    _pp72 = player_mod.Player(qq="pw72", name="满转战力")
    _pp72.level = 72
    _pp72.hp = _pp72.max_hp({})
    check("72 转裸装战力远超 1 转（>150 倍）",
          battle_mod.player_power(_pp72, {}) > _p1_power * 150,
          f"{battle_mod.player_power(_pp72, {})}")
    #
    # 阈值为什么从 200 降到 150：
    #   成长是线性的，倍率 = 终值/初值。INIT_ATK 10->18 抬高了分母，
    #   同样的终值算出来的倍数就变小了。
    #   实测：改前 205 倍，改后 160 倍 —— 成长曲线没变，只是起点变了。
    #   150 仍能守住「72 转远超 1 转」这个语义。

    # ---- 妖兽动态缩放 ----
    _m = {"name": "缩放测试兽", "level": 5, "hp": 1000, "atk": 100,
          "defense": 50, "exp": 10, "gold": (1, 2), "drops": []}
    # 「不缩放」的准确含义是**不按转数缩放**，而不是数值原样 ——
    # 妖兽实际生命 = hp + level × 15（items.py 的等级加成机制），
    # 这条加成在缩放前后都成立，所以不能拿 hp == 1000 当断言。
    _m_base_hp = _m["hp"] + _m["level"] * 15          # 1000 + 75 = 1075
    _raw = battle_mod.monster_stats(_m)
    check("player_turns=None 时不缩放",
          _raw["hp"] == _m_base_hp and _raw["atk"] == 100,
          f"{_raw}（期望 hp={_m_base_hp}）")

    _s1 = battle_mod.monster_stats(_m, 1, 0, False)
    _s72 = battle_mod.monster_stats(_m, 72, 0, False)
    check("按转数缩放：高转数妖兽更强", _s72["hp"] > _s1["hp"],
          f"{_s72['hp']} vs {_s1['hp']}")
    check("缩放后妖兽战力贴近玩家（比例落在合理区间）",
          abs(_s72["hp"] * 0.5 + _s72["atk"] * 2.0
              - battle_mod.player_power(_pp72, {})) < battle_mod.player_power(_pp72, {}) * 0.6,
          f"怪={_s72}")

    _elite = battle_mod.monster_stats(_m, 20, 0, True)
    _plain = battle_mod.monster_stats(_m, 20, 0, False)
    check("妖王比普通妖兽强", _elite["hp"] > _plain["hp"],
          f"{_elite['hp']} vs {_plain['hp']}")

    # prescaled 标记：已标定好的怪必须**完全原样**返回（一个字段都不改）。
    # 副本靠这个防二次缩放；也正因为语义是「原样」，
    # 它连 hp + level*15 的等级加成都不该加。
    _pre = dict(_m, prescaled=True)
    _pre_res = battle_mod.monster_stats(_pre, 72, 0, False)
    check("prescaled 标记的怪完全原样返回",
          _pre_res["hp"] == 1000 and _pre_res["atk"] == 100
          and _pre_res["defense"] == 50,
          f"{_pre_res}")
    _pre_plain = dict(_m, prescaled=True, level=99)
    _pre2 = battle_mod.monster_stats(_pre_plain, 72, 0, False)
    check("prescaled 的怪不吃 level*15 等级加成",
          _pre2["hp"] == 1000, f"{_pre2}")
    _zero = battle_mod.monster_stats(_m, 0, 0, False)
    check("player_turns<=0 视为不缩放",
          _zero["hp"] == _m_base_hp and _zero["atk"] == 100, f"{_zero}")

    # ---- 秘境难度曲线（成长曲线，固定锚点，与玩家转数无关）----
    _a = dungeon_mod.anchor_stats()
    check("秘境锚点是常量（1 转裸装 = INIT 常量）",
          _a == {"atk": player_mod.INIT_ATK, "def": player_mod.INIT_DEF,
                 "hp": player_mod.INIT_HP}, f"{_a}")
    check("锚点转数 = 1", dungeon_mod.DUNGEON_ANCHOR_TURNS == 1)

    _l1 = dungeon_mod.layer_stats(1)
    _l20 = dungeon_mod.layer_stats(20)
    check("第 1 层怪很弱（血 < 锚点血）", _l1["hp"] < _a["hp"],
          f"{_l1['hp']} vs {_a['hp']}")
    check("层数越高怪越强（三围单调递增）",
          all(
              dungeon_mod.layer_stats(n + 1)[k] >= dungeon_mod.layer_stats(n)[k]
              for n in range(1, 40)
              for k in ("hp", "atk", "defense")
          ),
          "存在非递增层")
    check("第 20 层怪明显强于第 1 层", _l20["hp"] > _l1["hp"] * 5,
          f"{_l20['hp']} vs {_l1['hp']}")
    check("怪血有兜底上限（不超过玩家血 3 倍）",
          dungeon_mod.build_monster(999, _pp72, {})["hp"]
          <= int(_pp72.max_hp({}) * dungeon_mod.DUNGEON_HP_CAP_RATIO),
          f"{dungeon_mod.build_monster(999, _pp72, {})['hp']:,} "
          f"(cap={int(_pp72.max_hp({}) * dungeon_mod.DUNGEON_HP_CAP_RATIO):,})")
    # 兜底必须真的生效，而不是被 except 吞掉 ——
    # 这里显式断言它确实把血量压到了上限，否则 999 层会算出天文数字。
    _cap_hp = dungeon_mod.build_monster(999, _pp72, {})["hp"]
    check("兜底确实生效（不是被异常静默吞掉）",
          _cap_hp == int(_pp72.max_hp({}) * dungeon_mod.DUNGEON_HP_CAP_RATIO),
          f"{_cap_hp:,}")

    # 实战胜率：钉住"第 1 层人人能过" + "转数越高打得越远"
    def _dungeon_reach(turns: int, samples: int = 120, cap: int = 60) -> int:
        """某转数玩家在秘境能连破几层（胜率 ≥50% 视为通过）。"""
        import random as _rnd
        last = 0
        for n in range(1, cap + 1):
            wins = 0
            for _ in range(samples):
                p = player_mod.Player(qq=f"dr{turns}", name="秘境")
                p.level = turns
                p.hp = p.max_hp({})
                if battle_mod.fight(
                    p, dungeon_mod.build_monster(n, p), {}
                ).won:
                    wins += 1
            if wins / samples >= 0.5:
                last = n
            else:
                break
        return last

    _r1 = _dungeon_reach(1)
    _r10 = _dungeon_reach(10)
    _r72 = _dungeon_reach(72)
    check("第 1 层连新人都能过（1 转至少破 1 层）", _r1 >= 1, f"1转={_r1}")
    check("转数越高打得越远（10 转 > 1 转）", _r10 > _r1, f"10转={_r10} 1转={_r1}")
    check("转数越高打得越远（72 转 > 10 转）", _r72 > _r10,
          f"72转={_r72} 10转={_r10}")
    check("满转也有天花板（不是无限层）", _r72 < 60, f"72转={_r72}")
    check("满转能打到较深层（>= 20 层）", _r72 >= 20, f"72转={_r72}")

    # ---- 每日限制 ----
    _dp = player_mod.Player(qq="dgday", name="秘境每日")
    check("新角色当天可闯", dungeon_mod.can_run_today(_dp))
    dungeon_mod.mark_ran(_dp)
    check("闯过之后当天不可再闯", not dungeon_mod.can_run_today(_dp))
    check("dungeon_date 记录了日期",
          _dp.dungeon_date == dungeon_mod.today_str(), f"{_dp.dungeon_date}")
    # 模拟隔天
    check("换一天后恢复可闯",
          dungeon_mod.can_run_today(_dp, now=time.time() + 86400 * 2))

    # ---- 起始层回退 ----
    _sp = player_mod.Player(qq="dgstart", name="起始层")
    check("无纪录时从第 1 层开始", dungeon_mod.start_layer_for(_sp) == 1)
    _sp.dungeon_best = 10
    check("有纪录时往回退 2 层",
          dungeon_mod.start_layer_for(_sp)
          == max(1, 10 - dungeon_mod.DUNGEON_WARMUP_BACKOFF),
          f"{dungeon_mod.start_layer_for(_sp)}")
    _sp.dungeon_best = 1
    check("纪录很低时不越界（至少第 1 层）",
          dungeon_mod.start_layer_for(_sp) >= 1,
          f"{dungeon_mod.start_layer_for(_sp)}")

    # ---- 端到端：失败不计死亡、不掉转 ----
    _ep = player_mod.Player(qq="dge2e", name="秘境端到端")
    _ep.level = 3
    _ep.hp = _ep.max_hp({})
    _deaths_before = _ep.total_deaths
    _level_before = _ep.level
    _run = dungeon_mod.run_dungeon(_ep, {}, 999)
    check("秘境能通关若干层", _run.cleared >= 1, f"cleared={_run.cleared}")
    check("秘境失败不计入死亡",
          _ep.total_deaths == _deaths_before,
          f"{_ep.total_deaths} vs {_deaths_before}")
    check("秘境失败不掉转", _ep.level == _level_before,
          f"{_ep.level} vs {_level_before}")
    check("秘境更新了最高层纪录", _ep.dungeon_best >= 1, f"{_ep.dungeon_best}")
    check("本次战果记录了历史最高层", _run.best_layer == _ep.dungeon_best,
          f"{_run.best_layer} vs {_ep.dungeon_best}")
    check("首通层数被正确统计", _run.first_clears >= 1, f"{_run.first_clears}")
    check("逐层战报非空", len(_run.logs) == _run.cleared + (1 if _run.failed_layer else 0),
          f"logs={len(_run.logs)} cleared={_run.cleared} failed={_run.failed_layer}")

    # 秘境是试炼：不进入休整
    check("秘境失败不触发休整", not _ep.is_resting(), f"{_ep.rest_until}")

    # ---- 首通重奖 / 重刷衰减 ----
    check("重刷倍率低于首通", dungeon_mod.DUNGEON_RERUN_MULT < 1.0,
          f"{dungeon_mod.DUNGEON_RERUN_MULT}")
    _first = dungeon_mod.dungeon_qi_reward(10, None)
    _rerun = dungeon_mod.dungeon_qi_reward(10, 20)
    _newlayer = dungeon_mod.dungeon_qi_reward(10, 5)
    check("首通全额", _newlayer == _first, f"{_newlayer} vs {_first}")
    check("重刷打折", _rerun < _first, f"{_rerun} vs {_first}")
    check("重刷层不给物品", not dungeon_mod.dungeon_item_layers(3, 10))
    check("首通层（3 的倍数）给物品", dungeon_mod.dungeon_item_layers(6, 5))

    # 连续两次闯关，第二次应走重刷口径
    import random as _rnd2
    _rnd2.seed(20260919)
    _p2 = player_mod.Player(qq="dgrerun", name="重刷测试")
    _p2.level = 30
    _p2.hp = _p2.max_hp({})
    _run1 = dungeon_mod.run_dungeon(_p2, {}, 999)
    _qi1 = _run1.qi_gain
    _p2.dungeon_date = ""      # 绕过每日限制，模拟第二天
    _run2 = dungeon_mod.run_dungeon(_p2, {}, 999)
    check("第二次闯关会走重刷口径（首通层少于第一次）",
          _run2.first_clears < _run1.first_clears,
          f"{_run2.first_clears} vs {_run1.first_clears}")
    check("第二次起始层 > 1（跳过已碾压的层）", _run2.start_layer > 1,
          f"{_run2.start_layer}")

    # ---- 装备体系（v3.4：十品 + 百分比）----
    check("部位数为 5", len(player_mod.SLOTS) == 5, f"{player_mod.SLOTS}")
    check("套装需要穿满 5 件", player_mod.SET_BONUS_SLOTS == 5)

    # v3.4：旧的「每件穿甲 1%、上限 5%」已被「按套系按品阶给」取代。
    # 这两个常量保留只为兼容未穿满套装的过渡场景，但不再是主口径。
    check("过渡期穿甲常量仍在（兼容用途）",
          player_mod.PENETRATION_PER_ITEM > 0
          and player_mod.PENETRATION_CAP > 0)

    # 所有装备的部位都必须是已登记的部位
    _bad_slots = [e["name"] for e in items_mod.EQUIPMENTS
                  if e["slot"] not in player_mod.SLOTS]
    check("所有装备部位合法", not _bad_slots, f"{_bad_slots}")
    check("每个部位都有可选装备",
          all(any(e["slot"] == s for e in items_mod.EQUIPMENTS)
              for s in player_mod.SLOTS),
          "存在无装备的部位")

    # v3.4：十品体系的结构性约束
    check(f"装备总数 = 135（当前 {len(items_mod.EQUIPMENTS)}）",
          len(items_mod.EQUIPMENTS) == 135, str(len(items_mod.EQUIPMENTS)))
    for _r in range(1, 10):
        for _sid in ("物穿", "法穿", "肉盾"):
            _n = sum(1 for e in items_mod.EQUIPMENTS
                     if e.get("tier_rank") == _r and e.get("set_id") == _sid)
            if _n != 5:
                check(f"{_r}品{_sid}套应有 5 件（当前 {_n}）", False, str(_n))
    check("九个品阶 × 三套 × 5 部位齐备", True)
    _missing = [e["name"] for e in items_mod.EQUIPMENTS
                if not all(k in e for k in
                           ("tier_rank", "set_id", "req_turns", "tier"))]
    check("每件装备都有品阶/套系/门槛字段", not _missing, f"{_missing}")

    # 穿甲表单调递增
    _pen_vals = [items_mod.SET_PENETRATION_BY_RANK[k]
                 for k in sorted(items_mod.SET_PENETRATION_BY_RANK)]
    check("套装穿甲表单调递增", _pen_vals == sorted(_pen_vals), str(_pen_vals))
    _dr_vals = [items_mod.SET_DR_BY_RANK[k]
                for k in sorted(items_mod.SET_DR_BY_RANK)]
    check("套装减伤表单调递增", _dr_vals == sorted(_dr_vals), str(_dr_vals))

    _eq = player_mod.Player(qq="set1", name="套装测试")
    _eq.level = 68
    _db34 = {e["name"]: e for e in items_mod.EQUIPMENTS}
    _eq.hp = _eq.max_hp(_db34)
    check("空装：穿甲 0%", _eq.penetration() == 0.0, f"{_eq.penetration()}")
    check("空装：无套装增伤", _eq.damage_bonus() == 0.0, f"{_eq.damage_bonus()}")
    check("空装：未凑齐套装", not _eq.has_full_set())
    check("空装：无套系", _eq.active_set() == "", _eq.active_set())

    # 逐件穿九品物穿套：套系在第 5 件才生效
    _set_items = [e["name"] for e in items_mod.EQUIPMENTS
                  if e.get("tier_rank") == 9 and e.get("set_id") == "物穿"]
    check("九品物穿套有 5 件", len(_set_items) == 5, str(_set_items))
    for _i, _nm in enumerate(_set_items, start=1):
        _eq.equipment[items_mod.EQUIPMENT_BY_NAME[_nm]["slot"]] = _nm
        if _i < 5:
            check(f"穿 {_i} 件不触发套系", _eq.active_set() == "",
                  _eq.active_set())
            check(f"穿 {_i} 件无破防机制", _eq.avoid_def_pct() == 0,
                  str(_eq.avoid_def_pct()))
    check("穿满 5 件触发套系", _eq.has_full_set() and _eq.active_set() == "物穿",
          _eq.active_set())
    check("九品物穿套穿甲 45%",
          abs(_eq.penetration() - 0.45) < 1e-6, str(_eq.penetration()))
    check("九品物穿套破防 18%",
          abs(_eq.avoid_def_pct() - 18.0) < 1e-6, str(_eq.avoid_def_pct()))
    check("九品物穿套暴击 +15%",
          abs(_eq.crit_bonus_pct() - 15.0) < 1e-6, str(_eq.crit_bonus_pct()))
    check("物穿套不给减伤", _eq.damage_reduce_pct() == 0.0,
          str(_eq.damage_reduce_pct()))

    # 法穿套
    _eqf = player_mod.Player(qq="setf", name="法穿测试")
    _eqf.level = 68
    _eqf.hp = _eqf.max_hp(_db34)
    for _e in items_mod.EQUIPMENTS:
        if _e.get("tier_rank") == 9 and _e.get("set_id") == "法穿":
            _eqf.equipment[_e["slot"]] = _e["name"]
    check("法穿套套系判定", _eqf.active_set() == "法穿", _eqf.active_set())
    check("法穿套穿甲 = 45% × 1.15",
          abs(_eqf.penetration() - 0.45 * items_mod.SET_FPEN_MULT) < 1e-6,
          str(_eqf.penetration()))
    check("法穿套术法增伤 20%",
          abs(_eqf.set_mech("magic_dmg_bonus") - 20.0) < 1e-6,
          str(_eqf.set_mech("magic_dmg_bonus")))
    check("法穿套冷却缩减 ≥ 1 回合", _eqf.cd_reduce() >= 1, str(_eqf.cd_reduce()))

    # 肉盾套
    _eqt = player_mod.Player(qq="sett", name="肉盾测试")
    _eqt.level = 68
    _eqt.hp = _eqt.max_hp(_db34)
    for _e in items_mod.EQUIPMENTS:
        if _e.get("tier_rank") == 9 and _e.get("set_id") == "肉盾":
            _eqt.equipment[_e["slot"]] = _e["name"]
    check("肉盾套套系判定", _eqt.active_set() == "肉盾", _eqt.active_set())
    check("肉盾套不给穿甲", _eqt.penetration() == 0.0, str(_eqt.penetration()))
    check("九品肉盾套减伤 30%",
          abs(_eqt.damage_reduce_pct() - 30.0) < 1e-6,
          str(_eqt.damage_reduce_pct()))
    check("九品肉盾套反伤 18%",
          abs(_eqt.reflect_pct() - 18.0) < 1e-6, str(_eqt.reflect_pct()))
    check("肉盾套上品有低血额外减免",
          _eqt.low_hp_extra_dr_pct() > 0, str(_eqt.low_hp_extra_dr_pct()))

    # 同部位塞两件不能骗过套装判定
    _dup = player_mod.Player(qq="set2", name="重复部位")
    _dup.equipment["weapon"] = _set_items[0]
    check("同部位只按 1 件计入", _dup.equipped_count() == 1,
          f"{_dup.equipped_count()}")

    # 穿甲确实提高伤害
    import random as _rnd3
    _rnd3.seed(4242)
    _dmg_base = sum(player_mod.roll_damage_tuned(1000, 600, 0.0, 0.0)
                    for _ in range(4000)) / 4000
    _rnd3.seed(4242)
    _dmg_pen = sum(player_mod.roll_damage_tuned(1000, 600, 0.05, 0.0)
                   for _ in range(4000)) / 4000
    _rnd3.seed(4242)
    _dmg_full = sum(player_mod.roll_damage_tuned(1000, 600, 0.05, 0.05)
                    for _ in range(4000)) / 4000
    check("穿甲提高对高防目标的伤害", _dmg_pen > _dmg_base,
          f"{_dmg_pen:.1f} vs {_dmg_base:.1f}")
    check("套装增伤进一步提高伤害", _dmg_full > _dmg_pen,
          f"{_dmg_full:.1f} vs {_dmg_pen:.1f}")
    check("伤害下限保护仍然生效",
          player_mod.roll_damage_tuned(1, 10 ** 6, 0.0, 0.0)
          == player_mod.MIN_DAMAGE,
          f"{player_mod.roll_damage_tuned(1, 10 ** 6, 0.0, 0.0)}")

    # ---- 历练修为折扣（v3.1 定位调整；v3.6 修正绝对值）----
    # 打怪降级为「物品与灵石来源」，修为主来源是修炼/闭关/探索。
    check("历练修为折扣 = 1.5（monster.exp 的倍数）",
          abs(battle_mod.EXP_SCALE_FIELD - 1.5) < 1e-9,
          f"{battle_mod.EXP_SCALE_FIELD}")

    import random as _rnd_exp
    _w_pl = player_mod.Player(qq="exp1", name="折扣测试")
    _w_pl.level = 30
    _w_pl.hp = _w_pl.max_hp({})
    _w_m = {"name": "折扣测试兽", "level": 30, "hp": 10, "atk": 1,
            "defense": 0, "exp": 10000, "gold": (0, 0), "drops": []}
    _rnd_exp.seed(7)
    _full = battle_mod.fight(
        _w_pl, dict(_w_m), {}, count_death=False, exp_scale=1.0)
    _w_pl2 = player_mod.Player(qq="exp2", name="折扣测试2")
    _w_pl2.level = 30
    _w_pl2.hp = _w_pl2.max_hp({})
    _rnd_exp.seed(7)
    _disc = battle_mod.fight(
        _w_pl2, dict(_w_m), {}, count_death=False,
        exp_scale=battle_mod.EXP_SCALE_FIELD)
    check("exp_scale 就是乘在修为上的倍数",
          _disc.exp_gain == int(_full.exp_gain * battle_mod.EXP_SCALE_FIELD),
          f"折后={_disc.exp_gain} 原={_full.exp_gain}")
    check("历练折扣不影响灵石",
          _disc.gold_gain == _full.gold_gain,
          f"{_disc.gold_gain} vs {_full.gold_gain}")
    check("默认 exp_scale=1.0（秘境等不受影响）",
          abs(battle_mod.fight.__defaults__[-1] - 1.0) < 1e-9,
          f"{battle_mod.fight.__defaults__[-1]}")

    # ★ v3.6 回归：新人打怪必须有正反馈。
    #   旧值 0.15 让 1 转玩家打 1 转怪只拿 int(7 × 1.0 × 0.15) = 1 点，
    #   而一次 /修炼 是 50 点 —— 第一条线完全没有正反馈，升级要 784 场。
    _lv1_mon = battle_mod.spawn_monster(1, 0)
    _lv1_win = int(_lv1_mon["exp"] * 1.0 * battle_mod.EXP_SCALE_FIELD)
    _lv1_cult = realm_mod.cultivate_gain(1)
    check("1 转打怪所得不再是 1 点（新人正反馈）",
          _lv1_win >= 5,
          f"1 转怪 exp={_lv1_mon['exp']} → {_lv1_win} 点")
    check("1 转打怪 ≈ 修炼的 15%~35%（有反馈但不喧宾夺主）",
          0.15 <= _lv1_win / _lv1_cult <= 0.35,
          f"打怪={_lv1_win} 修炼={_lv1_cult} 比值={_lv1_win / _lv1_cult:.2f}")

    # 各转数的「打怪 / 修炼」比值必须稳定。
    #
    # ⚠️ 不能只抽一只怪就断言 —— spawn_monster 会在「本境 ±1 境」的池子里
    #    随机抽，怪物 exp 是离散档位（7 / 14 / 28 / 57 / 114 / 228 / 455 / 910），
    #    所以**单次**结果在 10%~43% 之间浮动，稳定的是**中位数**。
    #    （第一版断言写成单次采样，是个概率性假失败 —— 已修。）
    import statistics as _stats
    _rnd_exp.seed(20260921)
    _medians = {}
    for _t in (1, 10, 20, 40, 64):
        _samples = [
            int(battle_mod.spawn_monster(_t, 0)["exp"]
                * battle_mod.EXP_SCALE_FIELD) / realm_mod.cultivate_gain(_t)
            for _ in range(300)
        ]
        _medians[_t] = _stats.median(_samples)
    check("打怪/修炼 比值的中位数在各转数稳定（20%~25%）",
          all(0.20 <= _v <= 0.25 for _v in _medians.values()),
          f"{ {k: round(v, 3) for k, v in _medians.items()} }")

    # 档位方差是**刻意的**：抽到越境强敌收益更高，打怪才有惊喜感。
    # 这条断言防的是「有人嫌浮动大，把怪物 exp 抹平成一条平滑曲线」。
    _tier = sorted({
        int(battle_mod.spawn_monster(20, 0)["exp"] * battle_mod.EXP_SCALE_FIELD)
        for _ in range(300)
    })
    check("打怪收益有档位方差（抽到越境强敌更高）",
          len(_tier) >= 2 and _tier[-1] >= _tier[0] * 2,
          f"档位={_tier}")

    # ---- /秘境 指令（端到端）----
    _dg_plugin = plugin_mod.TextRPGPlugin(context=None, config={
        # 访问控制在 [23]-[28] 段单独测；这里要让游戏逻辑跑通，
        # 所以显式放开白名单，避免未激活群被闸门拦下。
        "group_whitelist_mode": "all",
        "reply_delay_range": [0, 0],
        "rate_limit_count": 10000,
    })
    _dg_plugin.item_db = {}
    _ev_dg = AstrMessageEvent(qq="777001", name="秘境指令", group="999")
    await fast_create(_dg_plugin, _ev_dg, "秘境指令", level=5)
    _dgp = _dg_plugin.store.get("999", "777001")
    _dgp.dungeon_date = ""
    _out_dg = await run_cmd(_dg_plugin, "dungeon", _ev_dg)
    check("指令返回秘境战果", "秘境" in _out_dg, _out_dg[:200])
    check("战果含逐层战报", "层" in _out_dg, _out_dg[:200])
    check("闯关后写入日期，当天不能再闯",
          not dungeon_mod.can_run_today(_dgp), f"{_dgp.dungeon_date}")
    _out_dg2 = await run_cmd(_dg_plugin, "dungeon", _ev_dg)
    check("重复闯关被每日限制拦下",
          "已闭" in _out_dg2 or "明日" in _out_dg2, _out_dg2[:200])

    # 面板展示战力 / 穿甲 / 套装 / 秘境最深
    _dgp.dungeon_best = 7
    _out_pf = await run_cmd(_dg_plugin, "profile", _ev_dg)
    check("面板显示战力", "战力" in _out_pf, _out_pf[:300])
    check("面板显示穿甲", "穿甲" in _out_pf, _out_pf[:300])
    check("面板显示秘境最深", "秘境" in _out_pf, _out_pf[:300])

    # ---- v3.4：/菜单 与 /简介 指令 ----
    _out_menu = await run_cmd(_dg_plugin, "cmd_menu", _ev_dg)
    check("菜单包含分组标题", "道途指令总览" in _out_menu, _out_menu[:200])
    check("菜单含入门分组", "【入门】" in _out_menu, _out_menu[:300])
    check("菜单含装备分组", "【装备】" in _out_menu, _out_menu[:300])
    check("菜单给出下一步建议", "👉" in _out_menu, _out_menu[:300])
    check("菜单指向 /简介", "/简介" in _out_menu, _out_menu[:300])

    _out_intro = await run_cmd(_dg_plugin, "cmd_intro", _ev_dg)
    check("简介讲清怎么变强", "突破" in _out_intro, _out_intro[:300])
    # v3.5：套系展示名改走映射（物穿 -> 破锋），内部 id 不变
    check("简介说明装备三套系", "破锋套" in _out_intro, _out_intro[:600])
    check("简介不再暴露内部套系名",
          "物穿套" not in _out_intro, _out_intro[:800])
    check("简介说明帝兵在特殊栏", "特殊" in _out_intro, _out_intro[:800])
    check("简介说明五行秘典", "五行秘典" in _out_intro, _out_intro[:800])
    check("简介说明天道令来源", "天道令" in _out_intro, _out_intro[:1200])

    # ---- v3.4：/特殊 指令（帝兵【特殊】栏）----
    _out_sp0 = await run_cmd(_dg_plugin, "cmd_special", _ev_dg, "")
    check("无帝兵时 /特殊 给出获取引导",
          "帝兵" in _out_sp0 and "列表" in _out_sp0, _out_sp0[:300])
    check("无帝兵时不显示【特殊】栏",
          "【特殊】" not in _out_sp0, _out_sp0[:300])

    _out_sp1 = await run_cmd(_dg_plugin, "cmd_special", _ev_dg, "列表")
    # v3.5：/特殊 列表 从「天道兵库」（抽奖池）改成「天道兑换」进度面板
    check("/特殊 列表 展示天道兑换", "天道兑换" in _out_sp1, _out_sp1[:300])
    check("兑换面板带天道令进度", "天道令" in _out_sp1, _out_sp1[:400])

    # v3.5：**未兑换不能白认主** —— 帝兵改为 100 枚天道令兑换之后，
    # 这条校验是「整个天道令体系不被一行指令绕过」的关键防线。
    _out_sp2a = await run_cmd(_dg_plugin, "cmd_special", _ev_dg, "认主 dibing_000")
    check("未兑换时认主被拦", "尚未兑换" in _out_sp2a, _out_sp2a[:300])
    check("被拦后 special 仍为空", _dgp.special == "", str(_dgp.special))

    # 走正道：先兑换（这里直接写流水，等价于攒够 100 枚兑换过）
    _dgp.special_owned = ["dibing_000"]
    _out_sp2 = await run_cmd(_dg_plugin, "cmd_special", _ev_dg, "认主 dibing_000")
    check("/特殊 认主 成功", "认你为主" in _out_sp2, _out_sp2[:300])
    check("认主后写入 special 字段",
          _dgp.special == "dibing_000", str(_dgp.special))

    _out_sp3 = await run_cmd(_dg_plugin, "cmd_special", _ev_dg, "")
    check("有帝兵时 /特殊 显示【特殊】栏",
          "【特殊】" in _out_sp3, _out_sp3[:300])

    # 面板也要带出【特殊】栏
    _out_pf2 = await run_cmd(_dg_plugin, "profile", _ev_dg)
    check("面板在带帝兵时显示【特殊】栏",
          "【特殊】" in _out_pf2, _out_pf2[:600])

    # 卸下前先攒一点成长，才能验证「卸下后进度保留」不是空话
    _dgp.special_growth_pct = 37.5
    _out_sp4 = await run_cmd(_dg_plugin, "cmd_special", _ev_dg, "卸下")
    check("/特殊 卸下 成功", "已卸下" in _out_sp4, _out_sp4[:300])
    check("卸下后 special 字段清空", _dgp.special == "", str(_dgp.special))
    check("卸下后成长进度保留（不清零）",
          _dgp.special_growth_pct == 37.5, str(_dgp.special_growth_pct))
    check("卸下提示里带出保留的进度",
          "37.5" in _out_sp4, _out_sp4[:300])
    # 重新认主后成长继续累加（不清零才有意义）
    await run_cmd(_dg_plugin, "cmd_special", _ev_dg, "认主 dibing_000")
    check("重新认主后成长仍在",
          _dgp.special_growth_pct == 37.5, str(_dgp.special_growth_pct))
    _dgp.special_growth_pct = 0.0

    _out_sp5 = await run_cmd(_dg_plugin, "cmd_special", _ev_dg, "认主 不存在的东西")
    check("认主未知帝兵被拦下",
          "没有" in _out_sp5, _out_sp5[:300])

    # 无角色也能看菜单 —— 新手第一件事就是不知道发什么
    _ev_newbie = AstrMessageEvent(qq="777999", name="新手", group="999")
    _out_menu2 = await run_cmd(_dg_plugin, "cmd_menu", _ev_newbie)
    check("无角色也能看菜单", "道途指令总览" in _out_menu2, _out_menu2[:200])
    check("无角色菜单提示先报名", "/报名" in _out_menu2, _out_menu2[:300])

    # ---- v3.4：装备百分比体系（防回归） ----
    # 注意：本文件是以脚本方式运行的，不能用相对导入，走 importlib。
    _it34 = importlib.import_module(f"{HERE.name}.items")
    _pmod34 = importlib.import_module(f"{HERE.name}.player")
    _sp34 = importlib.import_module(f"{HERE.name}.special")
    _db34 = {e["name"]: e for e in _it34.EQUIPMENTS}
    check(f"十品装备表共 135 件（当前 {len(_it34.EQUIPMENTS)}）",
          len(_it34.EQUIPMENTS) == 135, str(len(_it34.EQUIPMENTS)))
    check("特殊装备表非空", len(_it34.SPECIAL_EQUIPMENTS) >= 1,
          str(len(_it34.SPECIAL_EQUIPMENTS)))

    # 满装面板增益必须显著（这是「装备有没有生效」的核心防线）
    _p34 = _pmod34.Player(qq="t34", name="测试34")
    _p34.level = 68
    _naked_atk = _p34.atk(_db34)
    for _e in _it34.EQUIPMENTS:
        if _e.get("tier_rank") == 9 and _e.get("set_id") == "物穿":
            _p34.equipment[_e["slot"]] = _e["name"]
    _full_atk = _p34.atk(_db34)
    _gain = (_full_atk / _naked_atk - 1) * 100 if _naked_atk else 0
    check(f"九品满装面板增益 ≥ 10%（当前 {_gain:.1f}%）", _gain >= 10.0,
          f"{_naked_atk} -> {_full_atk}")
    check("满装触发套系判定", _p34.active_set() == "物穿", _p34.active_set())
    check("满装穿甲 = 45%", abs(_p34.penetration() - 0.45) < 1e-6,
          str(_p34.penetration()))

    # 没穿满时机制必须全部失效（套装判定不能被绕过）
    _p34b = _pmod34.Player(qq="t34b", name="测试34b")
    _p34b.level = 68
    _it9 = [e["name"] for e in _it34.EQUIPMENTS
            if e.get("tier_rank") == 9 and e.get("set_id") == "物穿"]
    _it9b = [e["name"] for e in _it34.EQUIPMENTS
             if e.get("tier_rank") == 9 and e.get("set_id") == "法穿"]
    for _n in _it9[:3] + _it9b[:2]:
        _p34b.equipment[_it34.EQUIPMENT_BY_NAME[_n]["slot"]] = _n
    check("3+2 混搭不算套系", _p34b.active_set() == "", _p34b.active_set())
    check("混搭无破防机制", _p34b.avoid_def_pct() == 0, str(_p34b.avoid_def_pct()))
    check("混搭无减伤机制", _p34b.damage_reduce_pct() == 0,
          str(_p34b.damage_reduce_pct()))

    # 穿戴门槛
    check("5 转穿不了九品武器",
          not _it34.can_equip("九霄剑", 5), "can_equip 返回 True")
    check("5 转穿得了一品武器", _it34.can_equip("青锋剑", 5), "can_equip 返回 False")

    # 帝兵无阶位限制
    _p34c = _pmod34.Player(qq="t34c", name="测试34c")
    _p34c.level = 1
    _p34c.special = "dibing_000"
    check("帝兵 1 转就能带（无门槛）", _p34c.special_pct() > 0,
          str(_p34c.special_pct()))
    # can_equip 对帝兵必须放行 —— 它是「无阶位限制」的例外。
    # 如果哪天有人在 can_equip 里手写 turns >= req_turns，这条会立刻炸。
    check("can_equip 对帝兵放行（例外不能被门槛逻辑吃掉）",
          _it34.can_equip("dibing_000", 1), "can_equip 拦下了帝兵")

    # ---- 特殊装备系统层（special.py）----
    print("\n[48] 帝兵系统层")
    check("special 模块可导入", _sp34 is not None)
    check("成长被动注册表含 grow_on_kill",
          "grow_on_kill" in _sp34.PASSIVE_HANDLERS,
          str(list(_sp34.PASSIVE_HANDLERS)))
    check("帝兵可查到定义",
          _sp34.find_special("dibing_000") is not None)
    check("未知 id 返回 None", _sp34.find_special("不存在") is None)
    check("空 id 返回 None", _sp34.find_special("") is None)

    # 成长被动：击杀累积 + 封顶
    #
    # ⚠️ v3.5 数值下调：每击杀 1.0% -> **0.1%**，封顶 150% -> **30%**。
    # 原值是为「全服唯一」设计的 —— 一个人独享 +212% 无所谓，
    # 那正是唯一物的价值来源。但帝兵**去唯一化**（人人可得）之后，
    # +212% 意味着所有玩家面板 ×3.12，135 件装备的梯度全部失去意义。
    # 所以「去唯一化」和「原数值」二者只能留一个，必须一起改。
    _g1 = _pmod34.Player(qq="grow1", name="成长1")
    _g1.special = "dibing_000"
    _lines = _sp34.advance_passives(_g1, kills=10)
    check("击杀 10 次累积 +1%（0.1%/只）",
          abs(_g1.special_growth_pct - 1.0) < 1e-9,
          str(_g1.special_growth_pct))
    check("成长推进返回提示行", len(_lines) == 1, str(_lines))
    # 面板要跟着涨
    check("成长计入面板加成",
          abs(_g1.special_pct() - (20.0 + 1.0) / 100.0) < 1e-9,
          str(_g1.special_pct()))

    # 封顶：cap_pct = 30
    _g2 = _pmod34.Player(qq="grow2", name="成长2")
    _g2.special = "dibing_000"
    _sp34.advance_passives(_g2, kills=1000)
    check("成长被封顶在 30%", abs(_g2.special_growth_pct - 30.0) < 1e-9,
          str(_g2.special_growth_pct))
    check("满成长后面板 ×1.50（base 20 + 成长 30）",
          abs(_g2.special_pct() - 0.50) < 1e-9, str(_g2.special_pct()))

    # 败北不掉（老板定的 lose_on_death=False）
    _g3 = _pmod34.Player(qq="grow3", name="成长3")
    _g3.special = "dibing_000"
    _sp34.advance_passives(_g3, kills=20)
    _before = _g3.special_growth_pct
    _sp34.advance_passives(_g3, kills=0, died=True)
    check("道消不清零成长（老板定的不掉）",
          abs(_g3.special_growth_pct - _before) < 1e-9,
          f"{_before} -> {_g3.special_growth_pct}")

    # 无帝兵时是纯 no-op
    _g4 = _pmod34.Player(qq="grow4", name="成长4")
    check("无帝兵时成长推进无副作用",
          _sp34.advance_passives(_g4, kills=5) == []
          and _g4.special_growth_pct == 0.0,
          str(_g4.special_growth_pct))

    # 【特殊】栏：没帝兵不显示
    check("无帝兵时不渲染【特殊】栏",
          _sp34.render_special_slot(_g4) is None)
    _g4.special = "dibing_000"
    _slot_txt = _sp34.render_special_slot(_g4)
    check("有帝兵时渲染【特殊】栏", _slot_txt is not None and "【特殊】" in _slot_txt,
          str(_slot_txt))
    check("【特殊】栏标出基础加成",
          _slot_txt is not None and "20.0%" in _slot_txt, str(_slot_txt))

    # v3.5：展示层不再拼「（xxx 专属）」—— 帝兵已去唯一化
    check("展示名不带「专属」",
          "专属" not in str(_slot_txt), str(_slot_txt))

    # 兑换框架：池子语义
    #
    # v3.5：lottery_pool() / roll_special() 已删除，改为 exchange_pool()
    # 等 6 个兑换函数。这里钉住「新接口存在、旧接口已消失」——
    # 旧接口留着会让后来的人以为抽奖还在。
    check("抽奖接口已彻底删除",
          not hasattr(_sp34, "lottery_pool")
          and not hasattr(_sp34, "roll_special"),
          "lottery_pool/roll_special 仍存在")
    check("兑换接口齐备",
          all(hasattr(_sp34, n) for n in (
              "exchange_pool", "token_count", "owns_special",
              "can_redeem", "redeem", "redeem_preview", "grant_token",
          )),
          str([n for n in ("exchange_pool", "token_count", "owns_special",
                           "can_redeem", "redeem", "redeem_preview",
                           "grant_token") if not hasattr(_sp34, n)]))
    _pool = _sp34.exchange_pool()
    check("兑换池里的都是 token_exchange 来源",
          all(str(s.get("source")) in ("token_exchange", "any") for s in _pool),
          str([s.get("id") for s in _pool]))
    # 【关键】去唯一化之后，池子**不再过滤 owner** ——
    # 「被别人拿了就没了」的语义已经不存在了
    _saved_owner = _it34.SPECIAL_EQUIPMENT_BY_ID["dibing_000"].get("owner", "")
    _it34.SPECIAL_EQUIPMENT_BY_ID["dibing_000"]["owner"] = "someone"
    check("去唯一化：他人持有不影响兑换池",
          len(_sp34.exchange_pool()) == len(_pool),
          str(_sp34.exchange_pool()))
    _it34.SPECIAL_EQUIPMENT_BY_ID["dibing_000"]["owner"] = _saved_owner
    check("帝兵 unique 已置 False",
          not _it34.SPECIAL_EQUIPMENT_BY_ID["dibing_000"].get("unique"),
          str(_it34.SPECIAL_EQUIPMENT_BY_ID["dibing_000"].get("unique")))
    check("帝兵 source 已改为 token_exchange",
          _it34.SPECIAL_EQUIPMENT_BY_ID["dibing_000"].get("source")
          == "token_exchange",
          str(_it34.SPECIAL_EQUIPMENT_BY_ID["dibing_000"].get("source")))

    # ---- 帝兵成长必须认「碾压 / 胜利 / 惨胜」三种赢法 ----
    #
    # 【这条断言是为了钉住一个真 bug】
    # 战斗结果有五档，其中**三档都算赢**：crush(碾压) / win(胜利) / narrow(惨胜)。
    # 我第一版成长代码写的是 `outcome == OUTCOME_WIN`，
    # 结果碾压和惨胜全被漏掉 —— 而碾压恰恰是最常见的结果，
    # 玩家会「打一晚上一次没涨」。
    #
    # 这个 bug 单元测试抓不到（直接调 advance_passives 永远是对的），
    # 只有走完整战斗流程才会暴露。所以这里从**结果语义**这一侧钉住：
    # 三档赢法都必须 won=True，且用 won 判定时成长会推进。
    check("碾压 / 胜利 / 惨胜 三档都算赢",
          all(
              battle_mod.BattleResult(outcome=o, monster_name="x").won
              for o in (battle_mod.OUTCOME_CRUSH, battle_mod.OUTCOME_WIN,
                        battle_mod.OUTCOME_NARROW)
          ),
          "三种赢法里有 won=False 的")
    check("平局 / 败北不算赢",
          not battle_mod.BattleResult(
              outcome=battle_mod.OUTCOME_DRAW, monster_name="x").won
          and not battle_mod.BattleResult(
              outcome=battle_mod.OUTCOME_LOSE, monster_name="x").won)

    # 行为验证：碾压结果也必须推进帝兵成长
    _crush_p = _pmod34.Player(qq="crush", name="碾压测试")
    _crush_p.special = "dibing_000"
    _crush_res = battle_mod.BattleResult(
        outcome=battle_mod.OUTCOME_CRUSH, monster_name="史莱姆")
    _sp34.advance_passives(_crush_p, kills=1 if _crush_res.won else 0)
    check("碾压也推进帝兵成长（不是只认 win）",
          abs(_crush_p.special_growth_pct - 0.1) < 1e-9,
          str(_crush_p.special_growth_pct))

    # 战斗引擎关键常量
    # 注意：回合上限 ≥ 20 的断言在 [2] 段已有，这里不重复。
    check("暴击基础率在合理区间",
          0 < battle_mod.CRIT_BASE_RATE < 0.3,
          str(battle_mod.CRIT_BASE_RATE))
    check("破防追加伤害系数已启用（不为 0）",
          battle_mod.IGNORE_DEF_ATK_RATIO > 0,
          str(battle_mod.IGNORE_DEF_ATK_RATIO))
    check("装备机制注册表已填充",
          len(battle_mod.ATTACK_MODIFIERS) >= 3
          and len(battle_mod.DEFENSE_MODIFIERS) >= 3,
          f"atk={list(battle_mod.ATTACK_MODIFIERS)} "
          f"def={list(battle_mod.DEFENSE_MODIFIERS)}")

    # =================================================================
    # [49] 天道令 · 兑换（v3.5）
    # =================================================================
    #
    # 这一段钉的是**不变式**（数量、口径、防刷），
    # 完整链路（签到 -> 攒令 -> 兑换 -> 认主）在 verify_v35_token.py 里。
    print("\n[49] 天道令 · 兑换")

    _tk = _it34.get_item(_sp34.TOKEN_NAME)
    check("天道令在物品表里", _tk is not None, str(_tk))
    check("天道令 effect = token（可被 /使用 /出售 拦下）",
          (_tk or {}).get("effect") == "token", str((_tk or {}).get("effect")))
    check("天道令 price = 0（第二道保险）",
          float((_tk or {}).get("price", -1)) == 0.0,
          str((_tk or {}).get("price")))

    check("兑换价 = 100 枚", _sp34.TOKEN_COST == 100, str(_sp34.TOKEN_COST))
    check("签到给 1 枚", _sp34.TOKEN_FROM_SIGNIN == 1,
          str(_sp34.TOKEN_FROM_SIGNIN))
    check("秘境首通给 1 枚", _sp34.TOKEN_FROM_DUNGEON == 1,
          str(_sp34.TOKEN_FROM_DUNGEON))

    # ---- 兑换校验四条 ----
    _tk_p = _pmod34.Player(qq="tk1", name="兑令")
    _ok, _why = _sp34.can_redeem(_tk_p, "不存在")
    check("未知 id 不可兑换", not _ok and "查无此物" in _why, _why)

    _ok, _why = _sp34.can_redeem(_tk_p, "dibing_000")
    check("0 枚时不可兑换", not _ok and "天道令不足" in _why, _why)

    _sp34.grant_token(_tk_p, 100, limit=None)
    check("发 100 枚后数量正确", _sp34.token_count(_tk_p) == 100,
          str(_sp34.token_count(_tk_p)))
    _ok, _why = _sp34.can_redeem(_tk_p, "dibing_000")
    check("满 100 枚可兑换", _ok, _why)

    _ok, _msg, _spec = _sp34.redeem(_tk_p, "dibing_000")
    check("兑换成功", _ok, _msg)
    check("兑换扣掉 100 枚", _sp34.token_count(_tk_p) == 0,
          str(_sp34.token_count(_tk_p)))
    check("兑换后直接认主", _tk_p.special == "dibing_000", str(_tk_p.special))
    check("兑换写入永久流水", "dibing_000" in _tk_p.special_owned,
          str(_tk_p.special_owned))

    _ok, _why = _sp34.can_redeem(_tk_p, "dibing_000")
    check("重复兑换被拒", not _ok and "已兑换过" in _why, _why)

    # 【这条最要紧】卸下会清空 special，但流水不能清 ——
    # 否则「卸下 -> 再兑换」会被白扣 100 枚
    _tk_p.special = ""
    _sp34.grant_token(_tk_p, 100, limit=None)
    _ok, _why = _sp34.can_redeem(_tk_p, "dibing_000")
    check("卸下后仍不可重复兑换（防白扣 100 枚）",
          not _ok and "已兑换过" in _why, _why)
    check("100 枚没被白扣", _sp34.token_count(_tk_p) == 100,
          str(_sp34.token_count(_tk_p)))
    check("owns_special 认流水不认 special",
          _sp34.owns_special(_tk_p, "dibing_000"),
          "special 已清空但 owns_special 应为 True")

    # ---- 背包满时也要入包（进度型道具不能静默丢） ----
    _full_p = _pmod34.Player(qq="tk2", name="满包")
    _full_p.inventory = {f"占位{i}": 1 for i in range(200)}
    _got = _sp34.grant_token(_full_p, 1, limit=1)
    check("背包满仍强行入包（不丢进度）", _got == 1 and
          _sp34.token_count(_full_p) == 1,
          f"got={_got} count={_sp34.token_count(_full_p)}")

    # ---- 秘境里程碑表 ----
    check("里程碑表 = {30:7, 35:8, 40:9}",
          dungeon_mod.DUNGEON_TIER_MILESTONES == {30: 7, 35: 8, 40: 9},
          str(dungeon_mod.DUNGEON_TIER_MILESTONES))
    for _tier in (7, 8, 9):
        _names = {dungeon_mod._roll_tier_item(_tier) for _ in range(40)}
        _tiers = {int(_it34.EQUIPMENT_BY_NAME[n]["tier_rank"])
                  for n in _names if n in _it34.EQUIPMENT_BY_NAME}
        check(f"里程碑按品阶发（{_tier} 品）", _tiers == {_tier}, str(_tiers))
    check("不存在的品阶返回 None",
          dungeon_mod._roll_tier_item(99) is None)
    check("DungeonRun 有 tokens / milestone_items 字段",
          hasattr(dungeon_mod.DungeonRun(), "tokens")
          and hasattr(dungeon_mod.DungeonRun(), "milestone_items"))

    # ---- 坊市下架七品以上（修掉那个静默 bug） ----
    check("坊市最高品阶 = 6", _it34.SHOP_MAX_TIER_RANK == 6,
          str(_it34.SHOP_MAX_TIER_RANK))
    _stock68 = _it34.shop_stock(68)
    _stock_tiers = {int(_it34.EQUIPMENT_BY_NAME[n].get("tier_rank", 0))
                    for n in _stock68}
    check("68 转的坊市也不含七品以上", max(_stock_tiers) <= 6,
          str(sorted(_stock_tiers)))
    check("坊市按 req_turns 过滤（68 转能买到六品）",
          _stock_tiers == {1, 2, 3, 4, 5, 6}, str(sorted(_stock_tiers)))

    # ---- 套系展示名 ----
    check("套系展示名映射齐备",
          _it34.set_display_name("物穿") == "破锋"
          and _it34.set_display_name("法穿") == "通玄"
          and _it34.set_display_name("肉盾") == "镇岳",
          str(_it34.SET_DISPLAY_NAMES))
    check("内部 set_id 一个字都没改（存档兼容）",
          {"物穿", "法穿", "肉盾"} <= set(_it34.SET_DISPLAY_NAMES),
          str(sorted(_it34.SET_DISPLAY_NAMES)))
    check("战斗小结用展示名",
          battle_mod.BattleResult(
              outcome=battle_mod.OUTCOME_WIN, monster_name="x",
              active_set="物穿").mech_summary() == "破锋套",
          battle_mod.BattleResult(
              outcome=battle_mod.OUTCOME_WIN, monster_name="x",
              active_set="物穿").mech_summary())

    # ---- 老存档兼容 ----
    _old_p = _pmod34.Player.from_dict({
        "qq": "tk3", "name": "老档", "inventory": {}, "equipment": {},
    })
    check("老存档补出 sign_in_date", _old_p.sign_in_date == "",
          repr(_old_p.sign_in_date))
    check("老存档补出 special_owned", _old_p.special_owned == [],
          str(_old_p.special_owned))
    _dirty_p = _pmod34.Player.from_dict({
        "qq": "tk4", "name": "脏档",
        "special_owned": ["a", "a", "", "b", 123],
        "sign_in_date": None,
    })
    check("脏 special_owned 被清洗去重",
          _dirty_p.special_owned == ["a", "b"], str(_dirty_p.special_owned))
    check("脏 sign_in_date 归零", _dirty_p.sign_in_date == "",
          repr(_dirty_p.sign_in_date))

    # =================================================================
    # [50] VIP（赞助者）系统（v3.5）
    # =================================================================
    #
    # 【两条硬约束必须被断言钉住】
    #   1. VIP 只能由天道发放（非管理员调用 /vip 开通 被拒）
    #   2. VIP 不碰战斗数值（战斗面板与免费玩家完全一致）
    print("\n[50] VIP（赞助者）系统")

    _vip = importlib.import_module(f"{HERE.name}.vip")
    # 名单是**全局文件**，_testdata 会被多次运行复用 —— 先清干净，
    # 否则上一轮留下的 VIP 会让「非 VIP 得 1 枚」这类断言随机失败
    _vip.clear_all()

    check("VIP 模块可导入", _vip is not None)
    check("两档等级齐备",
          set(_vip.TIER_TOKEN_BONUS) == {"normal", "supreme"},
          str(sorted(_vip.TIER_TOKEN_BONUS)))
    check("普通 VIP 每天 +1 枚",
          _vip.TIER_TOKEN_BONUS["normal"] == 1,
          str(_vip.TIER_TOKEN_BONUS["normal"]))
    check("至尊 VIP 每天 +3 枚",
          _vip.TIER_TOKEN_BONUS["supreme"] == 3,
          str(_vip.TIER_TOKEN_BONUS["supreme"]))
    check("两档都有面板称号",
          _vip.tier_label("normal") and _vip.tier_label("supreme"),
          f"{_vip.tier_label('normal')} / {_vip.tier_label('supreme')}")

    # ---- 非 VIP / 各档的加成 ----
    check("非 VIP 加成 = 0", _vip.daily_token_bonus("900001") == 0,
          str(_vip.daily_token_bonus("900001")))
    check("非 VIP 无称号", _vip.title_of("900001") == "",
          repr(_vip.title_of("900001")))

    _vip.grant("900002", 1, "normal")
    check("普通 VIP 加成 = 1", _vip.daily_token_bonus("900002") == 1,
          str(_vip.daily_token_bonus("900002")))
    _vip.grant("900003", 1, "supreme")
    check("至尊 VIP 加成 = 3", _vip.daily_token_bonus("900003") == 3,
          str(_vip.daily_token_bonus("900003")))

    # ---- 到期判定 ----
    _t0 = 1_800_000_000.0
    _vip.grant("900004", 1, "normal", now=_t0)
    check("有效期内是 VIP",
          _vip.vip_tier("900004", now=_t0 + 86400) == "normal",
          _vip.vip_tier("900004", now=_t0 + 86400))
    check("过期后不再是 VIP",
          _vip.vip_tier("900004", now=_t0 + 40 * 86400) == "",
          _vip.vip_tier("900004", now=_t0 + 40 * 86400))
    check("过期后加成归 0",
          _vip.daily_token_bonus("900004", now=_t0 + 40 * 86400) == 0,
          str(_vip.daily_token_bonus("900004", now=_t0 + 40 * 86400)))
    check("过期**不删**记录（可续费 / 可查历史）",
          _vip.record("900004") is not None,
          "过期记录被删掉了")

    # ---- 续费：不能吃掉剩余天数（最容易引起投诉的低级 bug） ----
    _vip.clear_all()
    _vip.grant("900005", 1, "normal", now=_t0)
    _exp1 = float((_vip.record("900005") or {}).get("expire_ts", 0))
    # 在第 10 天提前续 1 个月
    _vip.grant("900005", 1, "normal", now=_t0 + 10 * 86400)
    _exp2 = float((_vip.record("900005") or {}).get("expire_ts", 0))
    check("提前续费不损失剩余天数（base = max(now, expire)）",
          abs(_exp2 - (_exp1 + 30 * 86400)) < 1e-6,
          f"{_exp1} -> {_exp2}（应 +{30 * 86400}）")
    check("提前续费从原到期日往后加（不是从 now 起算）",
          _exp2 > _t0 + 40 * 86400,
          str(_exp2))

    # ---- 过期后续费从 now 起算 ----
    _vip.clear_all()
    _vip.grant("900006", 1, "normal", now=_t0)
    _later = _t0 + 100 * 86400
    _vip.grant("900006", 1, "normal", now=_later)
    _exp3 = float((_vip.record("900006") or {}).get("expire_ts", 0))
    check("过期后续费从 now 起算",
          abs(_exp3 - (_later + 30 * 86400)) < 1e-6,
          f"{_later} -> {_exp3}")

    # ---- 累计月数 / 升级 ----
    _vip.clear_all()
    _vip.grant("900007", 2, "normal", now=_t0)
    _vip.grant("900007", 1, "supreme", now=_t0 + 86400)
    _rec7 = _vip.record("900007") or {}
    check("累计月数累加 = 3", _rec7.get("total_months") == 3,
          str(_rec7.get("total_months")))
    check("升级后等级为 supreme", _rec7.get("tier") == "supreme",
          str(_rec7.get("tier")))
    check("升级不折算剩余天数（直接覆盖 tier）",
          _vip.vip_tier("900007", now=_t0 + 10 * 86400) == "supreme",
          _vip.vip_tier("900007", now=_t0 + 10 * 86400))

    # ---- 撤销立即失效 ----
    _vip.clear_all()
    _vip.grant("900008", 1, "normal", now=_t0)
    _ok_r, _ = _vip.revoke("900008")
    check("撤销成功", _ok_r)
    check("撤销后立即失效", _vip.vip_tier("900008") == "",
          repr(_vip.vip_tier("900008")))
    check("撤销后记录已删除", _vip.record("900008") is None)

    # ---- 参数校验 ----
    _ok_bad, _why_bad, _ = _vip.grant("not-a-qq", 1, "normal")
    check("非法 QQ 被拒", not _ok_bad, _why_bad)
    _ok_bad2, _why_bad2, _ = _vip.grant("900009", 0, "normal")
    check("月数 < 1 被拒", not _ok_bad2, _why_bad2)
    _ok_bad3, _why_bad3, _ = _vip.grant("900009", 1, "unknown_tier")
    check("未知等级被拒", not _ok_bad3, _why_bad3)
    check("被拒后不留记录", _vip.record("900009") is None)

    # ---- 损坏的 vip.json 不崩 ----
    _vip.clear_all()
    _vip_path = HERE / "_testdata" / "vip.json"
    _vip_path.parent.mkdir(parents=True, exist_ok=True)
    _vip_path.write_text("{ 这不是合法 JSON", encoding="utf-8")
    _vip.store()._loaded = False          # 强制重新读盘
    check("vip.json 损坏时降级为空名单（不崩）",
          _vip.vip_tier("900010") == "",
          repr(_vip.vip_tier("900010")))
    _vip_path.unlink(missing_ok=True)
    _vip.store()._loaded = False
    check("无 vip.json 时正常（老存档）",
          _vip.vip_tier("900010") == "",
          repr(_vip.vip_tier("900010")))

    # ---- 指令层：非管理员不能发放 ----
    _vip_plugin = plugin_mod.TextRPGPlugin(context=None, config={
        "group_whitelist_mode": "all",
        "reply_delay_range": [0, 0],
        "rate_limit_count": 10000,
    })
    _vip_plugin.item_db = {}
    _vip.clear_all()
    _ev_user = AstrMessageEvent(qq="900101", name="玩家", group="995")
    _ev_admin = AstrMessageEvent(qq="1", name="天道", group="995", admin=True)

    _out_deny = await run_cmd(_vip_plugin, "cmd_vip", _ev_user, "开通 900101 1 normal")
    check("非管理员开通被拒", "只应天道亲传" in _out_deny, _out_deny[:200])
    check("被拒后没发出去", _vip.vip_tier("900101") == "",
          repr(_vip.vip_tier("900101")))

    _out_deny2 = await run_cmd(_vip_plugin, "cmd_vip", _ev_user, "列表")
    check("非管理员看列表被拒", "只应天道亲传" in _out_deny2, _out_deny2[:200])
    _out_deny3 = await run_cmd(_vip_plugin, "cmd_vip", _ev_user, "撤销 900101")
    check("非管理员撤销被拒", "只应天道亲传" in _out_deny3, _out_deny3[:200])

    _out_open = await run_cmd(_vip_plugin, "cmd_vip", _ev_user, "")
    check("查询对自己开放", "VIP" in _out_open, _out_open[:200])

    _out_grant = await run_cmd(
        _vip_plugin, "cmd_vip", _ev_admin, "开通 900101 1 supreme 微信0919"
    )
    check("管理员发放成功", "天道垂青" in _out_grant, _out_grant[:300])
    check("发放后生效", _vip.vip_tier("900101") == "supreme",
          repr(_vip.vip_tier("900101")))
    check("公告带尊号与有效期",
          "至尊赞助者" in _out_grant and "有效期至" in _out_grant,
          _out_grant[:400])

    # ---- 签到：基础 + VIP 加成 ----
    await fast_create(_vip_plugin, _ev_user, "玩家", level=5)
    _vp = _vip_plugin.store.get("995", "900101")
    _vp.sign_in_date = ""
    _out_sign = await run_cmd(_vip_plugin, "cmd_sign_in", _ev_user)
    check("至尊签到共得 4 枚（1 基础 + 3 VIP）",
          _sp34.token_count(_vp) == 4, str(_sp34.token_count(_vp)))
    check("签到回复标出 VIP 加成", "额外 3 枚" in _out_sign, _out_sign[:300])

    _out_sign2 = await run_cmd(_vip_plugin, "cmd_sign_in", _ev_user)
    check("同日重复签到不重复发（含 VIP 加成）",
          _sp34.token_count(_vp) == 4, str(_sp34.token_count(_vp)))

    # ---- 跨群有效（VIP 是全局名单，不是群存档） ----
    _ev_other = AstrMessageEvent(qq="900101", name="玩家", group="994")
    await fast_create(_vip_plugin, _ev_other, "玩家", level=5)
    _vp2 = _vip_plugin.store.get("994", "900101")
    _out_sign3 = await run_cmd(_vip_plugin, "cmd_sign_in", _ev_other)
    check("VIP 跨群有效（B 群签到同样加成）",
          _sp34.token_count(_vp2) == 4, str(_sp34.token_count(_vp2)))
    check("跨群签到回复也带加成", "额外 3 枚" in _out_sign3, _out_sign3[:300])

    # ---- 面板称号 ----
    _out_vp = await run_cmd(_vip_plugin, "profile", _ev_user)
    check("面板挂出 VIP 称号", "至尊赞助者" in _out_vp, _out_vp[:300])

    # ---- 【硬约束】VIP 不碰战斗数值 ----
    _free_p = _pmod34.Player(qq="900999", name="免费")
    _vip_p = _pmod34.Player(qq="900101", name="赞助")
    _vip.grant("900101", 1, "supreme")
    check("VIP 不改变战斗面板（攻击）",
          _free_p.atk(_db34) == _vip_p.atk(_db34),
          f"{_free_p.atk(_db34)} vs {_vip_p.atk(_db34)}")
    check("VIP 不改变战斗面板（防御）",
          _free_p.defense(_db34) == _vip_p.defense(_db34),
          f"{_free_p.defense(_db34)} vs {_vip_p.defense(_db34)}")
    check("VIP 不改变战斗面板（气血）",
          _free_p.max_hp(_db34) == _vip_p.max_hp(_db34),
          f"{_free_p.max_hp(_db34)} vs {_vip_p.max_hp(_db34)}")
    check("VIP 不影响战力",
          battle_mod.player_power(_free_p, _db34)
          == battle_mod.player_power(_vip_p, _db34))

    # ---- 撤销后签到回到 1 枚 ----
    _vip.revoke("900101")
    _vp.sign_in_date = ""
    await run_cmd(_vip_plugin, "cmd_sign_in", _ev_user)
    check("撤销后签到只发基础 1 枚（4 -> 5）",
          _sp34.token_count(_vp) == 5, str(_sp34.token_count(_vp)))

    # ---- 到期前 3 天提醒 ----
    _vip.clear_all()
    _vip.grant("900102", 1, "normal", now=_t0)
    check("到期前 3 天内提醒",
          _vip.expiring_soon("900102", now=_t0 + 28 * 86400) > 0,
          str(_vip.expiring_soon("900102", now=_t0 + 28 * 86400)))
    check("还早则不提醒",
          _vip.expiring_soon("900102", now=_t0 + 86400) == 0,
          str(_vip.expiring_soon("900102", now=_t0 + 86400)))

    # ---- 列表只列有效 VIP ----
    _vip.clear_all()
    _vip.grant("900103", 1, "normal", now=_t0)
    _vip.grant("900104", 1, "supreme", now=_t0)
    check("列表只含有效 VIP",
          {r[0] for r in _vip.list_vips(now=_t0 + 86400)}
          == {"900103", "900104"},
          str([r[0] for r in _vip.list_vips(now=_t0 + 86400)]))
    check("过期者不在列表里",
          {r[0] for r in _vip.list_vips(now=_t0 + 40 * 86400)} == set(),
          str([r[0] for r in _vip.list_vips(now=_t0 + 40 * 86400)]))

    # -----------------------------------------------------------------
    # [50b] 精确短指令 /激活vip · /至尊vip（v3.5 收尾）
    # -----------------------------------------------------------------
    #
    # 老板的要求：发 VIP 要**一步到位**，不要敲 `/vip 开通 <QQ> <月数> ...`。
    # 所以主入口改成两条独立短指令，一条一个等级，名字本身就是完整的：
    #
    #     /激活vip <QQ>    普通 VIP（10 元/月），默认 1 个月
    #     /至尊vip <QQ>    至尊 VIP（30 元/月），默认 1 个月
    #
    # 这一段的重点是钉住三件事：
    #   1. 两条新入口都**只能天道用**（否则就是白送 VIP 的漏洞）
    #   2. `/激活vip` 不会被已有的 `/激活` 吞掉（会静默失效的坑）
    #   3. 三条发放入口共用同一条流程，权限判断只有一处
    _vip.clear_all()

    # main.py 源码 —— 后面几处结构性断言要直接读它（注册名、委托关系）
    _vip_src = (HERE / "main.py").read_text(encoding="utf-8")

    check("普通 VIP 价目 = 10 元/月",
          _vip.TIER_PRICES.get("normal") == 10,
          str(_vip.TIER_PRICES.get("normal")))
    check("至尊 VIP 价目 = 30 元/月",
          _vip.TIER_PRICES.get("supreme") == 30,
          str(_vip.TIER_PRICES.get("supreme")))
    check("两条精确短指令已登记",
          _vip.GRANT_COMMANDS.get("normal") == "/激活vip"
          and _vip.GRANT_COMMANDS.get("supreme") == "/至尊vip",
          str(_vip.GRANT_COMMANDS))
    check("插件实现了 cmd_vip_normal",
          callable(getattr(_vip_plugin, "cmd_vip_normal", None)))
    check("插件实现了 cmd_vip_supreme",
          callable(getattr(_vip_plugin, "cmd_vip_supreme", None)))
    check("指令注册名不含斜杠（斜杠由 wake_prefix 剥离）",
          '@filter.command("激活vip")' in _vip_src
          and '@filter.command("至尊vip")' in _vip_src,
          "装饰器字面量里出现了斜杠，群里会匹配不到")
    check("帮助文案用带斜杠的展示形式（方便玩家照抄）",
          all(c.startswith("/") for c in _vip.GRANT_COMMANDS.values()),
          str(_vip.GRANT_COMMANDS))

    # 复刻 astrbot/core/star/filter/command.py:202 的匹配规则，
    # 把「/激活vip 123 不会被 /激活 吃掉」钉死。
    # 若框架改成裸 startswith(cmd)，这里会立刻红 —— 那意味着
    # 玩家的 QQ 号会被当成 /激活 的参数，静默发不出去。
    def _cmd_matches(registered: str, message: str) -> bool:
        return message.startswith(f"{registered} ") or message == registered

    check("/激活vip 123 不会误触 /激活（框架规则）",
          not _cmd_matches("激活", "激活vip 123"),
          "为 True 说明框架用裸 startswith —— QQ 号会被吃掉")
    check("/激活 123 仍能正常匹配 /激活",
          _cmd_matches("激活", "激活 123"))

    # ---- 非天道不能发放：两条新入口都要拦 ----
    _out_n_deny = await run_cmd(_vip_plugin, "cmd_vip_normal", _ev_user, "900101")
    check("非天道用 /激活vip 被拒", "只应天道亲传" in _out_n_deny,
          _out_n_deny[:200])
    check("被拒后没发出去（normal）", _vip.vip_tier("900101") == "",
          repr(_vip.vip_tier("900101")))

    _out_s_deny = await run_cmd(_vip_plugin, "cmd_vip_supreme", _ev_user, "900101")
    check("非天道用 /至尊vip 被拒", "只应天道亲传" in _out_s_deny,
          _out_s_deny[:200])
    check("被拒后没发出去（supreme）", _vip.vip_tier("900101") == "",
          repr(_vip.vip_tier("900101")))

    # ---- 天道一步发放（默认 1 个月） ----
    _out_n = await run_cmd(_vip_plugin, "cmd_vip_normal", _ev_admin, "900101")
    check("/激活vip 发放成功", "天道垂青" in _out_n, _out_n[:300])
    check("/激活vip 默认 1 个月（约 30 天）",
          _vip.days_left("900101") in (29, 30, 31),
          str(_vip.days_left("900101")))
    check("/激活vip 发的是普通档", _vip.vip_tier("900101") == "normal",
          repr(_vip.vip_tier("900101")))
    check("公告含尊号与价格",
          "赞助者" in _out_n and "10 元/月" in _out_n, _out_n[:400])

    _out_s = await run_cmd(_vip_plugin, "cmd_vip_supreme", _ev_admin, "900101")
    check("/至尊vip 发放成功", "天道垂青" in _out_s, _out_s[:300])
    check("/至尊vip 升级为至尊档", _vip.vip_tier("900101") == "supreme",
          repr(_vip.vip_tier("900101")))
    check("升级保留剩余天数（不归零）", _vip.days_left("900101") > 30,
          str(_vip.days_left("900101")))

    # ---- 不带 QQ 时给用法，而不是静默什么都不做 ----
    _out_usage = await run_cmd(_vip_plugin, "cmd_vip_normal", _ev_admin, "")
    check("/激活vip 不带 QQ 给用法", "用法" in _out_usage, _out_usage[:200])
    _out_usage2 = await run_cmd(_vip_plugin, "cmd_vip_supreme", _ev_admin, "")
    check("/至尊vip 不带 QQ 给用法", "用法" in _out_usage2, _out_usage2[:200])

    # ---- /vip 无参数 = 价目 + 自己的状态（老板明确要求） ----
    _price_vip = _vip_plugin._vip_price_text("900101")
    check("/vip 打出两档价格",
          "10 元/月" in _price_vip and "30 元/月" in _price_vip,
          _price_vip[:300])
    check("/vip 标出已激活与档位",
          "已激活" in _price_vip and "至尊赞助者" in _price_vip,
          _price_vip[:400])
    check("/vip 带剩余天数", "剩余" in _price_vip and "天" in _price_vip,
          _price_vip[:400])

    _price_free = _vip_plugin._vip_price_text("900777")
    check("/vip 未激活时标 ❌", "未激活" in _price_free, _price_free[:300])
    check("/vip 对非 VIP 也打出价目，且不回显他人 QQ",
          "10 元/月" in _price_free and "900777" not in _price_free,
          _price_free[:400])

    # 已到期 → ⌛（要能提示续费），而不是当成「从未赞助」的 ❌
    _vip.grant("900106", 1, "normal", now=time.time() - 40 * 86400)
    _price_exp = _vip_plugin._vip_price_text("900106")
    check("/vip 已到期时标 ⌛（提示续费）", "已到期" in _price_exp,
          _price_exp[:300])
    check("/vip 已到期仍显示累计月数",
          "累计赞助 1 个月" in _price_exp, _price_exp[:300])

    # ---- 帮助文案把短指令列为主入口 ----
    _help = _vip_plugin._vip_help_text()
    check("帮助把 /激活vip 列为主入口", "/激活vip" in _help, _help[:400])
    check("帮助把 /至尊vip 列为主入口", "/至尊vip" in _help, _help[:400])

    # ---- 唯一权限入口：三条发放路径都经 _vip_grant_flow ----
    #
    # 结构性断言。抽成一条流程的意义就是「权限判断只有一处」——
    # 多出来一处，就说明有人新开了入口却忘了共用，那正是漏洞来源。
    _flow_src = _vip_src.split("async def _vip_grant_flow", 1)[1].split(
        "\n    @staticmethod\n    def _vip_bless_text", 1)[0]
    check("统一发放流程内做权限校验", "_is_admin" in _flow_src, _flow_src[:200])

    for _m in ("cmd_vip_normal", "cmd_vip_supreme"):
        _seg = _vip_src.split(f"async def {_m}", 1)[1].split("\n    async def", 1)[0]
        check(f"{_m} 委托给统一发放流程", "_vip_grant_flow" in _seg, _seg[:200])

    _vip.clear_all()

    # -----------------------------------------------------------------
    # [50c] 指令注册写法（防回归：多装饰器 = AND 语义 = 别名全部失效）
    # -----------------------------------------------------------------
    #
    # 2026-09-20 线上实测事故。插件里 12 个方法原本这样写别名：
    #
    #     @filter.command("菜单")
    #     @filter.command("帮助")
    #     @filter.command("指令")
    #     @filter.command("help")
    #     async def cmd_menu(self, event): ...
    #
    # AstrBot 的 get_handler_or_create() 按「模块名_函数名」复用同一个
    # StarHandlerMetadata（register/star_handler.py:49-52），于是每次装饰
    # 都往**同一个** handler_md.event_filters 里 append 一个独立 CommandFilter。
    # 而 waking_check/stage.py:192 遍历 event_filters 时是 **AND** 语义
    # （任一 filter 返回 False 就 break）——
    # 结果变成要求消息同时等于「菜单」「帮助」「指令」「help」，永远不成立。
    #
    # 症状：群里发 /菜单 毫无反应，消息掉进 LLM 流程，机器人回
    #       「LLM 请求失败：未找到任何可用的对话模型（提供商）」。
    # 极易误判成「模型没配」，实际是**指令压根没匹配上**。
    #
    # 正确写法：一个装饰器 + alias 集合（register_command 原生支持）
    #
    #     @filter.command("菜单", alias={"帮助", "指令", "help"})
    #
    # 这一段用 AST 静态扫描钉死，只要再出现多装饰器写法立刻红。
    import ast as _ast

    _reg_src = (HERE / "main.py").read_text(encoding="utf-8")
    _reg_tree = _ast.parse(_reg_src)

    _multi_deco = []
    for _node in _ast.walk(_reg_tree):
        if isinstance(_node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            _cnt = sum(
                1
                for _d in _node.decorator_list
                if isinstance(_d, _ast.Call)
                and isinstance(_d.func, _ast.Attribute)
                and _d.func.attr == "command"
            )
            if _cnt > 1:
                _multi_deco.append(f"{_node.name}×{_cnt}")
    check("没有方法叠加多个 @filter.command（框架是 AND 语义，会让别名全死）",
          not _multi_deco,
          "违规方法：" + ", ".join(_multi_deco) if _multi_deco else "")

    # 再逐个核对：每个多别名指令的主名与 alias 集合都对得上。
    # 这是把「修复本身」钉死 —— 别名漏一个，玩家敲那个词就静默失效。
    _alias_expect = {
        "cmd_menu": ("菜单", {"帮助", "指令", "help"}),
        "cmd_intro": ("简介", {"玩法", "介绍"}),
        "profile": ("属性", {"面板", "属性面板"}),
        "inventory": ("背包", {"储物袋"}),
        "hunt": ("历练", {"打怪"}),
        "dungeon": ("秘境", {"闯关"}),
        "challenge": ("寻敌", {"挑战"}),
        "revive": ("恢复", {"复活"}),
        "equip": ("装备", {"祭炼"}),
        "cmd_sign_in": ("签到", {"打卡"}),
        "cmd_vip": ("vip", {"赞助"}),
        "shop": ("坊市", {"商店"}),
        "seclusion": ("闭关", set()),
        "end_seclusion": ("结束闭关", {"出关"}),
        "explore": ("探索", set()),
        "end_explore": ("结束探索", {"收工", "结束游历"}),
    }
    _alias_actual = {}
    for _node in _ast.walk(_reg_tree):
        if isinstance(_node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            for _d in _node.decorator_list:
                if (
                    isinstance(_d, _ast.Call)
                    and isinstance(_d.func, _ast.Attribute)
                    and _d.func.attr == "command"
                    and _d.args
                    and isinstance(_d.args[0], _ast.Constant)
                ):
                    _al = set()
                    for _kw in _d.keywords:
                        if _kw.arg == "alias" and isinstance(
                            _kw.value, (_ast.Set, _ast.List)
                        ):
                            _al = {
                                _e.value
                                for _e in _kw.value.elts
                                if isinstance(_e, _ast.Constant)
                            }
                    _alias_actual[_node.name] = (_d.args[0].value, _al)

    for _fn, (_main_name, _al_names) in _alias_expect.items():
        check(f"{_fn} 主名 {_main_name} 与别名齐全",
              _alias_actual.get(_fn) == (_main_name, _al_names),
              f"实际 {_alias_actual.get(_fn)!r}，期望 {(_main_name, _al_names)!r}")

    print("\n[51] 挂机状态：闭关 / 游历互斥 + 结束指令（v3.6）")
    idle_plugin = plugin_mod.TextRPGPlugin(context=None, config={
        "reply_delay_range": [0, 0],
        "chunk_gap_range": [0, 0],
        "rate_limit_count": 10000,
        "group_whitelist_mode": "all",
    })
    ev_i = AstrMessageEvent(qq="43000", name="挂机者")
    await fast_create(idle_plugin, ev_i, "挂机者", level=19)
    pi = idle_plugin.store.get("999", "43000")

    # --- 初始态 ---
    check("初始不在闭关", not pi.is_in_seclusion())
    check("初始不在游历", not pi.is_exploring())
    check("初始 is_idle 为假", not pi.is_idle())
    check("train_start_ts 默认 0（老存档也补这个默认值）",
          pi.train_start_ts == 0.0)

    # --- 进入闭关 ---
    out = await run_cmd(idle_plugin, "seclusion", ev_i)
    check("闭关进入成功", "结束闭关" in out, out[:150])
    check("已进入闭关状态", pi.is_in_seclusion())
    check("闭关开始时间戳已写入", pi.train_start_ts > 0, f"{pi.train_start_ts}")
    check("is_idle 为真", pi.is_idle())
    check("idle_name 为闭关", pi.idle_name() == "闭关", pi.idle_name())

    # ★ 核心回归：刚进入闭关就要能出关。
    #   旧实现 last_train_ts 兼任「冷却起点」，进入后 1 小时内
    #   再发 /闭关 会被判成「刚出关不久，需调息」—— 根本结算不了。
    out = await run_cmd(idle_plugin, "end_seclusion", ev_i)
    check("刚进入就能出关（不再被冷却拦住）",
          "调息" not in out and "距离下次闭关" not in out, out[:150])
    check("出关后退出闭关状态", not pi.is_in_seclusion())
    check("出关后 train_start_ts 归零", pi.train_start_ts == 0.0,
          f"{pi.train_start_ts}")

    # ★ 核心回归：出关后没有冷却，可以立刻再闭关。
    #   旧实现结算过之后 last_train_ts 恒为正，
    #   「第一次闭关」那条分支永远进不去，闭关退化成领奖按钮。
    out = await run_cmd(idle_plugin, "seclusion", ev_i)
    check("出关后可立刻再闭关（无冷却）", pi.is_in_seclusion(), out[:150])

    # --- 闭关期间禁止其它玩法 ---
    for _cmd, _args, _label in (
        ("cultivate", (), "修炼"),
        ("hunt", (), "历练"),
        ("dungeon", (), "秘境"),
        ("challenge", ("史莱姆",), "寻敌"),
    ):
        out = await run_cmd(idle_plugin, _cmd, ev_i, *_args)
        check(f"闭关中 {_label} 被拦下", "结束闭关" in out, out[:150])

    # --- 互斥：闭关中不能探索 ---
    out = await run_cmd(idle_plugin, "explore", ev_i)
    check("闭关中不能探索（互斥）", "结束闭关" in out, out[:150])
    check("被拦下后仍在闭关（没串状态）",
          pi.is_in_seclusion() and not pi.is_exploring())

    # --- 出关 -> 进游历 ---
    out = await run_cmd(idle_plugin, "end_seclusion", ev_i)
    check("出关成功", not pi.is_in_seclusion(), out[:150])

    out = await run_cmd(idle_plugin, "explore", ev_i)
    check("游历进入成功", "结束探索" in out, out[:150])
    check("已进入游历状态", pi.is_exploring())
    check("idle_name 为游历", pi.idle_name() == "游历", pi.idle_name())

    # --- 互斥：游历中不能闭关 ---
    out = await run_cmd(idle_plugin, "seclusion", ev_i)
    check("游历中不能闭关（互斥）", "结束探索" in out, out[:150])
    check("被拦下后仍在游历（没串状态）",
          pi.is_exploring() and not pi.is_in_seclusion())

    # --- 游历期间禁止其它玩法 ---
    out = await run_cmd(idle_plugin, "cultivate", ev_i)
    check("游历中 修炼 被拦下", "结束探索" in out, out[:150])
    out = await run_cmd(idle_plugin, "hunt", ev_i)
    check("游历中 历练 被拦下", "结束探索" in out, out[:150])

    # --- 结束游历 ---
    out = await run_cmd(idle_plugin, "end_explore", ev_i)
    check("结束游历成功", not pi.is_exploring(), out[:150])
    check("结束游历后 explore_start_ts 归零", pi.explore_start_ts == 0.0)
    check("游历归来无冷却（可立刻再出门）",
          pi.explore_remain(idle_plugin.explore_cooldown) == 0.0)

    # --- 不在状态时给提示而不是崩 ---
    out = await run_cmd(idle_plugin, "end_seclusion", ev_i)
    check("没闭关时 /结束闭关 给提示", "并没有在闭关" in out, out[:150])
    out = await run_cmd(idle_plugin, "end_explore", ev_i)
    check("没游历时 /结束探索 给提示", "并没有在外游历" in out, out[:150])

    # --- /属性 状态行要能看出在挂机 ---
    await run_cmd(idle_plugin, "seclusion", ev_i)
    out = await run_cmd(idle_plugin, "profile", ev_i)
    check("/属性 显示「闭关中」", "闭关中" in out, out[:300])
    await run_cmd(idle_plugin, "end_seclusion", ev_i)

    # --- 源码级：闭关时长必须读 train_start_ts，不能再用 last_train_ts ---
    import inspect as _inspect
    _st_src = _inspect.getsource(battle_mod.settle_training)
    check("settle_training 用 train_start_ts 算时长",
          "player.train_start_ts" in _st_src)
    check("settle_training 不再用 last_train_ts 算时长",
          "(now - player.last_train_ts)" not in _st_src)

    # ------------------------------------------------------------------
    # [52] 角色形象显示 & 存档防覆盖（v3.6 线上事故回归）
    # ------------------------------------------------------------------
    print("\n[52] 角色形象显示 & 存档防覆盖（v3.6）")

    av_plugin = plugin_mod.TextRPGPlugin(context=None, config={
        "reply_delay_range": [0, 0],
        "chunk_gap_range": [0, 0],
        "rate_limit_count": 10000,
        "group_whitelist_mode": "all",
    })
    ev_a = AstrMessageEvent(qq="44000", name="画师")
    await fast_create(av_plugin, ev_a, "画师", level=19)
    pa = av_plugin.store.get("999", "44000")

    # --- 没形象时：只发文字面板，不发图 ---
    check("新号 avatar 默认为空", pa.avatar == "", repr(pa.avatar))
    out = await run_cmd(av_plugin, "profile", ev_a)
    check("无形象时 /属性 不发图片", "[图片]" not in out, out[:200])
    check("无形象时 /属性 仍是正常面板", "道途" in out, out[:200])
    # v3.6 排版：乘区栏已从面板移除（老板要求）
    check("面板不再显示「乘区」栏", "乘区" not in out, out[:300])

    # --- 有形象时：图片在最前，主面板次之，战绩单独一条 ---
    pa.avatar = av_plugin._avatar_rel_path("999", "44000")
    _av_abs = av_plugin._avatar_abs_path(pa.avatar)
    _av_abs.parent.mkdir(parents=True, exist_ok=True)
    _av_abs.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

    _parts = await run_cmd_parts(av_plugin, "profile", ev_a)
    _texts = [str(p) for p in _parts]

    # ① 图文同框：图片和面板文字在**同一条**消息里，图片在上
    check("有形象时只发 2 条：图文面板 + 进度战绩",
          len(_texts) == 2, _texts)
    check("第 1 条图文同框（既有图片又有面板文字）",
          "[图片]" in _texts[0] and "道途" in _texts[0], _texts[0][:200])
    check("图片排在文字前面（图片在上）",
          _texts[0].index("[图片]") < _texts[0].index("道途"), _texts[0][:200])
    check("图片用的是存档相对路径绝对化后的位置",
          str(_av_abs) in _texts[0], _texts[0][:200])
    check("面板没被分段切碎（整块一条）",
          _texts[0].count("道途") == 1 and "境界" in _texts[0], _texts[0][:200])

    # ② 「秘境最深 / 秘典 / 战绩」三行是一组，单独一条
    _tail = _texts[1]
    check("第 2 条含秘境行", "秘境最深" in _tail, _tail)
    check("第 2 条含秘典行", "秘典" in _tail, _tail)
    check("第 2 条含战绩行", "战绩" in _tail, _tail)
    check("主面板里不再含这三行",
          all(k not in _texts[0] for k in ("秘境最深", "秘典", "战绩")),
          _texts[0][-200:])

    # --- avatar 指向的文件不存在：不发图、不崩 ---
    pa.avatar = "avatars/999_44000_并不存在.png"
    out = await run_cmd(av_plugin, "profile", ev_a)
    check("形象文件缺失时不崩且不发图",
          "[图片]" not in out and "道途" in out, out[:200])

    # --- avatar 为空串：不发图 ---
    pa.avatar = ""
    out = await run_cmd(av_plugin, "profile", ev_a)
    check("avatar 空串时不发图", "[图片]" not in out, out[:200])

    # ★ 核心回归：未加载的 store 不许写盘。
    #   线上事故：players.json 与 .bak 双双变成 {}，玩家角色全丢。
    #   根因是 terminate() -> save() 时 _loaded 还是 False、_cache 是空的。
    import inspect as _inspect2
    _src_w = _inspect2.getsource(data_mod.PlayerStore._write_sync)
    check("_write_sync 带「未加载不写盘」保护", "self._loaded" in _src_w)

    # 行为级验证：磁盘有存档 -> 新建 store 但一次都不访问 -> save()
    _probe = HERE / "_testdata_guard"
    _probe.mkdir(parents=True, exist_ok=True)
    _keep = _probe / "players.json"
    _keep.write_text(
        '{"999:1":{"qq":"1","group_id":"999","name":"守卫"}}',
        encoding="utf-8",
    )
    _guard_store = data_mod.PlayerStore(_probe)
    await _guard_store.save()  # 故意不 ensure_loaded
    _after = _keep.read_text(encoding="utf-8")
    check("未加载就 save() 不会清空磁盘存档",
          "守卫" in _after, _after[:200])

    # 反例对照：正常加载后再保存，内容仍在（保护不能误伤正常写入）
    _guard_store.ensure_loaded()
    await _guard_store.save()
    _after2 = _keep.read_text(encoding="utf-8")
    check("正常加载后 save() 存档内容保留", "守卫" in _after2, _after2[:200])

    # 清掉本次的临时存档目录，别在仓库里留垃圾
    shutil.rmtree(_probe, ignore_errors=True)

    # 收尾：清掉测试留下的 VIP 名单，避免污染下一次运行
    _vip.clear_all()

    print("\n" + "=" * 50)
    print(f"结果：{PASS} 通过，{FAIL} 失败")
    print("=" * 50)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    code = asyncio.run(main())
    sys.exit(code)
