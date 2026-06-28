#!/bin/bash
# ================================================================
# Wellness Reminder - 卸载脚本
# 停止并移除 systemd 服务及相关文件
# ================================================================
set -euo pipefail

SERVICE_NAME="wellness-reminder.service"
SCRIPT_NAME="wellness_reminder.py"

BIN_DIR="${HOME}/.local/bin"
CONFIG_DIR="${HOME}/.config/wellness-reminder"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
SCRIPT_PATH="${BIN_DIR}/${SCRIPT_NAME}"
SERVICE_PATH="${SYSTEMD_USER_DIR}/${SERVICE_NAME}"

echo "========================================"
echo "  Wellness Reminder 卸载脚本"
echo "========================================"
echo ""

# --- 停止并禁用服务 ---
if [ -f "${SERVICE_PATH}" ]; then
    echo ">> 停止服务..."
    systemctl --user stop "${SERVICE_NAME}" 2>/dev/null || true
    echo ">> 禁用服务..."
    systemctl --user disable "${SERVICE_NAME}" 2>/dev/null || true
    echo ">> 删除服务文件..."
    rm -vf "${SERVICE_PATH}"
    systemctl --user daemon-reload
    echo "  ✅ 服务已停止并移除"
else
    echo "  ℹ️  服务文件不存在, 跳过"
fi

# --- 移除 Python 脚本 ---
if [ -f "${SCRIPT_PATH}" ]; then
    echo ""
    echo ">> 删除 Python 脚本..."
    rm -vf "${SCRIPT_PATH}"
    echo "  ✅ 脚本已删除"
else
    echo "  ℹ️  脚本文件不存在, 跳过"
fi

# --- 清理日志 ---
if [ -f "${HOME}/.cache/wellness-reminder.log" ]; then
    echo ""
    echo ">> 清理日志文件..."
    rm -vf "${HOME}/.cache/wellness-reminder.log"
    echo "  ✅ 日志已清理"
fi

# --- 清理配置目录 (含用户编辑的名言库) ---
if [ -d "${CONFIG_DIR}" ]; then
    echo ""
    read -r -p "是否删除名言库及配置目录 (${CONFIG_DIR})? 包含你的名言编辑 [y/N] " answer
    if [[ "${answer,,}" == "y" ]]; then
        rm -rfv "${CONFIG_DIR}"
        echo "  ✅ 配置目录已删除"
    else
        echo "  ℹ️  保留配置目录: ${CONFIG_DIR}"
    fi
fi

# --- 可选: 禁用 linger ---
echo ""
read -r -p "是否禁用用户 linger (关闭开机启动用户服务的能力)? [y/N] " answer
if [[ "${answer,,}" == "y" ]] && command -v loginctl &>/dev/null; then
    sudo loginctl disable-linger "$(whoami)" 2>/dev/null || {
        echo "  ⚠️  禁用失败, 请手动运行: sudo loginctl disable-linger $(whoami)"
    }
    echo "  ✅ linger 已禁用"
fi

echo ""
echo "========================================"
echo "  卸载完成!"
echo "========================================"
