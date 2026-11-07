# check_system_onehour — Ubuntu 健康提醒与系统监控服务

> **角色模板（必读）**：开始任何任务前，先阅读并遵循
> `/media/data/claudewzp/.claude/prompts/A-coding.md`
> 中的角色定义、领域专长、工作方式与质量标准。本文件仅补充本项目特有信息；两者冲突时以模板为准。

## 项目概述

Ubuntu 桌面系统用户服务，每小时提醒站立走动并推送一个随机英文单词（音标+词性释义），同时监控系统健康状态（CPU/内存/磁盘使用率 + journalctl 错误/警告日志分级过滤）。单词按艾宾浩斯遗忘曲线复习（6h/1d/3d/7d/21d，从首次曝光起算），复习与新词每小时交替。通过 systemd 用户服务 (`wellness-reminder.service`) 管理，开机自启。

## 关键文件与目录

| 路径 | 作用 |
|------|------|
| `wellness_reminder.py` | 主程序（含所有逻辑），顶部为配置常量区 |
| `english_word.json` | 英文单词库（音标/词性/释义/词源，安装时部署到 `~/.config`） |
| `install.sh` | 安装脚本（复制到 `~/.local/bin`、启用 systemd 用户服务） |
| `uninstall.sh` | 卸载脚本（停止服务、删除文件） |
| `README.md` | 完整使用说明（219行） |
| `help.readme` | 简明帮助文本 |

## 常用命令

```bash
# 手动测试运行（输出到终端，不依赖 systemd）
python3 wellness_reminder.py --test

# 查看服务状态
systemctl --user status wellness-reminder

# 查看服务日志
journalctl --user -u wellness-reminder -f

# 安装 / 卸载
bash install.sh
bash uninstall.sh
```

## 项目约定

- **配置常量集中在文件顶部**：`CPU_THRESHOLD`、`MEM_THRESHOLD`、`DISK_THRESHOLD`、`WARN_SEVERE_SOURCES`、`WARN_NONSEVERE_THRESHOLD`、静默时段等
- **日志过滤分级**：严重来源（kernel/systemd/sshd/sudo 等）的警告无论数量都提示；非严重来源须超过 100 条/小时才提示
- **静默时段**：21:00–07:00 不发送站立提醒（系统监控不受影响）
- **单词复习**：按艾宾浩斯遗忘曲线复习（`EBBINGHAUS_INTERVALS_H`，从首次曝光起算），复习与新词每小时交替，进度记录在 `~/.config/wellness-reminder/state.json`
- **通知退化**：notify-send 不可用时不得崩溃，记录日志即可
- **安装路径**：程序文件 → `~/.local/bin/`，配置和数据 → `~/.config/wellness-reminder/`
- **Git 提交信息**：中文，`feat:`/`fix:`/`docs:` 前缀

## 常见任务

- 调整监控告警阈值（修改文件顶部常量）
- 新增/修改日志过滤规则（严重来源列表、非严重阈值）
- 修改提醒内容或频率（单词库、静默时段、提醒间隔）
- 调试 systemd 服务（journalctl 日志分析、--test 测试模式）
- 扩展或替换单词库（覆盖 `english_word.json`）
- 调整复习间隔（`EBBINGHAUS_INTERVALS_H`）或复习/新词交替策略
