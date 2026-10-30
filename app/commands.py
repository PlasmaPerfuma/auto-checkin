# -*- coding: utf-8 -*-
r"""
命令总线：把「功能」统一注册成命令，界面和（将来的）AI 都只通过它调用。

为什么要这一层（2026-10-23 与用户定案）：
    以前界面直接对着零散接口（/api/state、/api/start…），加一个功能就要在界面里
    写一套专属调用逻辑，业务代码也跟着改。有了总线之后：
      · 加功能 = 注册一条命令；界面只多一个入口，**核心逻辑不动**；
      · AI 与界面变成【同级客户端】——AI 只产出 {"name": ..., "args": {...}}，
        走同一通道，不需要知道界面长什么样。
      · 这正是用户要的「可再开发性」：未来接自动读课表、自动规划签到、AI 指令，
        都是往这里加命令，而不是重写架构。

约定（新命令必须遵守）：
    · 命令名用 `域.动作`，如 `autowatch.start`、`log.tail`。
    · 处理函数签名 `fn(args: dict) -> 任意可 JSON 序列化值`；参数缺失/非法就抛 CommandError。
    · **命令里不 print、不 sys.exit、不开窗口**——副作用只限于它声明的领域，
      表现交给调用方（界面弹 toast / AI 读返回值）。
    · 需要写文件的命令必须自带校验；本文件第一批只做【只读 + 进程控制】，
      写配置（改课表、改坐标）留到下一批，因为那要同时改 scheduler 的语义。
"""


class CommandError(Exception):
    """命令的可预期失败（参数不对、前置条件不满足）。带一个稳定的 code 给调用方判断。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# name -> {"fn": callable, "desc": str, "args": {argName: 说明}}
REGISTRY = {}


def command(name, desc="", args=None):
    """注册一个命令。args 只用于自我描述（给界面做表单、给 AI 看能力清单）。"""

    def deco(fn):
        REGISTRY[name] = {"fn": fn, "desc": desc, "args": args or {}}
        return fn

    return deco


def _d():
    """延迟拿 dashboard 模块（它 import 本文件，模块级再 import 会成环）。"""
    import dashboard

    return dashboard


def list_commands():
    """给界面/AI 看的「我有哪些能力」。"""
    out = []
    for name in sorted(REGISTRY):
        meta = REGISTRY[name]
        out.append({"name": name, "desc": meta["desc"], "args": meta["args"]})
    return out


def dispatch(name, args=None):
    """统一入口。返回 (ok, error, data)。

    ok=False 时 error 是 {"code": ..., "message": ...}，data 为 None。
    任何异常都被收成 error，绝不让服务端 500——界面只要看 ok 就知道怎么办。
    """
    args = args or {}
    meta = REGISTRY.get(name)
    if meta is None:
        return False, {
            "code": "unknown_command",
            "message": f"没有这个命令：{name}",
            "available": sorted(REGISTRY),
        }, None
    try:
        return True, None, meta["fn"](args)
    except CommandError as e:
        return False, {"code": e.code, "message": e.message}, None
    except Exception as e:
        return False, {"code": "internal", "message": f"{type(e).__name__}: {e}"}, None


# ------------------------------------------------------------------ 元信息
@command("commands.list", "列出所有可用命令（界面/AI 的能力自查）")
def _commands_list(args):
    return list_commands()


# ------------------------------------------------------------------ 状态
@command("state.get", "一次取全：运行状态 / 心跳 / 下一节 / 本周课表 / 最新画面")
def _state_get(args):
    d = _d()
    pid = d.read_json_text(d.PID_FILE)
    wk, sessions = d.week_sessions()
    nx = d.next_one(sessions)
    st = d.read_json(d.STATUS_JSON, {}) or {}
    # 心跳过期 = 进程很可能已经死了（PID 文件残留时也能看出来）
    try:
        hb_age = d.time.time() - float(st.get("ts") or 0)
    except Exception:
        hb_age = None
    heartbeat = {
        "alive": d.pid_alive(pid),
        "heartbeatAge": round(hb_age, 1) if hb_age is not None else None,
        "stale": hb_age is not None and hb_age > 180,
        # 界面可以据此区分「在干活 / 只是休眠 / 真死了」：
        # sleeping/waiting 是正常状态，stale 才是真的可疑。
        "phase": st.get("state") or "",
    }
    # 上一节的结论：没签上时，界面要能直接告诉用户"为什么"
    # （路径走 dashboard 里已 import 的 paths，本文件刻意不引 os）
    last_session = d.read_json(d.paths.LOGS + "/session-result.json", None)
    history = d.read_json(d.paths.LOGS + "/session-history.json", []) or []
    if isinstance(history, list):
        history = history[-10:]
    else:
        history = []
    return {
        "now": d.dt.datetime.now().isoformat(timespec="seconds"),
        "week": wk,
        "running": d.pid_alive(pid),
        "pid": pid,
        "status": st,
        "heartbeat": heartbeat,
        "next": nx,
        "sessions": sessions,
        "frame": d.frame_info(),
        "lastSession": last_session,
        "history": history,
    }


@command("autowatch.status", "只看常驻签到的进程状态", {"": "无参数"})
def _aw_status(args):
    d = _d()
    pid = d.read_json_text(d.PID_FILE)
    alive = d.pid_alive(pid)
    return {"running": alive, "pid": pid}


@command("autowatch.start", "启动常驻签到后台进程")
def _aw_start(args):
    d = _d()
    ok, msg = d.start_autowatch()
    if not ok:
        raise CommandError("start_failed", msg)
    return {"message": msg}


@command("autowatch.stop", "停止常驻签到后台进程")
def _aw_stop(args):
    d = _d()
    ok, msg = d.stop_autowatch()
    if not ok:
        raise CommandError("stop_failed", msg)
    return {"message": msg}


@command("autowatch.restart", "重启常驻签到（改完配置后用）")
def _aw_restart(args):
    d = _d()
    d.stop_autowatch()
    d.time.sleep(1.5)
    ok, msg = d.start_autowatch()
    if not ok:
        raise CommandError("restart_failed", msg)
    return {"message": msg}


# ------------------------------------------------------------------ 日志 / 画面
@command("log.tail", "取最近若干行日志", {"n": "行数，默认 160，上限 2000"})
def _log_tail(args):
    d = _d()
    try:
        n = int(args.get("n") or 160)
    except Exception:
        raise CommandError("bad_args", "n 必须是整数")
    n = max(1, min(2000, n))
    return {"lines": d.tail(d.LOG, n)}


@command("frame.info", "最新抓到的签到画面元信息（是否新鲜/过期/无图）")
def _frame_info(args):
    d = _d()
    fi = d.frame_info()
    # path 是服务端内部用的，不往界面暴露
    return {k: v for k, v in fi.items() if k != "path"}


# ------------------------------------------------------------------ 课表 / 课程
@command("schedule.get", "本周课表模板（semesterStart + 每周重复的 classes）")
def _schedule_get(args):
    d = _d()
    return d.read_json(d.paths.SCHEDULE, {}) or {}


@command("course.list", "所有已知课程（courseId/toolId/名称），并标出是否已排进课表")
def _course_list(args):
    d = _d()
    raw = d.read_json(d.paths.COURSES, []) or []
    items = raw if isinstance(raw, list) else list(raw.values())
    sched = d.read_json(d.paths.SCHEDULE, {}) or {}
    in_sched = set()
    for c in (sched.get("classes") or []):
        try:
            in_sched.add(int(c.get("courseId")))
        except Exception:
            pass
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            cid = int(it.get("courseId"))
        except Exception:
            cid = None
        out.append({
            "courseId": cid,
            "toolId": it.get("toolId"),
            "name": it.get("name") or "",
            "code": it.get("code") or "",
            "inSchedule": cid in in_sched,
        })
    return {"courses": out, "count": len(out)}


# ==================================================================
# 以下是【会写文件】的命令。
# 统一走 _safe_write + 事后自检：任何一步不对就自动回滚，绝不留半套配置。
# ==================================================================

def _sched():
    import scheduler

    return scheduler


def _verify_schedule():
    """写完自检：把整个学期 1~16 周都真实构建一遍。

    只要有一周抛异常（文件不可解析）或者整学期一节课都没有，就判定写坏了。
    scheduler.load_json 在文件缺失时会 sys.exit，所以必须连 SystemExit 一起接。
    """
    sc = _sched()
    total = 0
    for wk in range(1, 17):
        try:
            res = sc.build_sessions(wk)
        except SystemExit:
            raise CommandError("verify_failed",
                               "写后自检失败：课表文件无法解析（scheduler 直接退出）")
        except Exception as e:
            raise CommandError("verify_failed",
                               f"写后自检失败：{type(e).__name__}: {e}")
        ss = res[0] if isinstance(res, tuple) else res
        total += len(ss or [])
    if total == 0:
        raise CommandError("verify_failed", "写后自检失败：整个学期一节课都没有")
    return total


def _safe_write_schedule(obj):
    """原子写 schedule.json → 自检 → 不过就恢复备份。返回备份名。"""
    import config_store

    d = _d()
    bak = config_store.write_json(d.paths.SCHEDULE, obj)
    try:
        _verify_schedule()
    except CommandError:
        try:
            config_store.restore(d.paths.SCHEDULE)
        except Exception:
            pass
        raise
    return bak


def _classes_or_fail():
    import config_store

    d = _d()
    sched = config_store.read(d.paths.SCHEDULE, {})
    if not isinstance(sched, dict) or not isinstance(sched.get("classes"), list):
        raise CommandError("bad_schedule", "schedule.json 结构不对（没有 classes 数组）")
    return sched


def _pick_class(classes, index):
    try:
        i = int(index)
    except Exception:
        raise CommandError("bad_args", "index 必须是整数")
    if i < 0 or i >= len(classes):
        raise CommandError("bad_index", f"index 越界：0~{len(classes) - 1}")
    return i


@command("settings.get", "取用户可调设置（坐标 / 截图间隔 / 课后继续盯）")
def _settings_get(args):
    import settings

    return settings.load()


@command("settings.set", "改设置（深合并且严格校验；坐标必须落在闵行校区内）",
         {"checkin": "{lat,lng,address}", "watch": "{sampleGap,postMin}"})
def _settings_set(args):
    import config_store
    import settings

    if not isinstance(args, dict) or not args:
        raise CommandError("bad_args", "至少要给一项要改的配置")
    try:
        clean, bak = settings.save(args)
    except config_store.ConfigError as e:
        raise CommandError(e.code, e.message)
    return {"settings": clean, "backup": bak}


@command("schedule.get.all", "取完整课表（含每个上课段的 enabled 状态与下标）")
def _schedule_get_all(args):
    import config_store

    d = _d()
    sched = config_store.read(d.paths.SCHEDULE, {}) or {}
    classes = []
    for i, c in enumerate(sched.get("classes") or []):
        if not isinstance(c, dict):
            continue
        item = dict(c)
        item["index"] = i
        item["enabled"] = item.get("enabled", True) is not False
        classes.append(item)
    return {"semesterStart": sched.get("semesterStart"), "classes": classes,
            "count": len(classes)}


@command("schedule.setEnabled", "启用/停用某个上课段（课表 classes 的下标）",
         {"index": "classes 下标", "enabled": "true/false"})
def _schedule_set_enabled(args):
    sched = _classes_or_fail()
    classes = sched["classes"]
    i = _pick_class(classes, args.get("index"))
    if "enabled" not in args:
        raise CommandError("bad_args", "缺少 enabled")
    en = bool(args.get("enabled"))
    c = dict(classes[i])
    c["enabled"] = en
    classes[i] = c
    bak = _safe_write_schedule(sched)
    return {"index": i, "enabled": en,
            "course": c.get("course"), "day": c.get("day"),
            "backup": bak}


ALLOWED_CLASS_FIELDS = {"course", "courseId", "toolId", "day", "periods",
                        "weeks", "room", "enabled",
                        # 每门课自定义签到地点（可选，缺省用全局设置）
                        "lat", "lng", "address"}


@command("schedule.update", "改某个上课段的字段（day/periods/weeks/room/course 等）",
         {"index": "classes 下标", "patch": "要改的字段对象"})
def _schedule_update(args):
    sched = _classes_or_fail()
    classes = sched["classes"]
    i = _pick_class(classes, args.get("index"))
    patch = args.get("patch")
    if not isinstance(patch, dict) or not patch:
        raise CommandError("bad_args", "patch 必须是非空对象")
    bad = set(patch) - ALLOWED_CLASS_FIELDS
    if bad:
        raise CommandError("bad_field", f"不允许改这些字段：{sorted(bad)}")

    merged = dict(classes[i])
    merged.update(patch)
    _validate_class(merged)
    classes[i] = merged
    bak = _safe_write_schedule(sched)
    return {"index": i, "class": merged, "backup": bak}


@command("schedule.remove", "从课表里彻底删掉一个上课段（不在监控范围内了）",
         {"index": "classes 下标"})
def _schedule_remove(args):
    import config_store

    d = _d()
    sched = _classes_or_fail()
    classes = sched["classes"]
    i = _pick_class(classes, args.get("index"))
    gone = dict(classes[i])
    if len(classes) <= 1:
        # 删光会导致 schedule.json 里 classes 为空 → 自检的"整学期 0 节课"会触发回滚，
        # 不如在这里直接说清楚，别让用户以为操作失败了。
        raise CommandError("last_one", "这是课表里的最后一段，不能删（否则整个课表就空了）")
    del classes[i]
    bak = _safe_write_schedule(sched)
    return {"removed": gone.get("course"), "day": gone.get("day"),
            "periods": gone.get("periods"), "left": len(classes), "backup": bak}


# ------------------------------------------------------------ 首次配置三步
# 用户要把这个工具发给朋友，朋友卡在三件没有界面的事上：
# 填凭据 / 发现课程 ID / 把课加进课表。这三个命令就是那三步的后端。

@command("credentials.get", "看凭据填了没（不回传密码本身）")
def _credentials_get(args):
    import os

    d = _d()
    p = os.path.join(d.paths.DATA, "credentials.json")
    out = {"fileExists": os.path.isfile(p), "username": "", "hasPassword": False}
    if out["fileExists"]:
        try:
            import json as _json

            data = _json.load(open(p, encoding="utf-8"))
            out["username"] = str(data.get("username") or "").strip()
            out["hasPassword"] = bool(str(data.get("password") or "").strip())
        except Exception:
            out["broken"] = True
    return out


@command("credentials.set", "填 jAccount 学号与密码（写进 data/credentials.json）",
         {"username": "学号或 jAccount 用户名", "password": "密码"})
def _credentials_set(args):
    import json as _json
    import os

    d = _d()
    u = str(args.get("username") or "").strip()
    p = str(args.get("password") or "")
    if not u or not p.strip():
        raise CommandError("bad_args", "学号和密码都要填")
    os.makedirs(d.paths.DATA, exist_ok=True)
    path = os.path.join(d.paths.DATA, "credentials.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump({"username": u, "password": p}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)   # 原子替换，不会留半截文件
    return {"username": u, "saved": True}


@command("courses.list", "列出已发现的课程（data/courses.json）")
def _courses_list(args):
    import json as _json
    import os

    d = _d()
    p = os.path.join(d.paths.DATA, "courses.json")
    arr = []
    try:
        arr = _json.load(open(p, encoding="utf-8"))
    except Exception:
        arr = []
    if not isinstance(arr, list):
        arr = []
    return {"courses": arr, "count": len(arr)}


@command("courses.discover", "重新发现课程 ID（会开浏览器登录，约 40 秒）")
def _courses_discover(args):
    """跑 discover_courses.py，把输出带回界面。

    为什么不放在请求线程里直接 import：那个脚本会起 Playwright（还要登录），
    单独起一个进程最稳，挂了也只挂它自己。
    """
    import os
    import subprocess
    import sys

    d = _d()
    if d.paths.FROZEN:
        cmd = [sys.executable, "--internal-run", "discover_courses.py"]
    else:
        cmd = [d.PYEXE, os.path.join(d.paths.APP_DIR, "discover_courses.py")]
    env = dict(os.environ)
    env["PYTHONPATH"] = d.paths.LIBS
    env["PYTHONIOENCODING"] = "utf-8"
    env["AUTOCHECKIN_HOME"] = d.paths.HOME_DIR
    for k in ("TEMP", "TMP", "TMPDIR"):
        env[k] = d.paths.TMP
    try:
        r = subprocess.run(cmd, cwd=d.paths.ROOT, env=env, timeout=240,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise CommandError("timeout",
                           "发现课程超时（240 秒）。多半卡在登录或浏览器起不来，"
                           "看 logs\\ 下最新日志。")
    tail = ((r.stdout or "") + (r.stderr or ""))[-1500:]
    ok, _err, data = dispatch("courses.list")
    n = (data or {}).get("count", 0) if ok else 0
    if r.returncode != 0 and n == 0:
        raise CommandError("discover_failed",
                           f"发现课程失败（退出码 {r.returncode}）：\n{tail}")
    return {"count": n, "returncode": r.returncode, "output": tail}


@command("schedule.add", "往课表里加一个上课段（首次配置/换课表用）",
         {"course": "课程名", "courseId": "Canvas 课程 id", "day": "1-7",
          "periods": "[1,2]", "weeks": "'1-16' 或 '2,4,6'", "room": "可选",
          "address": "这门课的上课地点（可选，不填用全局）", "toolId": 8329})
def _schedule_add(args):
    sched = _classes_or_fail()
    classes = sched["classes"]
    item = {}
    for k in ("course", "courseId", "toolId", "day", "periods", "weeks", "room",
              "lat", "lng", "address"):
        v = args.get(k)
        if v not in (None, ""):
            item[k] = v
    if not item.get("course"):
        raise CommandError("bad_args", "course 不能为空")
    if not item.get("courseId"):
        raise CommandError("bad_args", "courseId 不能为空")
    item.setdefault("toolId", 8329)
    item.setdefault("weeks", "1-16")
    item["enabled"] = True
    # 界面上的星期可能是 1~7 的数字，课表里存的是「周一」…
    day = item.get("day")
    if isinstance(day, int) or (isinstance(day, str) and day.strip().isdigit()):
        idx = int(day) - 1
        if 0 <= idx < len(DAYS):
            item["day"] = DAYS[idx]
    _validate_class(item)

    for c in classes:
        same = (str(c.get("course")) == str(item.get("course"))
                and c.get("day") == item.get("day")
                and [int(x) for x in (c.get("periods") or [])]
                == [int(x) for x in (item.get("periods") or [])])
        if same:
            raise CommandError("duplicate", "这个上课段已经在课表里了，不用重复添加")

    classes.append(item)
    bak = _safe_write_schedule(sched)
    return {"added": item, "count": len(classes), "backup": bak}


@command("ui.report", "前端把自己的异常报上来，写到 logs/ui-error.log",
         {"kind": "error|reject", "message": "...", "stack": "..."})
def _ui_report(args):
    """让页面能把自己的崩溃报回来。

    为什么需要：窗口是 WebView2，页面报错只显示在黑框里，我这边完全看不到，
    只能靠用户描述"白屏了/点不动"——这是最费回合的一种排查。有了这条命令，
    前端异常会落到 logs/ui-error.log，我直接读文件即可。
    """
    import datetime as _dt
    import os as _os

    import paths as _paths

    _d()   # 确认 dashboard 已就绪（本命令不需要它的数据）
    msg = str(args.get("message") or "")[:500]
    if not msg:
        raise CommandError("bad_args", "message 不能为空")
    line = args.get("line") or 0
    src = str(args.get("source") or "")[:200]
    stack = str(args.get("stack") or "")[:2000]
    try:
        _os.makedirs(_paths.LOGS, exist_ok=True)
        p = _os.path.join(_paths.LOGS, "ui-error.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write("-" * 60 + "\n")
            f.write(_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    + f"  [{args.get('kind') or 'error'}]  {msg}\n")
            if src or line:
                f.write(f"  at {src}:{line}\n")
            if stack:
                f.write(stack + "\n")
    except Exception as e:
        raise CommandError("write_failed", f"写日志失败：{e}")
    return {"logged": True}


DAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

def _validate_class(c):
    """严格校验一个上课段。宁可拒绝，也不写进一个 scheduler 解析不了的东西。"""
    sc = _sched()

    day = c.get("day")
    if day not in DAYS:
        raise CommandError("bad_day", f"day 必须是 {'/'.join(DAYS)}，收到 {day!r}")

    ws = c.get("weeks")
    if not isinstance(ws, str) or not ws.strip():
        raise CommandError("bad_weeks", "weeks 必须是非空字符串，如 '1-16' 或 '2,4,6'")
    wset = sc.parse_weeks(ws)
    if not wset:
        raise CommandError("bad_weeks", f"weeks 解析不出任何周次：{ws!r}")
    if min(wset) < 1 or max(wset) > 30:
        raise CommandError("bad_weeks", f"周次必须在 1~30，收到 {min(wset)}~{max(wset)}")

    ps = c.get("periods")
    if not isinstance(ps, list) or not ps:
        raise CommandError("bad_periods", "periods 必须是非空数组，如 [1, 2]")
    try:
        pnum = [int(x) for x in ps]
    except Exception:
        raise CommandError("bad_periods", "periods 里必须是整数")
    if sorted(set(pnum)) != pnum:
        raise CommandError("bad_periods", "periods 必须升序且不重复")
    if min(pnum) < 1 or max(pnum) > 14:
        raise CommandError("bad_periods", "课时编号必须在 1~14")

    if not isinstance(c.get("course"), str) or not c["course"].strip():
        raise CommandError("bad_course", "course 不能为空")
    for key in ("courseId", "toolId"):
        if c.get(key) is not None:
            try:
                int(c[key])
            except Exception:
                raise CommandError("bad_ids", f"{key} 必须是整数")

    # 每门课可以自定义签到地点（可选）。不填就用「设置」里的全局坐标。
    # 这里只做"是合法数值"的检查，不强求落在闵行——万一某门课在校外上课。
    if c.get("lat") not in (None, ""):
        try:
            v = float(c["lat"])
        except Exception:
            raise CommandError("bad_lat", "lat 必须是数字")
        if not (-90 <= v <= 90):
            raise CommandError("bad_lat", "lat 必须在 -90~90")
    if c.get("lng") not in (None, ""):
        try:
            v = float(c["lng"])
        except Exception:
            raise CommandError("bad_lng", "lng 必须是数字")
        if not (-180 <= v <= 180):
            raise CommandError("bad_lng", "lng 必须在 -180~180")
    if c.get("address") not in (None, ""):
        if not isinstance(c["address"], str) or len(c["address"]) > 80:
            raise CommandError("bad_address", "address 必须是不超过 80 字的文本")
    return True


@command("schedule.validate", "只校验不写入（界面可以在提交前先试一下）",
         {"classes": "可选：要试的完整 classes 数组"})
def _schedule_validate(args):
    sched = _classes_or_fail()
    classes = args.get("classes") if isinstance(args.get("classes"), list) else sched["classes"]
    for i, c in enumerate(classes):
        if not isinstance(c, dict):
            raise CommandError("bad_args", f"第 {i} 项不是对象")
        try:
            _validate_class(c)
        except CommandError as e:
            raise CommandError(e.code, f"第 {i} 项：{e.message}")
    sc = _sched()
    total = 0
    for wk in range(1, 17):
        try:
            res = sc.build_sessions(wk)
        except SystemExit:
            raise CommandError("bad_schedule", "现有课表文件无法解析")
        ss = res[0] if isinstance(res, tuple) else res
        total += len(ss or [])
    return {"ok": True, "classes": len(classes), "sessionsInSemester": total}


@command("config.backups", "列出某个配置文件的备份（schedule.json / settings.json 等）",
         {"file": "文件名，默认 schedule.json"})
def _config_backups(args):
    import os

    import config_store
    import paths

    name = args.get("file") or "schedule.json"
    if "/" in name or "\\" in name or ".." in name:
        raise CommandError("bad_args", "file 只给文件名")
    path = os.path.join(paths.DATA, name)
    return {"file": name, "backups": config_store.list_backups(path)}


@command("config.restore", "从备份恢复配置文件（name 留空取最近一份）",
         {"file": "文件名，默认 schedule.json", "name": "备份名，可选"})
def _config_restore(args):
    import os

    import config_store
    import paths

    name = args.get("file") or "schedule.json"
    if "/" in name or "\\" in name or ".." in name:
        raise CommandError("bad_args", "file 只给文件名")
    path = os.path.join(paths.DATA, name)
    try:
        used = config_store.restore(path, args.get("name") or None)
    except config_store.ConfigError as e:
        raise CommandError(e.code, e.message)
    if name == "schedule.json":
        _verify_schedule()   # 恢复回来的东西也要能解析，否则立刻报出来
    return {"file": name, "restored": used}
