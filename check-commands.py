# -*- coding: utf-8 -*-
"""命令总线冒烟测试：只读，不启动/停止任何进程。"""
import json
import sys

sys.path.insert(0, "app")
import paths

paths.setup_env()
import commands

fail = 0


def check(label, cond, extra=""):
    global fail
    if cond:
        print(f"  OK   {label}")
    else:
        fail += 1
        print(f"  FAIL {label} {extra}")


print("=== 1) 注册表 ===")
names = [c["name"] for c in commands.list_commands()]
print("  已注册:", ", ".join(names))
check("commands.list 存在", "commands.list" in names)
check("state.get 存在", "state.get" in names)
check("命令数 >= 10", len(names) >= 10, f"(实际 {len(names)})")

print("=== 2) commands.list ===")
ok, err, data = commands.dispatch("commands.list")
check("返回 ok", ok, str(err))
check("返回是列表", isinstance(data, list))
check("每项有 name/desc/args", all({"name", "desc", "args"} <= set(x) for x in data))

print("=== 3) state.get（与页面同一份数据）===")
ok, err, data = commands.dispatch("state.get")
check("返回 ok", ok, str(err))
if ok:
    need = {"now", "week", "running", "pid", "status", "heartbeat", "next", "sessions", "frame"}
    check("字段齐全", need <= set(data), f"缺 {need - set(data)}")
    print("     running =", data.get("running"), " week =", data.get("week"),
          " sessions =", len(data.get("sessions") or []))
    print("     heartbeat =", data.get("heartbeat"))
    try:
        json.dumps(data, ensure_ascii=False)
        check("可 JSON 序列化", True)
    except Exception as e:
        check("可 JSON 序列化", False, str(e))

print("=== 4) log.tail ===")
ok, err, data = commands.dispatch("log.tail", {"n": 3})
check("返回 ok", ok, str(err))
check("行数是 3", ok and len(data.get("lines") or []) == 3)
print("     最后一行:", (data.get("lines") or [""])[-1][:70] if ok else "")

print("=== 5) frame.info ===")
ok, err, data = commands.dispatch("frame.info")
check("返回 ok", ok, str(err))
check("不含 path（不对外暴露路径）", ok and "path" not in data, str(data))
print("     ", data)

print("=== 6) schedule.get ===")
ok, err, data = commands.dispatch("schedule.get")
check("返回 ok", ok, str(err))
check("有 semesterStart", ok and data.get("semesterStart") == "2026-09-14")
check("有 classes", ok and isinstance(data.get("classes"), list))

print("=== 7) course.list ===")
ok, err, data = commands.dispatch("course.list")
check("返回 ok", ok, str(err))
if ok:
    print("     课程数:", data.get("count"))
    for c in data.get("courses", [])[:3]:
        print(f"       {c['courseId']} {c['name']} inSchedule={c['inSchedule']}")
    check("至少一门在课表里", any(c["inSchedule"] for c in data.get("courses", [])))

print("=== 8) autowatch.status（只读）===")
ok, err, data = commands.dispatch("autowatch.status")
check("返回 ok", ok, str(err))
print("     ", data)

print("=== 9) 错误处理 ===")
ok, err, data = commands.dispatch("no.such.command")
check("未知命令 -> ok=False", ok is False)
check("code=unknown_command", err.get("code") == "unknown_command", str(err))
check("列出可用命令", isinstance(err.get("available"), list))

ok, err, data = commands.dispatch("log.tail", {"n": "abc"})
check("参数非法 -> bad_args", ok is False and err.get("code") == "bad_args", str(err))

print()
print("失败数:", fail)
sys.exit(1 if fail else 0)
