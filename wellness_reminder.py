#!/usr/bin/env python3
"""
Ubuntu 健康提醒 & 系统监控服务
================================
功能:
  1. 启动后每隔一小时提醒: 站立走动 + 本地名言 (一条)
  2. 系统健康检查: CPU / 内存 / 磁盘使用率 + journalctl 警告/错误
  3. 通过 systemd 用户服务管理, 开机自启
  4. 名言本地化管理: 用户可编辑, 每月自动追加

使用:
  手动测试:  python3 wellness_reminder.py --test
  安装服务:  bash install.sh
  卸载服务:  bash uninstall.sh
"""

import subprocess
import time
import json
import urllib.request
import urllib.error
import os
import sys
import signal
import logging
import random
from datetime import datetime
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

QUOTES_API       = "https://v1.hitokoto.cn/"   # 一言 API (月度追加用)
QUOTES_TIMEOUT   = 8        # API 请求超时 (秒)
MONTHLY_ADD_COUNT = 100     # 每月追加名言数量

NOTIFY_TIMEOUT   = 20000    # 提醒通知停留 (ms)
NOTIFY_APP_NAME  = "健康提醒"

LOG_FILE = os.path.expanduser("~/.cache/wellness-reminder.log")
CONFIG_DIR = os.path.expanduser("~/.config/wellness-reminder")
QUOTES_FILE = os.path.join(CONFIG_DIR, "quotes.txt")
RESERVE_FILE = os.path.join(CONFIG_DIR, "quotes_reserve.txt")
STATE_FILE = os.path.join(CONFIG_DIR, "state.json")

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
# 本地名言管理
# ============================================================
def get_random_quote() -> str | None:
    """从本地名言文件随机选取一条."""
    try:
        with open(QUOTES_FILE, "r") as f:
            lines = [l.strip() for l in f if l.strip()]
        if not lines:
            logging.warning("名言文件为空")
            return None
        return random.choice(lines)
    except FileNotFoundError:
        logging.warning(f"名言文件不存在: {QUOTES_FILE}")
        return None


def fetch_one_quote_from_api(category: str = "") -> str | None:
    """从一言 API 获取一条名言. 返回格式化的字符串."""
    url = QUOTES_API
    if category:
        url += f"?c={category}"
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "WellnessReminder/1.0")
        with urllib.request.urlopen(req, timeout=QUOTES_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            content = data.get("hitokoto", "").strip()
            author  = data.get("from_who") or data.get("from", "") or "佚名"
            if content:
                return f'"{content}" — {author.strip()}'
    except Exception:
        pass
    return None


def fetch_quotes_batch(count: int) -> list[str]:
    """从一言 API 批量拉取名言的去重列表."""
    categories = ["d", "k", "c", "i", "a", "b"]
    quotes = []
    seen = set()
    max_attempts = count * 3
    attempts = 0

    while len(quotes) < count and attempts < max_attempts:
        cat = categories[attempts % len(categories)]
        q = fetch_one_quote_from_api(cat)
        if q and q not in seen:
            seen.add(q)
            quotes.append(q)
        attempts += 1
        time.sleep(0.3)

    logging.info(f"API 拉取: {len(quotes)}/{count} 条名言 ({attempts} 次请求)")
    return quotes


def fetch_from_reserve(count: int) -> list[str]:
    """从储备池取名言 (不足则返回实际数量)."""
    try:
        with open(RESERVE_FILE, "r") as f:
            lines = [l.strip() for l in f if l.strip()]
        if len(lines) <= count:
            result = lines[:]
        else:
            result = random.sample(lines, count)
        logging.info(f"储备池取出: {len(result)} 条名言")
        return result
    except FileNotFoundError:
        logging.warning(f"储备池文件不存在: {RESERVE_FILE}")
        return []


def maybe_add_monthly_quotes():
    """如果距上次追加超过30天，尝试追加 100 条名言."""
    Path(CONFIG_DIR).mkdir(parents=True, exist_ok=True)

    # 读取上次追加月份
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {"last_add_month": ""}

    current_month = datetime.now().strftime("%Y-%m")
    if state.get("last_add_month") == current_month:
        return  # 本月已追加

    logging.info(f"本月 ({current_month}) 尚未追加名言，开始获取...")

    # 优先从 API 拉取
    new_quotes = fetch_quotes_batch(MONTHLY_ADD_COUNT)

    # 不足 50 条则从储备池补充
    if len(new_quotes) < 50:
        logging.warning(f"API 仅获取 {len(new_quotes)} 条，从储备池补充")
        reserve_quotes = fetch_from_reserve(MONTHLY_ADD_COUNT - len(new_quotes))
        new_quotes.extend(reserve_quotes)

    if new_quotes:
        with open(QUOTES_FILE, "a") as f:
            for q in new_quotes:
                f.write(q + "\n")
        state["last_add_month"] = current_month
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
        logging.info(f"本月已追加 {len(new_quotes)} 条名言到 {QUOTES_FILE}")
    else:
        logging.warning("未能获取任何新名言，跳过本月追加")


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
                    priority = int(entry.get("PRIORITY", 4))
                    if priority <= 3:
                        err_count += 1
                    else:
                        # PRIORITY 4: 按来源分级
                        source = entry.get("SYSLOG_IDENTIFIER", "")
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
    """执行一次提醒周期: 运动提醒 + 健康检查 + 名言."""
    if not is_session_available():
        logging.warning("桌面会话不可用, 跳过本轮提醒")
        return

    # ---- 1. 从本地文件获取一条名言 ----
    quote = get_random_quote()

    # ---- 2. 系统健康检查 ----
    cpu_pct = get_cpu_percent()
    mem_pct = get_memory_percent()
    disk_info = get_disk_usage()
    disk_part, disk_pct = disk_info if disk_info else ("", 0.0)
    jnl_err, jnl_warn_severe, jnl_warn_nonsevere = check_journal_errors()

    # ---- 3. 构建精简通知: 运动一下 -- 错误X个 -- 警告X个 -- 名言 ----
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

    if quote:
        parts.append(f"📜 {quote}")

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
            # 每月检查名言追加
            maybe_add_monthly_quotes()

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

    # 如果名言文件不存在，提示从安装目录复制
    if not os.path.exists(QUOTES_FILE):
        print(f"\n⚠️  名言文件不存在: {QUOTES_FILE}")
        print(f"   请先运行 install.sh 安装，或手动创建该文件")
        print(f"   将使用内置回退逻辑继续测试...\n")
    else:
        with open(QUOTES_FILE, "r") as f:
            count = sum(1 for l in f if l.strip())
        print(f"📜 名言库共有 {count} 条名言")

    print("\n📜 随机选取名言:")
    quote = get_random_quote()
    if quote:
        print(f"  {quote}")
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
