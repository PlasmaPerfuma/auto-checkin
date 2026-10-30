# -*- coding: utf-8 -*-
"""看门狗：每 5 分钟检查一次 autowatch，死了就立刻拉起来。

⚠️ 必须由**用户在资源管理器里双击**启动（`start-watchdog.cmd`）。
不能从 DSH 会话里启动——实测 DSH 沙箱会在命令结束时杀掉该命令派生的所有
子进程（2026-10-25 用 sleeper 脚本验证：日志只留下 start，没有 exit）。

为什么需要它：autowatch 已经两次在长休眠里静默消失，而开机自启只在登录时
跑一次。白天死了就没人管，第二天早八直接错过。

日志：logs/watchdog.log（只在"检查到死掉并拉起"时写，平时安静）。
"""
import datetime
import os
import sys
import time

import paths

paths.setup_env()

try:
    import dashboard as d
except Exception:
    sys.exit(1)

GAP = 300  # 秒；5 分钟一次
LOG = os.path.join(paths.LOGS, "watchdog.log")


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def main():
    log("watchdog 启动 pid=%d，每 %d 秒检查一次" % (os.getpid(), GAP))
    while True:
        try:
            pid = d.read_json_text(os.path.join(paths.LOGS, "autowatch.pid"))
            if not d.pid_alive(pid):
                log("autowatch 不在（pid=%s），重新拉起…" % pid)
                ok, msg = d.start_autowatch()
                log("  → %s / %s" % (ok, msg))
        except BaseException as e:      # 看门狗自己绝不能死
            log("检查时异常：%r" % (e,))
        time.sleep(GAP)


if __name__ == "__main__":
    main()
