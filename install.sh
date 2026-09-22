#!/usr/bin/env bash
# AstrBot 文字 RPG 插件 —— 一键安装脚本
#
# 用法：
#   bash install.sh                  # 自动查找 AstrBot 目录
#   bash install.sh /path/to/AstrBot # 手动指定 AstrBot 根目录
#
# 脚本做的事：
#   1. 定位 AstrBot 根目录
#   2. 把插件复制到 data/plugins/
#   3. 校验文件完整性
#   4. 跑一遍自测确认没问题
#
# 注意：脚本不负责安装 AstrBot 本体和 NapCat，
#       那两步需要 GUI 交互（下载安装包、扫码登录 QQ）。

set -euo pipefail

PLUGIN_NAME="astrbot_plugin_textrpg"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

green() { printf '\033[32m%s\033[0m\n' "$1"; }
red()   { printf '\033[31m%s\033[0m\n' "$1"; }
yellow(){ printf '\033[33m%s\033[0m\n' "$1"; }
info()  { printf '  %s\n' "$1"; }

echo "=========================================="
echo " AstrBot 文字 RPG 插件 安装脚本"
echo "=========================================="
echo

# ---------------------------------------------------------------------------
# 1. 定位 AstrBot 根目录
# ---------------------------------------------------------------------------
find_astrbot() {
    # 用户手动指定优先
    if [ "${1:-}" != "" ]; then
        if [ -d "$1/data" ] || [ -d "$1/data/plugins" ]; then
            echo "$1"; return 0
        fi
        red "指定的目录不是有效的 AstrBot 根目录：$1"
        return 1
    fi

    # 常见安装位置
    local candidates=(
        "$HOME/AstrBot"
        "$HOME/astrbot"
        "$HOME/Documents/AstrBot"
        "$HOME/Downloads/AstrBot"
        "/opt/AstrBot"
        "D:/ai/AstrBot"
        "C:/AstrBot"
        "$SCRIPT_DIR/../AstrBot"
    )
    for c in "${candidates[@]}"; do
        if [ -d "$c/data" ]; then
            echo "$c"; return 0
        fi
    done

    # 家目录下浅层搜索（限深度，避免全盘扫描）
    local found
    found=$(find "$HOME" -maxdepth 3 -type d -name "AstrBot" 2>/dev/null | head -1 || true)
    if [ -n "$found" ] && [ -d "$found/data" ]; then
        echo "$found"; return 0
    fi

    return 1
}

info "正在查找 AstrBot 安装目录..."
if ASTRBOT_DIR=$(find_astrbot "${1:-}"); then
    green "找到：$ASTRBOT_DIR"
else
    red "未找到 AstrBot 安装目录。"
    echo
    echo "请先安装 AstrBot，然后手动指定路径："
    echo "  bash install.sh /你的/AstrBot/路径"
    echo
    echo "AstrBot 安装方式："
    echo "  桌面版：https://astrbot.app 下载安装包"
    echo "  命令行：git clone https://github.com/AstrBotDevs/AstrBot"
    echo
    echo "（沙箱/本机访问不了 github 的话，用桌面版安装包最省事）"
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. 校验源文件
# ---------------------------------------------------------------------------
echo
info "校验插件源文件..."
REQUIRED=(main.py metadata.yaml player.py battle.py data.py items.py)
for f in "${REQUIRED[@]}"; do
    if [ ! -f "$SCRIPT_DIR/$f" ]; then
        red "缺少必需文件：$f"
        exit 1
    fi
done
green "源文件完整（${#REQUIRED[@]} 个核心文件）"

# ---------------------------------------------------------------------------
# 3. 复制插件
# ---------------------------------------------------------------------------
PLUGINS_DIR="$ASTRBOT_DIR/data/plugins"
TARGET="$PLUGINS_DIR/$PLUGIN_NAME"

echo
info "目标位置：$TARGET"
mkdir -p "$PLUGINS_DIR"

if [ -d "$TARGET" ]; then
    yellow "检测到已存在的插件，将覆盖（玩家存档不受影响）"
    # 只删代码文件，保留插件目录（存档不在这里，在 data/plugin_data/）
    rm -rf "$TARGET"
fi

mkdir -p "$TARGET"

# 复制代码，排除测试产物与缓存
for item in main.py metadata.yaml player.py battle.py data.py items.py \
            _conf_schema.json requirements.txt README.md selftest.py; do
    if [ -f "$SCRIPT_DIR/$item" ]; then
        cp "$SCRIPT_DIR/$item" "$TARGET/"
    fi
done

# 清理可能被带过来的缓存
rm -rf "$TARGET/__pycache__" "$TARGET/_testdata" "$TARGET/_testdata_bad" "$TARGET/_testdata_old"

green "已复制 $(ls -1 "$TARGET" | wc -l | tr -d ' ') 个文件"

# ---------------------------------------------------------------------------
# 4. 跑自测
# ---------------------------------------------------------------------------
echo
info "运行自测（验证核心逻辑）..."
PYTHON_BIN="${PYTHON_BIN:-python}"
if command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python3"
fi

if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    if (cd "$TARGET" && "$PYTHON_BIN" selftest.py >/tmp/textrpg_selftest.log 2>&1); then
        RESULT=$(grep -E "^结果" /tmp/textrpg_selftest.log || echo "自测完成")
        green "$RESULT"
    else
        yellow "自测未全部通过，详见 /tmp/textrpg_selftest.log"
        tail -5 /tmp/textrpg_selftest.log || true
    fi
    rm -rf "$TARGET/_testdata" "$TARGET/_testdata_bad" "$TARGET/_testdata_old" "$TARGET/__pycache__"
else
    yellow "未找到 python 命令，跳过自测（不影响使用）"
fi

# ---------------------------------------------------------------------------
# 5. 完成提示
# ---------------------------------------------------------------------------
echo
echo "=========================================="
green " 安装完成"
echo "=========================================="
echo
echo "接下来："
echo "  1. 启动 AstrBot，打开 WebUI（默认 http://localhost:6185）"
echo "  2. 进「插件」页，找到「文字冒险 RPG」，点启用"
echo "  3. 群里发送 /报名 开始游戏"
echo
echo "存档位置：$ASTRBOT_DIR/data/plugin_data/$PLUGIN_NAME/"
echo "配置文件：$ASTRBOT_DIR/data/config/${PLUGIN_NAME}_config.json"
echo
echo "完整指令表见：$TARGET/README.md"
echo
