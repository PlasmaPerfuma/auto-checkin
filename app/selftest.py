# -*- coding: utf-8 -*-
"""
离线自检 —— 不启动浏览器、不联网，纯本地检查。

用途：每次改完代码先跑它，能挡住绝大部分"搬坏了/写错了"的问题。
（需要真启动浏览器的部分验不了，那些只能在外面跑。）

用法：python app\\main.py selftest
"""
import datetime as dt
import glob
import json
import os
import re
import sys
import urllib.parse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import paths  # noqa: E402

FAIL = []
PASS = []


def ok(name, detail=""):
    PASS.append(name)
    print(f"  [v] {name}" + (f"  {detail}" if detail else ""))


def bad(name, detail=""):
    FAIL.append(f"{name}: {detail}")
    print(f"  [X] {name}" + (f"  {detail}" if detail else ""))


def section(t):
    print()
    print("=" * 74)
    print(t)


# ---------------------------------------------------------------- 1. 路径
section("1. 路径与目录")
problems = paths.check(verbose=False)
if problems:
    for p in problems:
        bad("必需的路径", p)
else:
    ok("所有必需路径都存在")
for d in (paths.DATA, paths.LOGS, paths.OUTPUT, paths.TMP):
    if os.path.isdir(d):
        ok(f"目录 {os.path.basename(d)}/", d)
    else:
        bad(f"目录缺失 {d}")

# ---------------------------------------------------------------- 2. 配置
section("2. 配置文件")


def load(path, label):
    if not os.path.isfile(path):
        bad(label, f"文件不存在 {path}")
        return None
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as e:
        bad(label, f"{type(e).__name__}: {str(e)[:80]}")
        return None


periods_doc = load(paths.PERIODS, "periods.json")
schedule_doc = load(paths.SCHEDULE, "schedule.json")
courses_doc = load(paths.COURSES, "courses.json (可选)")
if isinstance(courses_doc, list):
    ok("courses.json", f"{len(courses_doc)} 门课")
elif courses_doc is not None:
    bad("courses.json", "格式应为数组")

cred_doc = load(paths.CREDENTIALS, "credentials.json")
if isinstance(cred_doc, dict):
    u, p = (cred_doc.get("username") or "").strip(), (cred_doc.get("password") or "").strip()
    if u and p:
        ok("credentials.json", f"账号已填（{u[:3]}***）")
    else:
        bad("credentials.json", "username/password 还是空的")

if os.path.isfile(paths.TOKEN):
    tok = open(paths.TOKEN, encoding="utf-8").read().strip()
    ok("mlearning-token.txt", f"{len(tok)} 字符")
else:
    bad("mlearning-token.txt", "不存在（跑一次 python app\\main.py get-token）")

# ---------------------------------------------------------------- 3. 课时表
section("3. 课时表")
pmap = {}
if isinstance(periods_doc, dict) and periods_doc.get("periods"):
    ps = periods_doc["periods"]
    ok("课时数量", f"{len(ps)} 个")
    try:
        prev = None
        for p in sorted(ps, key=lambda x: x["no"]):
            s, e = p["start"], p["end"]
            sh, sm = (int(x) for x in s.split(":"))
            eh, em = (int(x) for x in e.split(":"))
            if (eh, em) <= (sh, sm):
                raise ValueError(f"第{p['no']}课时 结束早于开始")
            if prev is not None and (sh, sm) < prev:
                raise ValueError(f"第{p['no']}课时 与上一节时间倒挂")
            prev = (sh, sm)
            pmap[p["no"]] = p
        ok("课时时间单调且合法")
    except Exception as e:
        bad("课时表", str(e)[:90])
    w = periods_doc.get("watch", {})
    for k in ("pre_min", "post_min", "break_pre_min", "break_post_min"):
        if k in w:
            ok(f"watch.{k}", str(w[k]))
        else:
            bad("watch", f"缺 {k}")
else:
    bad("periods.json", "没有 periods 数组")

# ---------------------------------------------------------------- 4. 课表
section("4. 课表与周次")
DAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def parse_weeks(s):
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


if isinstance(schedule_doc, dict) and schedule_doc.get("classes"):
    sem = dt.date.fromisoformat(schedule_doc["semesterStart"])
    if sem.weekday() != 0:
        bad("semesterStart", f"{sem} 不是周一")
    else:
        ok("semesterStart", f"{sem}（周一）")
    classes = schedule_doc["classes"]
    ok("课表条目", f"{len(classes)} 个上课段")
    noid = [c.get("course") for c in classes if c.get("courseId") is None]
    if noid:
        bad("有课缺 courseId", str(sorted(set(noid))))
    else:
        ok("所有课都有 courseId")
    # 周次解析抽查
    for c in classes:
        ws = parse_weeks(c.get("weeks"))
        if not ws:
            bad("周次解析", f"{c.get('course')} weeks={c.get('weeks')!r} 解析为空")
        else:
            bad_days = [d for d in (c.get("day"),) if d not in DAYS]
            if bad_days:
                bad("星期非法", f"{c.get('course')} day={c.get('day')!r}")
    # 本周展开
    today = dt.date.today()
    wk = (today - sem).days // 7 + 1
    n = sum(1 for c in classes
            if wk in parse_weeks(c.get("weeks")) and c.get("courseId") is not None)
    ok("本周（第 %d 周）应上的课" % wk, f"{n} 节")
else:
    bad("schedule.json", "没有 classes")

# ---------------------------------------------------------------- 5. 解析器
section("5. 解析器抽查")
QR = ("https://mlearning.sjtu.edu.cn/lms/mobile2/forscan/?courseCode=95173"
      "&rollCallToken=dcb6cb4ad1334bf8a94e70e38becb24e_NORMAL_6ac47f1fd9835f0c03c2d3aa7c3bae52"
      "&signHistoryId=dcb6cb4ad1334bf8a94e70e38becb24e&fromType=scanRollCall")
q = urllib.parse.parse_qs(urllib.parse.urlparse(QR).query)
q = {k: v[0] for k, v in q.items()}
if q.get("signHistoryId") and q.get("rollCallToken", "").startswith(q["signHistoryId"] + "_NORMAL_"):
    ok("二维码解析", "signHistoryId 与 rollCallToken 前缀一致")
else:
    bad("二维码解析", "字段对不上")

for c in ("zdah", "bjbme"):
    if re.fullmatch(r"[A-Za-z0-9]{3,6}", c):
        ok(f"验证码格式 {c!r}", "接受")
    else:
        bad(f"验证码格式 {c!r}", "不应该被拒")
for c in ("", "ab", "a b c", "abcdefg"):
    if not (c and re.fullmatch(r"[A-Za-z0-9]{3,6}", c)):
        ok(f"验证码格式 {c!r}", "正确拒绝")
    else:
        bad(f"验证码格式 {c!r}", "应该被拒")

# ---------------------------------------------------------------- 6. 启动器
section("6. .cmd 启动器")
cmds = sorted(glob.glob(os.path.join(paths.ROOT, "*.cmd"))
              + glob.glob(os.path.join(paths.ROOT, "probe", "*.cmd")))
if not cmds:
    bad(".cmd", "一个都没找到")
for p in cmds:
    raw = open(p, "rb").read()
    nbad = sum(1 for b in raw if b > 127)
    if nbad:
        bad(f"{os.path.relpath(p, paths.ROOT)}", f"含 {nbad} 个非 ASCII 字节")
    else:
        ok(f"{os.path.relpath(p, paths.ROOT)}", "纯 ASCII")

# ---------------------------------------------------------------- 7. 代码
section("7. 代码文件")
pys = sorted(glob.glob(os.path.join(paths.APP_DIR, "*.py")))
ok("app/ 下脚本数", str(len(pys)))
nopaths = []
for p in pys:
    src = open(p, encoding="utf-8").read()
    name = os.path.basename(p)
    if name == "paths.py":
        continue
    if "import paths" not in src:
        if re.search(r"\bHERE\b", src):
            bad(name, "用了 HERE 却没 import paths")
        else:
            nopaths.append(name)
ok("接入 paths.py 的脚本", f"{len(pys) - 1 - len(nopaths)} 个")
if nopaths:
    print(f"      （这 {len(nopaths)} 个不碰项目路径、不需要 paths：{', '.join(nopaths)}）")

# ---------------------------------------------------------------- 汇总
print()
print("=" * 74)
print(f"通过 {len(PASS)}  失败 {len(FAIL)}")
if FAIL:
    print("失败项：")
    for x in FAIL:
        print("  -", x)
    print("=" * 74)
    sys.exit(1)
print("自检全部通过 [OK]")
print("=" * 74)
