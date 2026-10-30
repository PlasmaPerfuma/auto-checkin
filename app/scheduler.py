# -*- coding: utf-8 -*-
"""
课表调度器。

  periods.json  —— 每天固定的课时时段表（第几课时从几点到几点）+ 监视线外扩参数
  schedule.json —— 每周重复的课表模板（哪天的哪几个课时、在哪几周上课）

把「一节课 = 若干连续课时」展开成具体时间窗口：
  课前盯   = 课开始前 pre_min 分钟起
  各课时   = 照本宣科
  中间休息 = 上一课时结束前 break_pre_min ~ 下一课时开始后 break_post_min（**重点签到窗口**）
  课后盯   = 课结束后再盯 post_min 分钟

用法：
  --show        打印某一周的完整监视线（默认本周；--week N 指定第几周）
  --now         只看此刻在不在某个窗口里
  --run         常驻：到窗口就拉起 capture_checkin.py
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import paths                      # 项目唯一的路径真源（app/paths.py）
HERE = paths.ROOT                 # 兼容旧代码：HERE 一律指项目根
PERIODS = paths.PERIODS
SCHEDULE = paths.SCHEDULE
DAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def load_json(path, label):
    if not os.path.isfile(path):
        print(f"[!] 找不到 {label}: {path}")
        sys.exit(1)
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def hm(s):
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def parse_weeks(s):
    """'1-16' / '1,3,5' / '2,4-7,9' -> set[int]"""
    out = set()
    for part in str(s or "").replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                out.update(range(int(a), int(b) + 1))
            except ValueError:
                pass
        else:
            try:
                out.add(int(part))
            except ValueError:
                pass
    return out


def week_of(date, semester_start):
    return (date - semester_start).days // 7 + 1


def build_sessions(week_no, include_disabled=False):
    """构建某一周的课。

    include_disabled=False（默认）：**真正执行**用的视图，停用的课直接跳过。
    include_disabled=True：**界面**用的视图，停用的课也返回，但带 enabled=False，
                           这样取消勾选后那一行不会从列表里凭空消失。
    """
    P = load_json(PERIODS, "periods.json")
    S = load_json(SCHEDULE, "schedule.json")
    w = P.get("watch", {})
    pre, post = w.get("pre_min", 10), w.get("post_min", 10)
    bpre, bpost = w.get("break_pre_min", 3), w.get("break_post_min", 3)
    pmap = {p["no"]: p for p in P["periods"]}

    sem = dt.date.fromisoformat(S["semesterStart"])
    monday = sem + dt.timedelta(days=(week_no - 1) * 7)

    out, skipped = [], []
    for c in S.get("classes", []):
        # 界面里取消勾选 = 写 "enabled": false；缺省视为启用，
        # 所以旧课表（没有这个字段）行为完全不变。
        enabled = c.get("enabled", True) is not False
        if not enabled and not include_disabled:
            continue
        if week_no not in parse_weeks(c.get("weeks")):
            continue
        noid = c.get("courseId") is None
        if noid:
            skipped.append(c.get("course", "?"))
        day = c.get("day", "")
        if day not in DAYS:
            print(f"[!] 未知的 day: {day!r}")
            continue
        pnos = sorted(n for n in (c.get("periods") or []) if n in pmap)
        if not pnos:
            print(f"[!] {c.get('course')} 的课时号无效: {c.get('periods')}")
            continue
        date = monday + dt.timedelta(days=DAYS.index(day))
        start = hm(pmap[pnos[0]]["start"])
        end = hm(pmap[pnos[-1]]["end"])

        phases = [("课前盯",
                   dt.datetime.combine(date, start) - dt.timedelta(minutes=pre),
                   dt.datetime.combine(date, start))]
        for i, no in enumerate(pnos):
            p = pmap[no]
            phases.append((f"课时{no}",
                           dt.datetime.combine(date, hm(p["start"])),
                           dt.datetime.combine(date, hm(p["end"]))))
            if i + 1 < len(pnos):
                nxt = pmap[pnos[i + 1]]
                phases.append((f"★中间休息{no}->{pnos[i + 1]}",
                               dt.datetime.combine(date, hm(p["end"])) - dt.timedelta(minutes=bpre),
                               dt.datetime.combine(date, hm(nxt["start"])) + dt.timedelta(minutes=bpost)))
        phases.append(("课后盯",
                       dt.datetime.combine(date, end),
                       dt.datetime.combine(date, end) + dt.timedelta(minutes=post)))

        sess = {
            "date": date, "day": day, "course": c.get("course", "?"),
            "courseId": c.get("courseId"), "toolId": c.get("toolId"),
            "room": c.get("room", ""), "periods": pnos, "noId": noid,
            "enabled": enabled,
            "watch": (phases[0][1], phases[-1][2]),
            "phases": phases,
        }
        # 每门课可以覆盖签到用的地点（课表里的可选 lat/lng/address）。
        # 只在这几个字段确实存在时才带上，旧课表行为完全不变。
        for _k in ("lat", "lng", "address"):
            if c.get(_k) not in (None, ""):
                sess[_k] = c[_k]
        out.append(sess)
    out.sort(key=lambda x: x["watch"][0])
    return out, skipped, sem


def fmt(d):
    return d.strftime("%m-%d %H:%M")


def show(week_no):
    sessions, skipped, sem = build_sessions(week_no)
    monday = sem + dt.timedelta(days=(week_no - 1) * 7)
    sunday = monday + dt.timedelta(days=6)
    print(f"第 {week_no} 周（{monday} ~ {sunday}）")
    if not sessions:
        print("  本周没有需要处理的课（或数据还没填全）。")
    else:
        print(f"  共 {len(sessions)} 节课\n")
    for s in sessions:
        t0, t1 = s["watch"]
        tag = "   ⚠ 缺 courseId，暂不执行" if s.get("noId") else ""
        print(f"{s['day']} {s['date']}  {s['course']}  课时{s['periods']}  {s['room']}{tag}")
        print(f"    总监视线：{fmt(t0)} → {fmt(t1)}")
        for name, a, b in s["phases"]:
            mark = "  ← 重点" if name.startswith("★") else ""
            print(f"      {name:<16} {a.strftime('%H:%M')}-{b.strftime('%H:%M')}{mark}")
        print()
    if skipped:
        uniq = sorted(set(skipped))
        print(f"[!] 这些课因为缺 courseId 被跳过（占位，不影响窗口计算）：{uniq}")
    return sessions


def phase_now(sessions, now):
    for s in sessions:
        for name, a, b in s["phases"]:
            if a <= now <= b:
                return s, name
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="打印某一周的监视线")
    ap.add_argument("--now", action="store_true", help="只看此刻")
    ap.add_argument("--run", action="store_true", help="常驻调度")
    ap.add_argument("--week", type=int, default=None, help="看第几周（默认本周）")
    ap.add_argument("--date", default=None, help="假装今天是哪天（YYYY-MM-DD，调试用）")
    ap.add_argument("--tick", type=float, default=30, help="--run 的检查间隔秒数")
    ap.add_argument("--spawn", action="store_true", help="--run 到窗口时真启动 capture_checkin.py")
    args = ap.parse_args()

    _, _, sem = build_sessions(1)          # 只为拿 semesterStart
    today = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    week_no = args.week if args.week else week_of(today, sem)

    if not (args.now or args.run):
        show(week_no)
        if not args.week:
            print(f"（今天是第 {week_no} 周。看别的周用 --week N）")
        return 0

    sessions = build_sessions(week_no)[0]

    if args.now:
        now = dt.datetime.now()
        s, name = phase_now(sessions, now)
        if s:
            print(f"现在 {now:%H:%M}，正处在：{s['course']} 的「{name}」")
        else:
            print(f"现在 {now:%H:%M}，第 {week_no} 周此刻不在任何课的窗口内。")
        nxt = min((x for x in sessions if x["watch"][0] > now),
                  key=lambda x: x["watch"][0], default=None)
        if nxt:
            print(f"下一节：{nxt['course']}  从 {fmt(nxt['watch'][0])} 开始盯")
        return 0

    if args.run:
        print(f"调度器启动（第 {week_no} 周，{len(sessions)} 节课），每 {args.tick:.0f}s 检查一次。")
        fired = set()
        try:
            while True:
                now = dt.datetime.now()
                for s in sessions:
                    if s.get("noId"):
                        continue
                    t0, t1 = s["watch"]
                    key = (s["date"], s["course"], tuple(s["periods"]))
                    if t0 <= now <= t1 and key not in fired:
                        fired.add(key)
                        print(f"\n[{now:%H:%M:%S}] ▶ 进入窗口：{s['course']} "
                              f"课时{s['periods']}（{fmt(t0)} → {fmt(t1)}）")
                        if args.spawn:
                            url = f"https://oc.sjtu.edu.cn/courses/{s['courseId']}"
                            print(f"    启动 capture_checkin.py {url}")
                            try:
                                subprocess.Popen([sys.executable,
                                                  os.path.join(paths.APP_DIR, "capture_checkin.py"),
                                                  "--course-url", url],
                                                 cwd=paths.APP_DIR)
                            except Exception as e:
                                print(f"    [!] 启动失败: {e}")
                        else:
                            print("    （--spawn 未开，只打印不启动）")
                    elif t1 < now and key in fired:
                        fired.discard(key)
                time.sleep(args.tick)
        except KeyboardInterrupt:
            print("\n[i] 结束。")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
