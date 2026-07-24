# Wellness Reminder — 健康提醒 & 系统监控服务

一个 Ubuntu 桌面系统服务，定时提醒站立走动，同时监控系统健康状态（CPU/内存/磁盘/journalctl 日志），并通过桌面通知推送。

## 功能概览

| 功能 | 说明 |
|------|------|
| 🏃 **运动提醒** | 每小时提醒站立活动（21:00–07:00 静默） |
| 📜 **名言推送** | 附带一条随机名言，本地库管理 |
| 📊 **系统监控** | 检查 CPU / 内存 / 磁盘使用率 |
| 📋 **日志告警** | 监控 journalctl 系统级警告/错误，智能过滤噪音 |
| 🔔 **桌面通知** | 通过 `notify-send` 推送，异常时提升 urgency |
| 🚀 **开机自启** | 作为 systemd 用户服务运行 |

## 系统要求

- **操作系统**: Ubuntu 桌面版（GNOME 或其他支持 `notify-send` 的环境）
- **依赖**: `python3`、`libnotify-bin`（提供 `notify-send`）
- **权限**: 需 `sudo` 权限安装（linger 配置），运行无需 root

## 快速安装

```bash
# 1. 克隆仓库
git clone git@github.com:wzp1216/check_system.git
cd check_system

# 2. 运行安装脚本
bash install.sh
```

安装脚本会自动：
- 安装依赖 (`libnotify-bin`)
- 复制 Python 脚本到 `~/.local/bin/`
- 安装名言库到 `~/.config/wellness-reminder/quotes.txt`
- 创建并启用 systemd 用户服务
- 配置 `linger` 实现开机自启

安装完成后服务立即启动，每小时自动执行一次。

## 手动测试

```bash
python3 ~/.local/bin/wellness_reminder.py --test
```

输出示例：

```
=======================================================
  Wellness Reminder — 测试模式
=======================================================
✅ 桌面会话可用
📜 名言库共有 500 条名言

📜 随机选取名言:
  "学而不思则罔，思而不学则殆" — 孔子

📊 系统状态检查:
  CPU:    12.5%
  内存:   45.2%
  磁盘:   / = 62.1%
  日志:   0 错误, 1 严重警告, 45 一般警告 (近1小时, 系统级)
          (45 条一般警告未达 100 阈值, 通知中忽略)

🔔 发送桌面通知...
✅ 测试完成!
```

## 服务管理

```bash
# 查看服务状态
systemctl --user status wellness-reminder.service

# 查看实时日志
journalctl --user -u wellness-reminder.service -f

# 停止服务
systemctl --user stop wellness-reminder.service

# 重启服务
systemctl --user restart wellness-reminder.service

# 禁用开机自启
systemctl --user disable wellness-reminder.service
```

## 文件路径

| 用途 | 路径 |
|------|------|
| Python 脚本 | `~/.local/bin/wellness_reminder.py` |
| systemd 服务文件 | `~/.config/systemd/user/wellness-reminder.service` |
| 应用运行日志 | `~/.cache/wellness-reminder.log` |
| 名言库（可直接编辑）| `~/.config/wellness-reminder/quotes.txt` |
| 名言储备池 | `~/.config/wellness-reminder/quotes_reserve.txt` |
| 状态文件 | `~/.config/wellness-reminder/state.json` |

## 配置说明

所有配置项位于 `wellness_reminder.py` 文件顶部，可按需修改：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `CPU_THRESHOLD` | 80% | CPU 超过此值视为异常 |
| `MEM_THRESHOLD` | 80% | 内存超过此值视为异常 |
| `DISK_THRESHOLD` | 85% | 磁盘超过此值视为异常 |
| `NOTIFY_TIMEOUT` | 20000ms | 通知停留时间 |
| `WARN_SEVERE_SOURCES` | 见下方 | 严重警告来源列表 |
| `WARN_NONSEVERE_THRESHOLD` | 100 | 非严重警告阈值（条/小时） |

## 警告分级机制

journalctl 日志警告按来源分为两级，避免应用噪音淹没真正的系统问题：

### 严重来源（始终告警）

以下来源的警告无论数量多少都会在通知中显示：

```python
WARN_SEVERE_SOURCES = {
    "kernel",           # 内核警告 (硬件/驱动/OOM)
    "systemd",          # systemd 核心
    "systemd-udevd",    # 设备管理
    "smartd",           # 磁盘健康监控
    "mdadm",            # 软 RAID
    "sshd",             # SSH 服务（安全相关）
    "sudo",             # 权限提升（安全相关）
}
```

### 非严重来源（阈值过滤）

其他来源（GNOME、NetworkManager、snap、应用等）的警告只有 **超过 100 条/小时** 才会出现在通知中。低于阈值时仅记录到日志文件，不打扰用户。

### 通知展示规则

| 日志类型 | 通知行为 |
|----------|----------|
| 🔴 错误 (PRIORITY 0–3) | 有就显示 |
| ⚠️ 严重警告 | 有就显示 |
| ⚠️ 一般警告 | ≥ 100 条/小时才显示 |
| 一般警告 (< 100) | 仅记日志，不影响通知 |

## 通知内容

通知标题固定为「健康提醒」，正文格式：

```
🏃 运动一下 — 🔴 错误2个 — ⚠️ 严重警告1个 — ⚠️ 警告150个 — 🔴 资源异常 — 📜 "名言内容" — 作者
```

- 无异常时只显示：`🏃 运动一下 — 📜 名言`
- 有异常时 urgency 自动提升为 `critical`

## 名言库管理

### 查看/编辑

直接编辑 `~/.config/wellness-reminder/quotes.txt`，每行一条名言。删除不喜欢的行即可。

### 每月自动追加

每月自动从「一言 API」拉取 100 条新名言追加到库中。如果 API 拉取不足 50 条，会从储备池 (`quotes_reserve.txt`) 补充。

### 储备池

`quotes_reserve.txt` 是预置的备用名言池，首次安装时随 `quotes.txt` 一起提供。

## 静默时段

21:00 – 07:00 期间不发送运动提醒（静默时段由 `is_quiet_hours()` 函数控制）。系统健康检查仍正常执行并记录日志，但不会弹通知。

## 卸载

```bash
bash uninstall.sh
```

卸载脚本会：
- 停止并禁用 systemd 服务
- 删除 Python 脚本
- 清理应用日志
- 询问是否删除名言库和配置目录（保留你的编辑）
- 询问是否禁用 linger

## 调试

```bash
# 查看应用自身日志
cat ~/.cache/wellness-reminder.log

# 查看 systemd 服务日志
journalctl --user -u wellness-reminder.service -n 50

# 手动运行测试模式（输出到终端）
python3 ~/.local/bin/wellness_reminder.py --test
```

## 项目结构

```
check_system/
├── wellness_reminder.py          # 主程序
├── wellness_reminder_quotes.txt  # 名言储备池（安装时复制到 ~/.config）
├── install.sh                    # 安装脚本
├── uninstall.sh                  # 卸载脚本
└── README.md                     # 本文件
```
