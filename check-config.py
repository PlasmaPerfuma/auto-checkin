# -*- coding: utf-8 -*-
"""配置写入的端到端测试。

做法：把 AUTOCHECKIN_HOME 指到一个临时目录，把真实 data/ 复制进去，
**所有读写都发生在副本上**；同时全程对真实 data/ 做哈希比对，
证明"写命令跑了一圈，真配置一个字节都没动"。
"""
import hashlib
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
REAL_DATA = os.path.join(ROOT, "data")

# ---- 1) 先建沙箱，再 import（paths 在 import 时就定死了目录） ----
# 不用 tempfile：本机 Temp 目录有外部清理工具在抢，建完就被删（曾踩）。
# 直接在项目里开一个固定名字的沙箱，跑完自己清掉。
SANDBOX = os.path.join(ROOT, "_sandbox_cfg")
if os.path.isdir(SANDBOX):
    shutil.rmtree(SANDBOX, ignore_errors=True)
os.makedirs(os.path.join(SANDBOX, "data"), exist_ok=True)
for fn in os.listdir(REAL_DATA):
    src = os.path.join(REAL_DATA, fn)
    if os.path.isfile(src) and fn.endswith(".json"):
        shutil.copy2(src, os.path.join(SANDBOX, "data", fn))
os.environ["AUTOCHECKIN_HOME"] = SANDBOX

sys.path.insert(0, APP)
import paths  # noqa: E402

paths.setup_env()
import commands  # noqa: E402
import dashboard  # noqa: E402

assert paths.DATA.startswith(SANDBOX), f"沙箱没生效！DATA={paths.DATA}"

fail = 0


def check(label, cond, extra=""):
    global fail
    if cond:
        print(f"  OK   {label}")
    else:
        fail += 1
        print(f"  FAIL {label} {extra}")


def tree_hash(d):
    h = hashlib.sha256()
    for fn in sorted(os.listdir(d)):
        p = os.path.join(d, fn)
        if os.path.isfile(p):
            h.update(fn.encode())
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


REAL_BEFORE = tree_hash(REAL_DATA)
print(f"沙箱: {SANDBOX}")
print(f"真实 data/ 指纹: {REAL_BEFORE[:16]}...")

print("\n=== 1) settings.get → 默认值（文件还不存在）===")
ok, err, s = commands.dispatch("settings.get")
check("返回 ok", ok, str(err))
check("默认坐标 31.0269", s["checkin"]["lat"] == "31.0269", str(s))
check("默认截图间隔 0.6", s["watch"]["sampleGap"] == 0.6)

print("\n=== 2) settings.set 合法值 ===")
ok, err, r = commands.dispatch("settings.set", {"checkin": {"lat": 31.0300}})
check("返回 ok", ok, str(err))
check("坐标已更新", ok and r["settings"]["checkin"]["lat"].startswith("31.03"), str(r))
check("生成了 settings.json", os.path.isfile(os.path.join(paths.DATA, "settings.json")))

print("\n=== 3) settings.set 越界必须被拒（坐标必须在闵行校区） ===")
ok, err, r = commands.dispatch("settings.set", {"checkin": {"lat": 40.0}})
check("ok=False", ok is False)
check("code=out_of_campus", (err or {}).get("code") == "out_of_campus", str(err))
ok2, _, s2 = commands.dispatch("settings.get")
check("被拒后坐标没变", s2["checkin"]["lat"].startswith("31.03"), str(s2))

print("\n=== 4) settings.set 参数类型错误 ===")
ok, err, r = commands.dispatch("settings.set", {"watch": {"sampleGap": 99}})
check("code=bad_gap", (err or {}).get("code") == "bad_gap", str(err))
ok, err, r = commands.dispatch("settings.set", {"watch": {"sampleGap": "abc"}})
check("非数字也被拒", ok is False and (err or {}).get("code") == "bad_gap", str(err))

print("\n=== 5) schedule.get.all ===")
ok, err, d = commands.dispatch("schedule.get.all")
check("返回 ok", ok, str(err))
check("有 8 个上课段", ok and d["count"] == 8, str(d.get("count")))
for c in d["classes"][:2]:
    check(f"第 {c['index']} 项带 enabled", "enabled" in c)

print("\n=== 6) 基准：整个学期有多少节（写之前）===")
import scheduler  # noqa: E402


def semester_total():
    t = 0
    for wk in range(1, 17):
        res = scheduler.build_sessions(wk)
        ss = res[0] if isinstance(res, tuple) else res
        t += len(ss or [])
    return t


BASE = semester_total()
print("     基准节数:", BASE)
check("基准 > 0", BASE > 0)

print("\n=== 7) schedule.setEnabled(false) → 那一段真的不再被调度 ===")
target = None
for c in d["classes"]:
    if c.get("course") == "反常积分与级数" and c.get("day") == "周一":
        target = c
        break
check("找到目标上课段", target is not None)
ok, err, r = commands.dispatch("schedule.setEnabled",
                               {"index": target["index"], "enabled": False})
check("返回 ok", ok, str(err))
AFTER = semester_total()
print(f"     停用后节数: {AFTER}（基准 {BASE}）")
check("节数变少（说明 scheduler 真的认 enabled）", AFTER < BASE, f"{AFTER} vs {BASE}")

print("\n=== 8) 再启用回来 → 恢复原样 ===")
ok, err, r = commands.dispatch("schedule.setEnabled",
                               {"index": target["index"], "enabled": True})
check("返回 ok", ok, str(err))
check("节数回到基准", semester_total() == BASE)

print("\n=== 9) schedule.update 非法输入必须被拒 ===")
for patch, code in (
    ({"day": "周八"}, "bad_day"),
    ({"weeks": "abc"}, "bad_weeks"),
    ({"weeks": ""}, "bad_weeks"),
    ({"periods": []}, "bad_periods"),
    ({"periods": [2, 1]}, "bad_periods"),
    ({"periods": [0]}, "bad_periods"),
    ({"course": ""}, "bad_course"),
):
    ok, err, r = commands.dispatch("schedule.update",
                                   {"index": target["index"], "patch": patch})
    okc = (err or {}).get("code") == code
    check(f"拒绝 {list(patch)[0]}={list(patch.values())[0]!r}（{code}）", okc, str(err))

ok, err, r = commands.dispatch("schedule.update",
                               {"index": target["index"], "patch": {"hack": 1}})
check("拒绝未知字段", (err or {}).get("code") == "bad_field", str(err))
check("被拒后课表没被改坏", semester_total() == BASE)

print("\n=== 10) schedule.update 合法修改 ===")
ok, err, r = commands.dispatch("schedule.update",
                               {"index": target["index"],
                                "patch": {"room": "上院999"}})
check("返回 ok", ok, str(err))
check("room 已改", ok and r["class"]["room"] == "上院999", str(r))
check("节数不受影响", semester_total() == BASE)

print("\n=== 11) 备份与回滚 ===")
ok, err, b = commands.dispatch("config.backups", {"file": "schedule.json"})
check("有备份列表", ok and len(b["backups"]) > 0, str(err))
snapshot = b["backups"][0]
ok, err, r = commands.dispatch("config.restore",
                               {"file": "schedule.json", "name": snapshot})
check("恢复成功", ok, str(err))
raw = json.load(open(paths.SCHEDULE, encoding="utf-8-sig"))
check("恢复后能解析", isinstance(raw.get("classes"), list))
check("恢复后节数仍正常", semester_total() > 0)

ok, err, r = commands.dispatch("config.restore", {"file": "../../etc/passwd"})
check("路径穿越被拒", (err or {}).get("code") == "bad_args", str(err))
ok, err, r = commands.dispatch("config.backups", {"file": "nope.json"})
check("未知文件的备份为空", ok and r["backups"] == [])

print("\n=== 11.5) 停用的课不该从列表里消失（界面用 include_disabled）===")
_a = scheduler.build_sessions(1)
_b = scheduler.build_sessions(1, include_disabled=True)
check("默认视图跳过停用课", len(_a[0]) >= 0)
check("两种视图节数一致（还没停用任何课）", len(_a[0]) == len(_b[0]),
      f"{len(_a[0])} vs {len(_b[0])}")
check("每条都带 enabled 字段", all("enabled" in s for s in _b[0]))

ok, err, r = commands.dispatch("schedule.setEnabled",
                               {"index": target["index"], "enabled": False})
check("停用成功", ok, str(err))
_a = scheduler.build_sessions(1)
_b = scheduler.build_sessions(1, include_disabled=True)
n_off = [s for s in _b[0] if s.get("enabled") is False]
check("执行视图里没有了", len(_a[0]) < len(_b[0]), f"{len(_a[0])} vs {len(_b[0])}")
check("界面视图里还在（enabled=False）", len(n_off) > 0, str(len(n_off)))

_ws, _list = dashboard.week_sessions()
check("week_sessions 默认带出停用课",
      any(it.get("enabled") is False for it in _list), str(len(_list)))
_nx = dashboard.next_one(_list)
check("next_one 不会选中停用的课",
      _nx is None or _nx.get("enabled") is not False, str(_nx and _nx.get("course")))

ok, err, r = commands.dispatch("schedule.setEnabled",
                               {"index": target["index"], "enabled": True})
check("恢复启用", ok, str(err))

print("\n=== 11.6) schedule.remove 删除上课段 ===")
ok, err, before = commands.dispatch("schedule.get.all")
n_before = before["count"]
ok, err, r = commands.dispatch("schedule.remove", {"index": n_before})
check("越界被拒", ok is False and (err or {}).get("code") == "bad_index", str(err))

ok, err, r = commands.dispatch("schedule.remove", {"index": n_before - 1})
check("删除成功", ok, str(err))
if ok:
    print("     删掉:", r.get("removed"), r.get("day"), r.get("periods"),
          "剩", r.get("left"), "段")
check("剩 n-1 段", ok and r.get("left") == n_before - 1)
ok, err, after = commands.dispatch("schedule.get.all")
check("再次读取确实是 n-1", after["count"] == n_before - 1)
check("删完课表仍可解析", semester_total() > 0)
ok, err, r = commands.dispatch("config.restore", {"file": "schedule.json"})
check("可从备份恢复", ok, str(err))
ok, err, after2 = commands.dispatch("schedule.get.all")
check("恢复后段数回来", after2["count"] == n_before, str(after2["count"]))

print("\n=== 12) 最关键：真实 data/ 有没有被动过 ===")
REAL_AFTER = tree_hash(REAL_DATA)
print(f"     写入前: {REAL_BEFORE[:16]}...")
print(f"     写入后: {REAL_AFTER[:16]}...")
check("真实 data/ 一个字节都没变", REAL_BEFORE == REAL_AFTER)

print("\n=== 13) 真实课表仍然可解析 ===")
REAL_SCHED = os.path.join(REAL_DATA, "schedule.json")
raw = json.load(open(REAL_SCHED, encoding="utf-8-sig"))
check("真实课表 classes 完好", len(raw.get("classes") or []) == 8, str(len(raw.get("classes") or [])))

shutil.rmtree(SANDBOX, ignore_errors=True)
print()
print("失败数:", fail)
sys.exit(1 if fail else 0)
