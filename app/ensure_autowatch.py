# -*- coding: utf-8 -*-
"""看门狗：守护进程死了就重新拉起，活着就立刻退出。

为什么需要它
------------
autowatch 已经**两次**在长休眠里静默消失（2026-10-24 一次、10-25 一次）。
它是 pythonw 进程，没有控制台，死了不留任何痕迹——日志停在"休眠到 X"，
事件日志里也没有崩溃记录。开机自启只在登录时跑一次，所以白天死了没人管。

用法
----
计划任务每 10 分钟调一次：
    pythonw.exe app\\ensure_autowatch.py
活着 → 立刻退出（什么都不做）；死了 → 用既有的 start_autowatch 拉起来。

设计上刻意极简
--------------
· 不打印、不写日志、不改任何配置——它自己绝不能成为新的故障源。
· 判断存活和拉起都复用 dashboard.py 里已有的函数（同一份实现，不重复逻辑）。
· 任何异常都吞掉并返回 1，绝不弹窗、绝不阻塞。
"""
import os
import sys

import paths

paths.setup_env()

try:
    import dashboard as d
except Exception:
    sys.exit(1)


def main():
    try:
        pid = d.read_json_text(os.path.join(paths.LOGS, "autowatch.pid"))
        if d.pid_alive(pid):
            return 0                      # 活着，什么都不做
        ok, _msg = d.start_autowatch()
        return 0 if ok else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
