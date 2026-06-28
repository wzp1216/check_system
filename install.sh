#!/bin/bash
# ================================================================
# Wellness Reminder - 安装脚本
# 将服务安装为 systemd 用户服务, 开机自启
# ================================================================
set -euo pipefail

SCRIPT_NAME="wellness_reminder.py"
QUOTES_NAME="wellness_reminder_quotes.txt"
SERVICE_NAME="wellness-reminder.service"

# 路径
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
CONFIG_DIR="${HOME}/.config/wellness-reminder"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

echo "========================================"
echo "  Wellness Reminder 安装脚本"
echo "========================================"
echo ""

# --- 检查依赖 ---
echo ">> 检查依赖..."

if ! command -v notify-send &>/dev/null; then
    echo "⚠️  notify-send 未安装, 正在安装 libnotify-bin..."
    if command -v apt &>/dev/null; then
        sudo apt update && sudo apt install -y libnotify-bin
    else
        echo "❌ 请手动安装 libnotify-bin (notify-send)"
        exit 1
    fi
fi
echo "  ✅ notify-send 可用: $(which notify-send)"

if ! command -v python3 &>/dev/null; then
    echo "❌ python3 未安装, 请先安装 python3"
    exit 1
fi
echo "  ✅ python3 可用: $(python3 --version)"

# --- 安装 Python 脚本 ---
echo ""
echo ">> 安装 Python 脚本..."

mkdir -p "${BIN_DIR}"
cp -v "${SCRIPT_DIR}/${SCRIPT_NAME}" "${BIN_DIR}/${SCRIPT_NAME}"
chmod +x "${BIN_DIR}/${SCRIPT_NAME}"
echo "  ✅ 脚本已安装到 ${BIN_DIR}/${SCRIPT_NAME}"

# --- 安装名言库 ---
echo ""
echo ">> 安装名言库..."

mkdir -p "${CONFIG_DIR}"

if [ -f "${CONFIG_DIR}/quotes.txt" ]; then
    echo "  ℹ️  名言文件已存在，保留用户编辑 (不覆盖)"
else
    cp -v "${SCRIPT_DIR}/${QUOTES_NAME}" "${CONFIG_DIR}/quotes.txt"
    echo "  ✅ 名言库已安装: ${CONFIG_DIR}/quotes.txt"
    echo "     你可以直接编辑该文件，删除不喜欢的行即可"
fi

# --- 创建 systemd 用户服务 ---
echo ""
echo ">> 创建 systemd 用户服务..."

mkdir -p "${SYSTEMD_USER_DIR}"

cat > "${SYSTEMD_USER_DIR}/${SERVICE_NAME}" << SYSTEMDEOF
[Unit]
Description=Wellness Reminder - 健康提醒 & 系统监控
Documentation=https://github.com/user/wellness-reminder
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=${BIN_DIR}/${SCRIPT_NAME}
ExecStop=/bin/kill -s TERM \$MAINPID
Restart=on-failure
RestartSec=30

[Install]
WantedBy=graphical-session.target
SYSTEMDEOF

echo "  ✅ 服务文件已创建: ${SYSTEMD_USER_DIR}/${SERVICE_NAME}"

# --- 启用 linger (允许用户服务在开机时启动) ---
echo ""
echo ">> 启用用户 linger (允许开机启动用户服务)..."

if command -v loginctl &>/dev/null; then
    sudo loginctl enable-linger "$(whoami)" 2>/dev/null || {
        echo "  ⚠️  linger 启用失败 (可能需要 sudo 权限)"
        echo "     请手动运行: sudo loginctl enable-linger $(whoami)"
    }
    echo "  ✅ linger 已启用"
else
    echo "  ⚠️  loginctl 不可用, 服务将在登录后启动而非开机时"
fi

# --- 重载 & 启用服务 ---
echo ""
echo ">> 启用 systemd 服务..."

systemctl --user daemon-reload
systemctl --user enable "${SERVICE_NAME}"
systemctl --user start "${SERVICE_NAME}"

echo ""
echo ">> 服务状态:"
systemctl --user status "${SERVICE_NAME}" --no-pager --lines=5 || true

echo ""
echo "========================================"
echo "  安装完成!"
echo "========================================"
echo ""
echo "  管理命令:"
echo "    查看状态:  systemctl --user status ${SERVICE_NAME}"
echo "    查看日志:  journalctl --user -u ${SERVICE_NAME} -f"
echo "    停止服务:  systemctl --user stop ${SERVICE_NAME}"
echo "    重启服务:  systemctl --user restart ${SERVICE_NAME}"
echo "    手动测试:  python3 ${BIN_DIR}/${SCRIPT_NAME} --test"
echo ""
echo "  应用日志:  ~/.cache/wellness-reminder.log"
echo "  名言库:    ${CONFIG_DIR}/quotes.txt (可直接编辑)"
echo "  状态文件:  ${CONFIG_DIR}/state.json"
echo ""
