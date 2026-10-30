# -*- coding: utf-8 -*-
"""查 mlearning 的签到记录（只读，不提交任何东西）。

用途：验证「签到是否真的被服务端记账」——
签到接口成功时只返回 {"resultCode":"200","body":null}，看不到记录变化，
所以只能靠事先记下 total、事后比对 +1。

用法：双击 check-records.cmd（或 python check-records.py）。
可选参数：--course 95173  指定课程（默认 95173 反常积分）
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "app"))

import paths  # noqa: E402

paths.setup_env()

BASE = "https://mlearning.sjtu.edu.cn/lms-lti-rollcall-sjtu"


def baseline_path(course):
    """基线按课程分开存：不同课的 total 不能互相比较。"""
    return os.path.join(paths.LOGS, "records-baseline-%s.txt" % course)


def http_get(url, token):
    req = urllib.request.Request(url, headers={
        "Authorization": token,
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, repr(e)


def read_baseline(course):
    try:
        with open(baseline_path(course), encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def write_baseline(course, text):
    try:
        os.makedirs(paths.LOGS, exist_ok=True)
        with open(baseline_path(course), "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def main():
    course = "95173"
    av = sys.argv[1:]
    for i, a in enumerate(av):
        if a == "--course" and i + 1 < len(av):
            course = av[i + 1]

    try:
        with open(paths.TOKEN, encoding="utf-8") as f:
            token = f.read().strip()
    except Exception as e:
        print("[!] 读不到 token 文件: %r" % (e,))
        return 1

    url = "%s/sign/records?courseCode=%s&pageNum=1&pageSize=10" % (BASE, course)
    code, body = http_get(url, token)
    print("课程 %s    HTTP %s" % (course, code))
    if code != 200:
        print("[!] 请求失败，可能是 token 过期。body 前 200 字:")
        print("    " + body[:200])
        return 1

    try:
        d = json.loads(body)
    except Exception:
        print("[!] 返回不是 JSON: " + body[:200])
        return 1

    print("resultCode = %s   resultMessage = %s"
          % (d.get("resultCode"), d.get("resultMessage")))
    if str(d.get("resultCode")) != "200":
        return 1

    b = d.get("body") or {}
    total = b.get("total") if isinstance(b, dict) else None
    print("")
    print("=" * 52)
    print("  当前签到记录总数 total = %s" % total)
    print("=" * 52)

    items = (b.get("dataList") or []) if isinstance(b, dict) else []
    if items:
        print("\n最近几条（字段名可能随接口变化，原样打印）:")
        for it in items[:5]:
            print("  " + json.dumps(it, ensure_ascii=False)[:160])

    # 记基线 / 与基线对比
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    prev = read_baseline(course)
    if prev:
        prev_total = prev.split()[0] if prev.split() else ""
        if prev_total != str(total):
            print("\n>>> 与基线对比：%s → %s  **变了！**" % (prev_total, total))
        else:
            print("\n>>> 与基线对比：%s → %s  没变" % (prev_total, total))
        print("    基线记录于: %s" % " ".join(prev.split()[1:]))
    else:
        write_baseline(course, "%s %s" % (total, stamp))
        print("\n>>> 已记录基线: total=%s（%s）" % (total, stamp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
