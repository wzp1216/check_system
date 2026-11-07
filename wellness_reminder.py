#!/usr/bin/env python3
"""
Ubuntu 健康提醒 & 系统监控服务
================================
功能:
  1. 启动后每隔一小时提醒: 站立走动 + 英文单词 (一个, 含音标与释义)
  2. 系统健康检查: CPU / 内存 / 磁盘使用率 + journalctl 警告/错误
  3. 通过 systemd 用户服务管理, 开机自启
  4. 单词复习: 按艾宾浩斯遗忘曲线 (6h/1d/3d/7d/21d) 重复, 复习与新词交替

使用:
  手动测试:  python3 wellness_reminder.py --test
  安装服务:  bash install.sh
  卸载服务:  bash uninstall.sh
"""

import subprocess
import time
import json
import os
import sys
import signal
import logging
import random
from datetime import datetime, timedelta
from pathlib import Path

# ============================================================
# 配置
# ============================================================
CPU_THRESHOLD    = 80       # CPU 使用率超过此值视为异常 (%)
MEM_THRESHOLD    = 80       # 内存使用率超过此值视为异常 (%)
DISK_THRESHOLD   = 85       # 磁盘使用率超过此值视为异常 (%)

# 警告分级: 根据消息来源区分严重程度
# 严重来源的警告无论数量多少都提示; 非严重来源超过阈值才提示
WARN_SEVERE_SOURCES = {
    "kernel",           # 内核警告 (硬件/驱动/OOM 等)
    "systemd",          # systemd 核心
    "systemd-udevd",    # 设备管理
    "smartd",           # 磁盘健康监控
    "mdadm",            # 软 RAID
    "sshd",             # SSH 服务 (安全相关)
    "sudo",             # 权限提升 (安全相关)
}
WARN_NONSEVERE_THRESHOLD = 100  # 非严重警告超过此数量才提示 (条/小时)

# X 服务器日志噪音过滤: gdm-x-session 把信息级 (II) 消息也按 warning
# 优先级写入 journal, 造成每小时数百条假警告. 对这些来源只统计带
# (WW) 警告 / (EE) 错误标记的消息
X_NOISE_FILTER_SOURCES = {"/usr/libexec/gdm-x-session"}

# 艾宾浩斯遗忘曲线: 单词首次曝光后, 在这些时间点各复习一次 (小时)
EBBINGHAUS_INTERVALS_H = [6, 24, 72, 168, 504]  # 6小时 / 1天 / 3天 / 7天 / 21天

NOTIFY_TIMEOUT   = 20000    # 提醒通知停留 (ms)
NOTIFY_APP_NAME  = "健康提醒"

LOG_FILE = os.path.expanduser("~/.cache/wellness-reminder.log")
CONFIG_DIR = os.path.expanduser("~/.config/wellness-reminder")
WORD_FILE = os.path.join(CONFIG_DIR, "english_word.json")  # 英文单词库
STATE_FILE = os.path.join(CONFIG_DIR, "state.json")        # 单词复习状态

# ============================================================
# 日志
# ============================================================
def setup_logging():
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE),
        ],
    )


# ============================================================
# 桌面通知
# ============================================================
def notify_send(title: str, body: str, urgency: str = "normal",
                timeout: int = NOTIFY_TIMEOUT) -> bool:
    """发送桌面通知. 返回是否成功."""
    try:
        subprocess.run(
            ["notify-send",
             "-u", urgency,
             "-t", str(timeout),
             "-a", NOTIFY_APP_NAME,
             title, body],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError,
            subprocess.TimeoutExpired) as e:
        logging.warning(f"通知发送失败: {e}")
        return False


def is_session_available() -> bool:
    """检查桌面会话是否真的可用 (验证 D-Bus 连通性)."""
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return False
    try:
        subprocess.run(
            ["notify-send", "--version"],
            capture_output=True, timeout=3,
        )
        return True
    except Exception:
        return False


# ============================================================
# 静默时段检查
# ============================================================
def is_quiet_hours():
    """检查当前是否在静默时段 (21:00--07:00), 该时段内不发送运动提醒."""
    now = datetime.now()
    return now.hour >= 21 or now.hour < 7


# ============================================================
# 英文单词 (艾宾浩斯遗忘曲线复习)
# ============================================================
def load_word_library() -> list | None:
    """读取单词库 (JSON 数组). 文件缺失或损坏时返回 None."""
    try:
        with open(WORD_FILE, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        logging.warning(f"单词库文件不存在: {WORD_FILE}")
        return None
    except (json.JSONDecodeError, OSError) as e:
        logging.warning(f"单词库读取失败: {e}")
        return None
    if not data:
        logging.warning("单词库为空")
        return None
    return data


def load_word_state() -> dict:
    """读取复习状态, 清理格式非法的词条.

    词条格式: {"first": 首次曝光时间(ISO), "shown": 已显示次数(1-6, 6=已毕业)}
    返回 {"words": {...}, "next_kind": "new"|"review"}
    """
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    if not isinstance(state, dict):
        state = {}

    valid = {}
    for w, s in state.get("words", {}).items():
        if not isinstance(s, dict):
            continue
        try:
            datetime.fromisoformat(s["first"])
            shown = int(s.get("shown", 1))
            if 1 <= shown <= 6:
                valid[w] = {"first": s["first"], "shown": shown}
        except (KeyError, ValueError, TypeError):
            continue

    next_kind = state.get("next_kind")
    if next_kind not in ("new", "review"):
        next_kind = "new"
    return {"words": valid, "next_kind": next_kind}


def save_word_state(state: dict):
    """保存复习状态到 state.json."""
    Path(CONFIG_DIR).mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def format_word(w: dict) -> str:
    """格式化为 "📖 单词 /音标/ 词性 释义" 字符串."""
    word = (w.get("word") or "").strip()
    phonetic = (w.get("phonetic0") or w.get("phonetic1") or "").strip()
    trans = w.get("trans") or []
    if trans:
        pos = (trans[0].get("pos") or "").strip()
        cn = (trans[0].get("cn") or "").strip()
        meaning = f"{pos} {cn}".strip()
    else:
        meaning = ""

    parts = ["📖", word]
    if phonetic:
        parts.append(f"/{phonetic}/")
    if meaning:
        parts.append(meaning)
    return " ".join(parts)


def pick_next_word(data: list, state: dict) -> tuple[str, str] | None:
    """
    选取下一个要显示的单词 (复习与新词轮流交替).

    规则:
      - next_kind 决定本轮优先复习还是新词; 优先池无可用时退回另一池
      - 复习池: shown<6 且当前时间 >= first + EBBINGHAUS_INTERVALS_H[shown-1]
      - 新词池: 词库中尚未进入计划的单词
      - 两池皆空: 取未毕业单词中最近到期的提前复习; 全部毕业则重新循环词库
    选中后更新状态并保存, 返回 (格式化文本, 种类) 或 None.
    """
    now = datetime.now()
    words_state = state["words"]
    by_word = {w.get("word"): w for w in data if w.get("word")}

    def due_time(s):
        return datetime.fromisoformat(s["first"]) + timedelta(
            hours=EBBINGHAUS_INTERVALS_H[s["shown"] - 1])

    due = [by_word[w] for w, s in words_state.items()
           if w in by_word and s["shown"] < 6 and now >= due_time(s)]
    new_pool = [w for w in data if w.get("word") not in words_state]

    chosen, kind = None, ""
    if state["next_kind"] == "review":
        if due:
            chosen, kind = random.choice(due), "review"
        elif new_pool:
            chosen, kind = random.choice(new_pool), "new"
    else:
        if new_pool:
            chosen, kind = random.choice(new_pool), "new"
        elif due:
            chosen, kind = random.choice(due), "review"

    if chosen is None:
        # 两池皆空: 取最近到期的未毕业单词提前复习
        pending = sorted(
            (by_word[w] for w, s in words_state.items()
             if w in by_word and s["shown"] < 6),
            key=lambda w: due_time(words_state[w["word"]]))
        if pending:
            chosen, kind = pending[0], "review"
        elif data:
            # 全部毕业: 重新循环整个词库 (按新词处理, 重置计划)
            chosen, kind = random.choice(data), "new"

    if chosen is None:
        return None

    word = chosen["word"]
    if kind == "new":
        words_state[word] = {"first": now.isoformat(timespec="seconds"),
                             "shown": 1}
    else:
        words_state[word]["shown"] += 1
    state["next_kind"] = "new" if kind == "review" else "review"
    save_word_state(state)

    text = format_word(chosen)
    if kind == "review":
        text += " 🔄复习"
    return text, kind


# ============================================================
# 系统健康检查
# ============================================================
def get_cpu_percent() -> float | None:
    """获取 CPU 使用率 (基于 /proc/stat, 间隔 0.5s)."""
    try:
        def read_cpu():
            with open("/proc/stat", "r") as f:
                for line in f:
                    if line.startswith("cpu "):
                        return [int(x) for x in line.split()[1:]]
            return None

        t1 = read_cpu()
        if not t1:
            return None
        time.sleep(0.5)
        t2 = read_cpu()
        if not t2:
            return None

        idle_delta = t2[3] - t1[3]
        total_delta = sum(t2) - sum(t1)
        if total_delta == 0:
            return 0.0
        return round((1 - idle_delta / total_delta) * 100, 1)
    except Exception as e:
        logging.warning(f"CPU 使用率获取失败: {e}")
        return None


def get_memory_percent() -> float | None:
    """获取内存使用率 (基于 /proc/meminfo)."""
    try:
        mem = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) >= 2:
                    key = parts[0].strip()
                    val = parts[1].strip().split()[0]
                    mem[key] = int(val)

        total     = mem.get("MemTotal", 1)
        available = mem.get("MemAvailable", mem.get("MemFree", 0))
        if total == 0:
            return None
        return round((1 - available / total) * 100, 1)
    except Exception as e:
        logging.warning(f"内存使用率获取失败: {e}")
        return None


def get_disk_usage() -> tuple[str, float] | None:
    """获取使用率最高的挂载点及百分比 (使用 shutil)."""
    import shutil as _shutil

    candidates = ["/", "/home", "/var", "/tmp", "/boot"]
    max_part, max_pct = "", 0.0
    for mp in candidates:
        try:
            usage = _shutil.disk_usage(mp)
            pct = round(usage.used / usage.total * 100, 1)
            if pct > max_pct:
                max_pct = pct
                max_part = mp
        except Exception:
            continue
    if not max_part:
        return None
    return max_part, max_pct


def check_journal_errors() -> tuple[int, int, int]:
    """
    检查近1小时系统级 journalctl 中的警告/错误.
    只查系统级 (--system), 忽略用户级应用噪音.
    使用 -o json 根据 PRIORITY 字段准确分类:
      PRIORITY 0-3 → 错误,  PRIORITY 4 → 警告.
    警告按来源 (SYSLOG_IDENTIFIER) 分级:
      - 严重来源: 内核/systemd/硬件/安全等, 无论数量都提示
      - 非严重来源: GNOME/应用等, 超过阈值才提示
      - X 服务器来源 (X_NOISE_FILTER_SOURCES): 只统计 (WW)/(EE) 标记的消息,
        (II) 信息级消息属噪音, 忽略
    返回: (错误数, 严重警告数, 非严重警告数)
    """
    err_count, warn_severe, warn_nonsevere = 0, 0, 0

    for scope in ["--system"]:
        try:
            result = subprocess.run(
                ["journalctl", scope,
                 "--since", "1 hour ago",
                 "-p", "warning",
                 "--no-pager", "-q",
                 "-o", "json"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    source = entry.get("SYSLOG_IDENTIFIER", "")
                    # X 服务器噪音过滤: 只统计带 (WW)/(EE) 标记的消息
                    if source in X_NOISE_FILTER_SOURCES:
                        msg = entry.get("MESSAGE", "")
                        if not (msg.startswith("(WW)") or msg.startswith("(EE)")):
                            continue
                    priority = int(entry.get("PRIORITY", 4))
                    if priority <= 3:
                        err_count += 1
                    else:
                        # PRIORITY 4: 按来源分级
                        if source in WARN_SEVERE_SOURCES:
                            warn_severe += 1
                        else:
                            warn_nonsevere += 1
                except (json.JSONDecodeError, ValueError):
                    # 个别行解析失败则跳过, 不影响整体统计
                    continue
        except subprocess.CalledProcessError:
            logging.debug(f"journalctl {scope} 检查跳过 (权限不足)")
            continue
        except Exception as e:
            logging.debug(f"journalctl {scope} 检查跳过: {e}")
            continue

    return err_count, warn_severe, warn_nonsevere


# ============================================================
# 核心逻辑: 一轮提醒
# ============================================================
def run_reminder_cycle():
    """执行一次提醒周期: 运动提醒 + 健康检查 + 英文单词."""
    if not is_session_available():
        logging.warning("桌面会话不可用, 跳过本轮提醒")
        return

    # ---- 1. 选取下一个单词 (艾宾浩斯复习与新词交替) ----
    picked = pick_next_word(load_word_library() or [], load_word_state())
    word, word_kind = picked if picked else (None, "none")

    # ---- 2. 系统健康检查 ----
    cpu_pct = get_cpu_percent()
    mem_pct = get_memory_percent()
    disk_info = get_disk_usage()
    disk_part, disk_pct = disk_info if disk_info else ("", 0.0)
    jnl_err, jnl_warn_severe, jnl_warn_nonsevere = check_journal_errors()

    # ---- 3. 构建精简通知: 运动一下 -- 错误X个 -- 警告X个 -- 英文单词 ----
    parts = ["🏃 运动一下"]

    if jnl_err > 0:
        parts.append(f"🔴 错误{jnl_err}个")

    # 严重来源警告: 无论数量多少都提示
    if jnl_warn_severe > 0:
        parts.append(f"⚠️ 严重警告{jnl_warn_severe}个")

    # 非严重来源警告: 超过阈值才提示
    if jnl_warn_nonsevere >= WARN_NONSEVERE_THRESHOLD:
        parts.append(f"⚠️ 警告{jnl_warn_nonsevere}个")

    # CPU/内存/磁盘异常时追加告警标记
    has_resource_issue = False
    if cpu_pct is not None and cpu_pct > CPU_THRESHOLD:
        has_resource_issue = True
    if mem_pct is not None and mem_pct > MEM_THRESHOLD:
        has_resource_issue = True
    if disk_pct > DISK_THRESHOLD:
        has_resource_issue = True
    if has_resource_issue:
        parts.append("🔴 资源异常")

    if word:
        parts.append(word)

    body = " — ".join(parts)

    # 异常时提升 urgency: 有效警告数 = 严重警告 + 超阈值非严重警告
    effective_warns = jnl_warn_severe + (
        jnl_warn_nonsevere if jnl_warn_nonsevere >= WARN_NONSEVERE_THRESHOLD else 0
    )
    has_issue = has_resource_issue or jnl_err > 0 or effective_warns > 20
    urgency = "critical" if has_issue else "normal"

    notify_send("健康提醒", body, urgency=urgency, timeout=NOTIFY_TIMEOUT)

    # 日志记录详细信息 (含被忽略的非严重警告)
    status_parts = []
    if cpu_pct is not None:
        status_parts.append(f"CPU:{cpu_pct}%")
    if mem_pct is not None:
        status_parts.append(f"MEM:{mem_pct}%")
    if disk_part:
        status_parts.append(f"DISK({disk_part}):{disk_pct}%")
    log_detail = f"LOG:E{jnl_err}"
    if jnl_warn_severe > 0:
        log_detail += f"/WS{jnl_warn_severe}"
    if jnl_warn_nonsevere > 0:
        log_detail += f"/WN{jnl_warn_nonsevere}"
    if jnl_err + jnl_warn_severe + jnl_warn_nonsevere > 0:
        status_parts.append(log_detail)
    if word:
        status_parts.append(f"WORD[{word_kind}]:{word}")
    logging.info(f"提醒周期完成 | {' '.join(status_parts)}")


# ============================================================
# 主循环
# ============================================================
def main():
    setup_logging()
    logging.info("=== Wellness Reminder 服务启动 ===")

    running = True

    def handle_signal(sig, frame):
        nonlocal running
        logging.info(f"收到信号 {sig}, 正在退出...")
        running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # 等待桌面会话就绪 (最长等2分钟)
    waited = 0
    while not is_session_available() and waited < 120:
        time.sleep(2)
        waited += 2
    if is_session_available():
        logging.info("桌面会话已就绪")
    else:
        logging.warning("等待桌面会话超时, 通知功能可能不可用")

    # 启动后立即执行一次 (静默时段跳过)
    try:
        if is_quiet_hours():
            logging.info("当前处于静默时段 (21:00-07:00), 跳过首次运动提醒")
        else:
            run_reminder_cycle()
    except Exception as e:
        logging.error(f"首次提醒周期异常: {e}", exc_info=True)

    while running:
        try:
            # 分段休眠 3600 秒以响应退出信号
            remaining = 3600
            while remaining > 0 and running:
                chunk = min(remaining, 30)
                time.sleep(chunk)
                remaining -= chunk

            if not running:
                break

            if is_quiet_hours():
                logging.info("当前处于静默时段 (21:00-07:00), 跳过本轮运动提醒")
            else:
                run_reminder_cycle()
        except Exception as e:
            logging.error(f"提醒周期异常: {e}", exc_info=True)
            time.sleep(30)

    logging.info("=== Wellness Reminder 服务已退出 ===")


# ============================================================
# 测试模式 (python3 wellness_reminder.py --test)
# ============================================================
def test_mode():
    """立即执行一次完整流程, 用于手动验证."""
    setup_logging()
    # 同时输出到终端
    logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))
    logging.getLogger().setLevel(logging.DEBUG)

    print("\n" + "=" * 55)
    print("  Wellness Reminder — 测试模式")
    print("=" * 55)

    if not is_session_available():
        print("\n❌ 桌面会话不可用! 请在 GNOME 桌面终端中运行.")
        print(f"   DBUS_SESSION_BUS_ADDRESS = "
              f"{os.environ.get('DBUS_SESSION_BUS_ADDRESS', '(未设置)')}")
        sys.exit(1)
    print("✅ 桌面会话可用")

    # 确保配置目录存在
    Path(CONFIG_DIR).mkdir(parents=True, exist_ok=True)

    data = load_word_library()
    state = load_word_state()

    if data is None:
        print(f"\n⚠️  单词库文件不存在或损坏: {WORD_FILE}")
        print(f"   请先运行 install.sh 安装\n")
    else:
        n_words = len(state["words"])
        n_done = sum(1 for s in state["words"].values() if s["shown"] >= 6)
        now = datetime.now()
        n_due = sum(
            1 for s in state["words"].values()
            if s["shown"] < 6
            and now >= datetime.fromisoformat(s["first"])
            + timedelta(hours=EBBINGHAUS_INTERVALS_H[s["shown"] - 1]))
        n_new = len(data) - n_words
        print(f"\n📖 单词库: 共 {len(data)} 词 | 计划中 {n_words - n_done} | "
              f"已毕业 {n_done} | 到期复习 {n_due} | 待学新词 {n_new}")

    print("\n📖 本轮选取单词:")
    picked = pick_next_word(data or [], state)
    if picked:
        word_text, word_kind = picked
        print(f"  [{word_kind}] {word_text}")
    else:
        print("  (无)")

    print("\n📊 系统状态检查:")
    cpu = get_cpu_percent()
    mem = get_memory_percent()
    disk = get_disk_usage()
    jerr, jwarn_severe, jwarn_nonsevere = check_journal_errors()

    print(f"  CPU:    {cpu}%" if cpu is not None else "  CPU:    N/A")
    print(f"  内存:   {mem}%" if mem is not None else "  内存:   N/A")
    if disk:
        print(f"  磁盘:   {disk[0]} = {disk[1]}%")
    else:
        print("  磁盘:   N/A")
    if jerr > 0 or jwarn_severe > 0 or jwarn_nonsevere > 0:
        print(f"  日志:   {jerr} 错误, {jwarn_severe} 严重警告, "
              f"{jwarn_nonsevere} 一般警告 (近1小时, 系统级)")
        if jwarn_nonsevere > 0 and jwarn_nonsevere < WARN_NONSEVERE_THRESHOLD:
            print(f"          ({jwarn_nonsevere} 条一般警告未达 {WARN_NONSEVERE_THRESHOLD} 阈值, 通知中忽略)")
    else:
        print("  日志:   无警告/错误 ✅")

    print("\n🔔 发送桌面通知...")
    run_reminder_cycle()
    print("✅ 测试完成!\n")


if __name__ == "__main__":
    if "--test" in sys.argv:
        test_mode()
    else:
        main()
