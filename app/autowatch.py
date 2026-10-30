# -*- coding: utf-8 -*-
"""
autowatch —— 长期监控、到点自动签到（方案 B：平时只等时间，有课才开浏览器）。

设计：
  · 平时 = 一个 ~20MB 的 Python 进程，只做「看时间 + 算下一节」；
  · 进入某节课的监视窗口（课前 5 分钟 → 课后 10 分钟）才启动 Playwright；
  · 窗口内**全程**每秒截一张屏幕流解码，签到成功（resultCode=200）立刻停止本节；
  · 窗口结束还没成功 → 关浏览器，记一条未签到，等下一节。

用法：
    python app/autowatch.py --run          # 常驻（后台用 run-autowatch.cmd）
    python app/autowatch.py --status       # 看是否在跑、下一节是什么
    python app/autowatch.py --stop         # 停止
    python app/autowatch.py --once         # 立刻跑当前/最近一节课（调试用）
    python app/autowatch.py --dry-run      # 只截不交
"""
import argparse
import datetime as dt
import json
import os
import random
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import paths                       # 路径真源

paths.setup_env()                  # 必须在 import playwright 之前

from playwright.sync_api import sync_playwright   # noqa: E402

import capture_checkin as cap      # 复用已验证的抓码/解码/提交逻辑  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PID_FILE = os.path.join(paths.LOGS, "autowatch.pid")
STATUS_FILE = os.path.join(paths.LOGS, "autowatch-status.json")
LOG_FILE = os.path.join(paths.LOGS, "autowatch.log")

PRE_MIN = 5        # 课前提前多久开始盯
GOTO_ATTEMPTS = 40  # 打开课程页最多试几次（断网时在这里等，别让外层重开浏览器）
GOTO_RETRY_SEC = 30  # 每次重试间隔

# 用户可调项从 data/settings.json 读；**文件不存在/坏了就回落默认值**，
# 签到不会因为缺这个文件而跑不起来。
try:
    import settings as _settings

    _S = _settings.load()
except Exception:
    _S = {"checkin": {"lat": "31.0269", "lng": "121.4335",
                      "address": "上海市闵行区东川路800号上海交通大学"},
          "watch": {"sampleGap": 0.6, "postMin": 10}}

POST_MIN = int(_S["watch"]["postMin"])     # 课后继续盯多久
SAMPLE_GAP = float(_S["watch"]["sampleGap"])  # 两次截图之间歇多久（秒）
LAT = str(_S["checkin"]["lat"])
LNG = str(_S["checkin"]["lng"])
ADDRESS = _S["checkin"]["address"]

# 实时画面：监视期间每 LIVE_EVERY 秒把当前截图覆盖写到这个文件，
# 控制台的 /api/frame 就能显示"它现在在看什么"。
# （以前只在抓到码时存一张 autowatch-qr.png，所以监视的 100 多分钟里画面区一直是空的）
# LIVE_EVERY 由 settings.json 的 watch.liveEvery 控制，0 = 关闭（不写任何实时帧）。
LIVE_FRAME = os.path.join(paths.OUTPUT, "autowatch-live.png")
LIVE_EVERY = float(_S["watch"].get("liveEvery", 5) or 0)


class _WindowAbort(Exception):
    """本窗口没法继续（断网 / 未登录），直接放弃本节，别让主循环重开一轮。"""


# ---------------------------------------------------------------- 日志
def log(msg):
    line = f"[{dt.datetime.now():%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def write_status(**kv):
    try:
        old = {}
        if os.path.isfile(STATUS_FILE):
            old = json.load(open(STATUS_FILE, encoding="utf-8"))
        old.update(kv)
        old["updated"] = dt.datetime.now().isoformat(timespec="seconds")
        old["ts"] = int(time.time())
        json.dump(old, open(STATUS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


SESSION_RESULT = os.path.join(paths.LOGS, "session-result.json")
# 最近 30 节的结论历史（"这周哪几节没签上"要看它，不能只看最后一节）
SESSION_HISTORY = os.path.join(paths.LOGS, "session-history.json")


def _diagnose(got, reason, rounds, qr_seen, submit_fails, saw_video):
    """把"这一节没签上"翻译成一句人话 + 一条建议。

    存在的理由：这个工具大部分时间在没人看的时候干活，没签上时用户只能
    翻几百行日志猜。结论必须由程序自己给，而且要能区分「程序坏了」和
    「这节课本来就没发起点名」——后者会把用户引去乱改代码。
    """
    r = (reason or "").strip()
    if got:
        return ("success", "已签到", "")
    if "未登录" in r or "登录" in r:
        return ("not_logged_in", "没能登录 Canvas（自动登录也失败了）",
                "检查 data\\credentials.json 里的学号/密码是否正确")
    if "课堂视频页" in r:
        return ("no_video_page", "「课堂视频新版」页面没能打开",
                "可能是没登录，也可能直播页结构变了")
    if "课程页" in r:
        return ("network", "课程页一直打不开（网络或 Canvas 站点问题）",
                "多半是校园网调整或断网；网络恢复后不会自动补签")
    if not saw_video:
        return ("no_video", "没找到「电脑屏幕」那一路画面",
                "可能这节课没直播，或者页面结构变了")
    if qr_seen and submit_fails:
        return ("submit_failed", "看到了签到码，但提交失败：%s" % submit_fails[-1],
                "码可能已过期，或接口返回了业务错误")
    if qr_seen:
        return ("qr_no_submit", "看到了签到码，但没走到提交这一步",
                "看日志里的 token / 网络相关报错")
    return ("no_qr", "盯了 %d 轮，画面里一直没出现签到码" % rounds,
            "最可能是这节课没发起点名——不是程序故障")


def notify(title, text, kind="ok"):
    """弹一张系统通知卡（独立 PowerShell 进程）。

    为什么用独立进程：这个守护进程是 pythonw（无控制台、无 UI 线程），
    自己画不了窗口；起一个短命的 powershell 最省事，失败也伤不到签到。
    """
    try:
        ps1 = os.path.join(paths.APP_DIR, "notify.ps1")
        if not os.path.isfile(ps1):
            return
        flags = 0x08000000 | 0x00000008   # CREATE_NO_WINDOW | DETACHED_PROCESS
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", ps1, "-Title", title, "-Text", text, "-Kind", kind],
            creationflags=flags, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
    except Exception:
        pass


def write_session_result(course, course_id, window, got, reason, rounds,
                         qr_seen, submit_fails, saw_video):
    """每一节结束都落一份结论，控制台读它显示「上一节为什么没签上」。"""
    try:
        code, why, advice = _diagnose(got, reason, rounds, qr_seen,
                                      submit_fails, saw_video)
        rec = {
            "at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "course": course,
            "courseId": str(course_id),
            "window": window,
            "ok": bool(got),
            "code": code,
            "why": why,
            "advice": advice,
            "rounds": rounds,
            "qrSeen": qr_seen,
        }
        json.dump(rec, open(SESSION_RESULT, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        # 再往历史里追加一条（只留最近 30 条）：
        # 只看"最后一节"不够用——想回答"这周到底哪几节没签上"就得有历史。
        try:
            hist = []
            if os.path.isfile(SESSION_HISTORY):
                try:
                    hist = json.load(open(SESSION_HISTORY, encoding="utf-8"))
                except Exception:
                    hist = []
            if not isinstance(hist, list):
                hist = []
            hist.append(rec)
            hist = hist[-30:]
            json.dump(hist, open(SESSION_HISTORY, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        except Exception:
            pass
        if got:
            log("  结论：已签到")
        else:
            log(f"  结论：没签上 —— {why}")
            if advice:
                log(f"        建议：{advice}")
        return rec
    except Exception:
        return None


def heartbeat():
    """常驻进程每分钟刷一次，让控制台能判断"还活着吗"。
    2026-10-22 教训：进程悄悄死了，状态文件还写着 watching，看日志才发现。"""
    try:
        old = {}
        if os.path.isfile(STATUS_FILE):
            old = json.load(open(STATUS_FILE, encoding="utf-8"))
        old["ts"] = int(time.time())
        old["alive"] = True
        json.dump(old, open(STATUS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


# ---------------------------------------------------------------- 进程控制
def read_pid():
    try:
        return int(open(PID_FILE).read().strip())
    except Exception:
        return None


def pid_alive(pid):
    if not pid:
        return False
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    except Exception:
        return False


# ---------------------------------------------------------------- 课表
def _need_data_files():
    """数据目录里该有的文件不在时，给一句人话而不是 traceback。

    为什么会缺：打包后的 exe 若没带 `--home`，数据目录会回落到
    `%APPDATA%\\auto-checkin`（空的）→ 打开 schedule.json 直接抛
    FileNotFoundError → PyInstaller 的 GUI 程序把它弹成"处理失败"对话框，
    用户看到的是一大段 traceback，完全不知道是"少了个参数"。
    """
    missing = [p for p in (paths.SCHEDULE, paths.PERIODS) if not os.path.isfile(p)]
    if not missing:
        return True
    msg = ("[!] 数据目录里缺少：" + "、".join(os.path.basename(p) for p in missing)
           + "\n    数据目录 = " + paths.HOME_DIR
           + "\n    如果这是打包后的 exe，请带上 --home <项目目录> 再运行；"
           + "\n    源码模式则确认 data/periods.json 和 data/schedule.json 存在。")
    log(msg)
    print(msg, file=sys.stderr)
    return False


def today_sessions():
    """返回今天的 [(开始 datetime, 结束 datetime, session dict)]，已按时间排序。"""
    import scheduler
    sem = dt.date.fromisoformat(json.load(
        open(paths.SCHEDULE, encoding="utf-8-sig"))["semesterStart"])
    today = dt.date.today()
    wk = (today - sem).days // 7 + 1
    if wk < 1:
        return []
    sessions, _skipped, _sem = scheduler.build_sessions(wk)
    # scheduler 给的 watch 已经是「课前 pre_min ~ 课后 post_min」，
    # 所以起点直接用，终点按我们要的 POST_MIN 补差额，别重复叠加。
    try:
        post_min = json.load(open(paths.PERIODS, encoding="utf-8-sig")) \
            .get("watch", {}).get("post_min", 5)
    except Exception:
        post_min = 5
    extra = POST_MIN - post_min
    out = []
    for s in sessions:
        if s["date"] != today or not s.get("courseId"):
            continue
        start = s["watch"][0]
        end = s["watch"][1] + dt.timedelta(minutes=extra)
        out.append((start, end, s))
    out.sort(key=lambda x: x[0])
    return out


def next_session(now=None):
    now = now or dt.datetime.now()
    for a, b, s in today_sessions():
        if b > now:
            return a, b, s
    return None


# ---------------------------------------------------------------- 提交
def submit(info, token, dry_run=False, place=None):
    """返回 (成功?, 说明)。成功判据 = resultCode == 200。

    place：这门课自己的签到地点（课表里可选的 lat/lng/address）。没给就用全局设置。
    """
    sid = info["signHistoryId"]
    rtok = info["rollCallToken"]
    course = info.get("courseCode") or ""
    place = place or {}
    try:
        lat0 = float(place.get("lat") or LAT)
        lng0 = float(place.get("lng") or LNG)
    except Exception:
        lat0, lng0 = float(LAT), float(LNG)
    addr = str(place.get("address") or ADDRESS)
    lat = lat0 + random.uniform(-0.0002, 0.0002)
    lng = lng0 + random.uniform(-0.0002, 0.0002)

    def _send(label, path, params):
        """发一次请求，返回 (成功?, 说明)。成功判据 = resultCode == 200。"""
        st, body = cap.api_get(path, params, token)
        log(f"    试 {label} → HTTP {st}")
        if body:
            log(f"      返回: {body[:260]}")
        code = None
        try:
            code = str(json.loads(body).get("resultCode"))
        except Exception:
            pass
        if code == "200":
            if dry_run:
                return True, f"{label} 返回 200（dry-run 未提交）"
            return True, f"{label} 返回 200"
        return False, ""

    # ⭐ 优先走**带定位**的版本（用户要求：尽可能让服务器那边留下带位置/地址的记录）。
    # 网络抖一下、或服务端偶发 50000，不要立刻退化成"不带定位"，先多试两次。
    gis_params = {"lat": f"{lat:.6f}", "lng": f"{lng:.6f}", "address": addr}
    for i in range(1, 4):
        ok, note = _send(f"带定位 /sign/scan/gis（第 {i} 次）",
                         f"/sign/scan/gis/{sid}", gis_params)
        if ok:
            return True, note
        if i < 3:
            time.sleep(0.8)

    # 兜底：带定位确实不行才用不带定位（这条没有位置信息，尽量少走）
    log("    [!] 带定位那一路没成功，退回不带定位（这次不会留位置记录）")
    ok, note = _send("不带定位 /sign/scan", f"/sign/scan/{rtok}/{sid}", {})
    if ok:
        return True, note
    return False, "两种方式都没返回 200"


def _has_course_nav(page):
    """页面里有没有「课堂视频新版」这个导航链接；没有多半是没登录。"""
    try:
        loc = page.get_by_role("link", name="课堂视频新版")
        return loc.count() > 0
    except Exception:
        return False


def _relogin(page, ctx):
    """复用 sjtu_login 的登录逻辑。成功返回 True。"""
    try:
        import sjtu_login
        creds = sjtu_login.load_creds()
        if not creds:
            log("  [!] credentials.json 里没有账号密码")
            return False
        ok = sjtu_login.do_login(page, ctx, creds[0], creds[1])
        return bool(ok)
    except Exception as e:
        log(f"  [!] 自动登录异常 {type(e).__name__}: {str(e)[:120]}")
        return False


def _minimize_window(page):
    """把有头浏览器窗口最小化：不挡视线，但仍在任务栏能看到。
    不最小化的话，直播流被遮挡时 Chromium 可能降频，反而影响解码。"""
    try:
        import ctypes
        import ctypes.wintypes as wt
        u32 = ctypes.windll.user32
        # 通过 CDP 拿到窗口句柄
        cdp = page.context.new_cdp_session(page)
        info = cdp.send("Browser.getWindowForTarget")
        hwnd = int(info.get("windowId") or 0)
        # windowId 不是 hwnd，改用枚举：找本进程的 Chrome 主窗口
        import subprocess
        cdp.detach()
    except Exception:
        pass
    try:
        import ctypes
        import ctypes.wintypes as wt
        u32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def cb(hwnd, _):
            if not u32.IsWindowVisible(hwnd):
                return True
            n = u32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            cls = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(hwnd, cls, 256)
            if cls.value == "Chrome_WidgetWin_1":
                found.append(hwnd)
            return True

        u32.EnumWindows(cb, 0)
        for hwnd in found:
            u32.ShowWindow(hwnd, 6)   # SW_MINIMIZE
    except Exception:
        pass


def _wait_vpage(ctx, canvas_page, timeout=90):
    """等「课堂视频」那个页面就绪。

    课堂视频是 LTI 1.3 外部工具，点开后会**新开一个标签页**跳到 v.sjtu.edu.cn。
    这里按三步找，任一步命中就返回：
      ① 有 v.sjtu.edu.cn 域名的 page；
      ② 有 <video> 元素的 page；
      ③ 标题含「课堂」「实录」的 page。
    找不到返回 None。
    """
    dl = time.time() + timeout
    last_note = 0
    while time.time() < dl:
        cands = [pg for pg in ctx.pages if not pg.is_closed()]
        for pg in cands:
            try:
                if "v.sjtu.edu.cn" in (pg.url or ""):
                    return pg
            except Exception:
                continue
        for pg in cands:
            if pg is canvas_page:
                continue
            try:
                r = pg.evaluate(cap.FIND_SCREEN_VIDEO)
                if r and r.get("idx", -1) >= 0:
                    return pg
            except Exception:
                pass
            try:
                t = pg.title() or ""
                if "课堂" in t or "实录" in t:
                    return pg
            except Exception:
                pass
        # 每 15 秒把当前 pages 快照打一次，方便排查"等不到"到底卡在哪
        if time.time() - last_note > 15:
            last_note = time.time()
            snap = []
            for pg in cands:
                try:
                    snap.append(f"{pg.url[:60]}")
                except Exception:
                    snap.append("<url err>")
            log(f"    等课堂视频页… 当前 {len(cands)} 个页面: {snap}")
        time.sleep(1.5)
    return None


def _open_class_video(ctx, page, timeout=60):
    """点「课堂视频新版」并**用 expect_page 捕获新标签页**。

    2026-10-22 教训：靠轮询 `ctx.pages` 判断新页面有没有出现是不可靠的——
    实测 click 明明成功了，`ctx.pages` 里却一直是 1 个页面，等到超时也等不到。
    Playwright 官方推荐的是 `expect_page()`：在动作期间挂上监听，页面一创建就拿到。
    返回 (vpage, err)。vpage 可能是 None。
    """
    link = page.get_by_role("link", name="课堂视频新版")
    # 情况 A：新开标签页（课堂视频是 LTI 外部工具，通常这样）
    try:
        with ctx.expect_page(timeout=timeout * 1000) as pinfo:
            link.first.click(timeout=30000)
        vpage = pinfo.value
        log(f"  已点「课堂视频新版」→ 新标签页: {vpage.url[:70]}")
    except Exception as e:
        log(f"  [!] expect_page 没拿到新页面（{type(e).__name__}），试试当前页是否已跳转")
        vpage = None
        # 情况 B：在同一个标签页里跳转（有些 LTI 配置是 replace）
        dl = time.time() + 20
        while time.time() < dl:
            try:
                if "v.sjtu.edu.cn" in (page.url or ""):
                    vpage = page
                    break
            except Exception:
                pass
            time.sleep(1)
        if vpage is None:
            # 情况 C：兜底扫一遍现有页面
            for pg in list(ctx.pages):
                if pg.is_closed():
                    continue
                try:
                    if "v.sjtu.edu.cn" in (pg.url or ""):
                        vpage = pg
                        break
                except Exception:
                    continue
    if vpage is not None:
        # 等它真正就绪（LTI 跳转 + SPA 渲染需要几秒）
        dl = time.time() + 40
        while time.time() < dl:
            try:
                if "v.sjtu.edu.cn" in (vpage.url or ""):
                    break
            except Exception:
                pass
            time.sleep(1)
    return vpage


def _try_live(page, tag):
    """点「直播」入口，并把结果（含失败原因与页面上的候选文字）写进日志。
    2026-10-22 教训：原来点了没点中是完全静默的，只在日志里留下一句
    「30 秒没找到屏幕流」，根本不知道是"页面上没有直播按钮"还是"点错了元素"。"""
    for attempt in range(1, 4):
        try:
            r = page.evaluate(cap.LIVE_JS)
        except Exception as e:
            log(f"  [!] {tag}点直播异常 {type(e).__name__}: {str(e)[:100]}")
            time.sleep(2)
            continue
        if isinstance(r, dict):
            hit = (r.get("hit") or "").strip()
            seen = r.get("seen") or []
            note = r.get("note") or ""
        else:                      # 兼容旧返回
            hit, seen, note = (r or ""), [], ""
        if hit:
            log(f"  已点「{hit}」，切到直播（{tag}）")
            return True
        if seen:
            log(f"  [!] {tag}没点中；页面上含「直播」的元素有: {seen[:5]}")
        else:
            log(f"  [!] {tag}页面上没有任何含「直播」的可点元素（{note}）")
            # 没候选时把当前页面标题与 URL 记下来，便于判断是不是还在点播页
            try:
                log(f"      当前页面: {page.title()[:40]!r} {page.url[:100]}")
            except Exception:
                pass
        time.sleep(2)
    return False


# ---------------------------------------------------------------- 一个监视窗口
def run_window(start, end, sess, dry_run=False):
    name = sess.get("course", "?")
    cid = sess.get("courseId")
    log("=" * 62)
    log(f"开始监视：{name}（course {cid}）")
    log(f"窗口 {start:%H:%M:%S} → {end:%H:%M:%S}，全程每秒取样")
    write_status(state="watching", course=name, courseId=cid,
                 window=f"{start:%H:%M}-{end:%H:%M}")
    url = f"https://oc.sjtu.edu.cn/courses/{cid}"
    got = False
    reason = ""
    # 这几个计数在 with 块里才会被赋值；万一在更早的位置就 _WindowAbort（比如
    # 课程页打不开），结尾的诊断仍要能跑，所以先给默认值。
    rounds = 0
    qr_seen = 0
    submit_fails = []
    saw_video = False
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            # 2026-10-22：headless 下 WebRTC 直播流起不来（页面开着但 <video> 始终不存在），
            # 改成有头窗口。窗口会自动最小化——不遮挡、任务栏可见、可随时看它在干什么。
            # 用 headless=True。2026-10-22 实测：headless 下点「课堂视频新版」4 秒就开出
            # 新的 v.sjtu 标签页；改成 headless=False（有头）后 15 秒都开不出来，
            # 导致 _wait_vpage 永远等不到。两个 `viewport` 与 `no_viewport` 也不能同时给。
            paths.PROFILE, channel="chrome", headless=True, no_viewport=True,
            ignore_default_args=cap.IGNORE_DEFAULT_ARGS, args=cap.ARGS,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            # —— 打开课程页：网络不通时在这里**等**，不要抛出去让主循环重开一轮 ——
            # （2026-10-22 教训：断网那 9 分钟里，外层每 5 秒重启一次浏览器，白开上百个进程）
            ok = False
            for attempt in range(1, GOTO_ATTEMPTS + 1):
                if dt.datetime.now() >= end:
                    break
                try:
                    log(f"  打开 {url}" + (f"（第 {attempt} 次）" if attempt > 1 else ""))
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    ok = True
                    break
                except Exception as e:
                    msg = str(e)
                    if "ERR_NAME_NOT_RESOLVED" in msg or "ERR_INTERNET_DISCONNECTED" in msg \
                            or "ERR_NETWORK_CHANGED" in msg or "ERR_CONNECTION" in msg:
                        log(f"  [!] 网络不通（{msg.splitlines()[0][:70]}），{GOTO_RETRY_SEC}s 后重试")
                        # 分段睡，窗口结束就立刻退
                        napto = time.time() + GOTO_RETRY_SEC
                        while time.time() < napto and dt.datetime.now() < end:
                            time.sleep(2)
                    else:
                        log(f"  [!] 打开失败 {type(e).__name__}: {msg.splitlines()[0][:100]}")
                        time.sleep(5)
            if not ok:
                log("  窗口内一直打不开课程页（网络或站点问题），本节放弃")
                reason = "无法打开课程页"
                raise _WindowAbort(reason)

            # —— 2026-10-29 实战踩到的坑 ——
            # 登录态其实还在，但访问 /login/openid_connect 会把已登录的会话
            # **直接弹回 Canvas 首页**（URL 变成 https://oc.sjtu.edu.cn/），
            # 那里当然没有「课堂视频新版」→ 被误判成"没登录"→ 白跑一次登录
            # → 登录流程在已登录状态下必然失败 → 整节放弃。
            # 正确做法：先重新打开课程页（登录态在的话一次就进），别急着判没登录。
            for _r in range(3):
                if str(cid) in (page.url or ""):
                    break
                log(f"  页面停在 {page.url[:70]}，重新打开课程页（第 {_r + 1} 次）")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except Exception as e:
                    log(f"    重开失败 {type(e).__name__}: {str(e)[:60]}")
                time.sleep(2)

            # —— 没登录就会被跳到登录页，这时点「课堂视频新版」必然超时 ——
            logged = False
            for _r in range(3):
                if "login" not in (page.url or "").lower() and _has_course_nav(page):
                    logged = True
                    break
                time.sleep(1.5)
            if not logged:
                log("  [!] 看起来没登录（找不到课程导航），尝试自动登录…")
                if _relogin(page, ctx):
                    log("  √ 重新登录成功，重新打开课程页")
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    time.sleep(1.5)
                else:
                    log("  [X] 自动登录失败，本节放弃")
                    reason = "未登录且自动登录失败"
                    raise _WindowAbort(reason)

            # —— 关键：课堂视频是**新开的标签页**（v.sjtu.edu.cn），不是当前页 ——
            # 2026-10-22 教训：原代码一直用 ctx.pages[0]（Canvas 课程页）去点「直播」、
            # 去找 <video>，那里当然什么都没有，于是"永远切不到直播"。
            vpage = _open_class_video(ctx, page, timeout=60)
            if vpage is None:
                log("  [X] 等不到课堂视频页面")
                raise _WindowAbort("课堂视频页没打开")
            log(f"  课堂视频页已就绪: {vpage.url[:80]}")

            # 此时才最小化：页面已经开好了，最小化不会影响新 tab
            _minimize_window(vpage)

            time.sleep(1)
            _try_live(vpage, "首次")

            last_ok = time.time()
            rounds = 0
            saw_video = False
            # 监视窗口可以长达 100+ 分钟，期间必须持续打心跳，
            # 否则控制台会把「正在正常找码」误报成「进程可疑」
            # （2026-10-26 早上真实发生：日志已盯 360 轮，界面却显示疑似卡住）
            last_hb = 0.0
            last_live = 0.0      # 上次写实时画面的时间
            qr_seen = 0          # 一共看到过几次签到码（用于事后诊断）
            submit_fails = []    # 提交失败的返回说明
            while dt.datetime.now() < end:
                rounds += 1
                # 标签页可能被关掉，先确认还活着
                if vpage.is_closed():
                    log("  [!] 课堂视频页被关掉了，尝试重开")
                    try:
                        vpage = _wait_vpage(ctx, page, timeout=60) or vpage
                    except Exception:
                        pass
                    if vpage.is_closed():
                        log("  [X] 无法恢复课堂视频页，本节放弃")
                        raise _WindowAbort("课堂视频页丢失")
                try:
                    r = vpage.evaluate(cap.FIND_SCREEN_VIDEO)
                    idx = r.get("idx", -1)
                    if idx < 0:
                        # 可能是课时切换 / 直播断了：重新点一次
                        if time.time() - last_ok > 30:
                            log("  30 秒没找到屏幕流，重试切直播…")
                            _try_live(vpage, "重试")
                            last_ok = time.time()
                        time.sleep(2)
                        continue
                    last_ok = time.time()
                    # 首次找到屏幕流时说一声，否则"没找到"和"找到了但没码"分不清
                    if not saw_video:
                        saw_video = True
                        log(f"  √ 找到屏幕流: video#{idx}"
                            f"（页面共 {r.get('count')} 个 video），开始找码")
                    vpage.evaluate(cap.EXPAND, idx)
                    time.sleep(0.2)
                    png = vpage.screenshot(timeout=20000)
                    vpage.evaluate(cap.RESTORE, idx)
                except Exception as e:
                    log(f"  截图异常 {type(e).__name__}: {str(e)[:80]}")
                    time.sleep(1.5)
                    continue

                for text in cap.decode_png(png):
                    info = cap.parse_qr(text)
                    if not info:
                        continue
                    qr_seen += 1
                    log(f"  ★ 抓到签到码 signHistoryId={info['signHistoryId']}")
                    with open(os.path.join(paths.OUTPUT, "autowatch-qr.png"), "wb") as f:
                        f.write(png)
                    try:
                        token = open(paths.TOKEN, encoding="utf-8").read().strip()
                    except Exception:
                        log("  [!] 读不到 token")
                        token = ""
                    if token:
                        ok, why = submit(info, token, dry_run, place=sess)
                        if ok:
                            got = True
                            reason = why
                            log(f"  ★★ 签到成功：{why}")
                            break
                        else:
                            log(f"  这次没成：{why}（继续盯）")
                            submit_fails.append(str(why))
                # 实时画面：每 LIVE_EVERY 秒覆盖写一次；成功那一刻也写一张
                # （用 tmp + os.replace 原子替换，避免控制台读到半截 PNG）
                if LIVE_EVERY > 0 and (got or time.time() - last_live >= LIVE_EVERY):
                    try:
                        with open(LIVE_FRAME + ".tmp", "wb") as f:
                            f.write(png)
                        os.replace(LIVE_FRAME + ".tmp", LIVE_FRAME)
                    except Exception as e:
                        log(f"  [!] 写实时画面失败 {type(e).__name__}: {str(e)[:60]}")
                    last_live = time.time()
                if got:
                    break
                if rounds % 20 == 0:
                    left = int((end - dt.datetime.now()).total_seconds())
                    log(f"  …已盯 {rounds} 轮，还剩 {left // 60} 分钟")
                # 每 60 秒写一次心跳：这一段是"真正在干活"的循环，
                # 不写的话 ts 会从窗口开始一直冻着（见上方 last_hb 注释）
                if time.time() - last_hb >= 60:
                    heartbeat()
                    last_hb = time.time()
                time.sleep(SAMPLE_GAP)
        except _WindowAbort as e:
            reason = str(e)
            log(f"  [X] 本节放弃：{reason}")
        except Exception as e:
            log(f"  [!] 窗口异常 {type(e).__name__}: {str(e)[:200]}")
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    if got:
        log(f"结束：{name} 已签到（{reason}）")
        write_status(state="done", lastCourse=name, lastResult=reason)
    else:
        log(f"结束：{name} 窗口内没签到成功")
        write_status(state="missed", lastCourse=name)

    rec = write_session_result(name, sess.get("courseId", ""), sess.get("window", ""),
                               got, reason, rounds, qr_seen, submit_fails, saw_video)
    # 桌面通知：不开控制台也能马上知道结果
    if rec:
        try:
            hm = str(rec.get("at") or "")[11:16]
            if rec.get("ok"):
                notify(f"签到成功 · {name}", f"{hm}  已签到", "ok")
            else:
                notify(f"这节没签上 · {name}", str(rec.get("why") or "没签上")[:60], "bad")
        except Exception:
            pass
    return got


# ---------------------------------------------------------------- 主循环
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="常驻监控")
    ap.add_argument("--once", action="store_true", help="立刻跑最近的一节（调试）")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # 所有子命令都依赖 data/periods.json + data/schedule.json；缺了就直接说清楚，
    # 别让它崩成 traceback（打包后的 GUI exe 会把它弹成"处理失败"对话框）。
    if not _need_data_files():
        return 2

    if args.status:
        pid = read_pid()
        alive = pid_alive(pid)
        print(f"autowatch: {'运行中 PID=' + str(pid) if alive else '未运行'}")
        if os.path.isfile(STATUS_FILE):
            print("最近状态:")
            for k, v in json.load(open(STATUS_FILE, encoding="utf-8")).items():
                print(f"  {k} = {v}")
        nx = next_session()
        if nx:
            a, b, s = nx
            print(f"下一节: {s['course']} {a:%m-%d %H:%M} → {b:%H:%M}")
        else:
            print("今天没有剩余课程")
        return 0

    if args.stop:
        pid = read_pid()
        if pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                print(f"已发送停止信号给 PID {pid}")
            except Exception as e:
                print(f"停止失败: {e}")
        else:
            print("没在运行")
        try:
            os.remove(PID_FILE)
        except Exception:
            pass
        return 0

    if not (args.run or args.once):
        ap.print_help()
        return 0

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    log("=" * 62)
    log(f"autowatch 启动 PID={os.getpid()}"
        f"{'（dry-run）' if args.dry_run else ''}")
    write_status(state="idle", pid=os.getpid())

    try:
        if args.once:
            nx = next_session() or (None, None, None)
            if nx[2] is None:
                log("今天没有剩余课程")
                return 0
            a, b, s = nx
            run_window(dt.datetime.now(),
                       min(b, dt.datetime.now() + dt.timedelta(minutes=10)),
                       s, args.dry_run)
            return 0

        # 记住今天已经处理过的窗口，同一个窗口绝不重跑
        # （2026-10-22 教训：窗口一结束就重启，断网时每 5 秒开一次浏览器）
        done_windows = set()
        while True:
            now = dt.datetime.now()
            nx = next_session(now)
            if nx is None:
                # 今天没了：睡到明天 0:05 再重算
                tmr = (now + dt.timedelta(days=1)).replace(
                    hour=0, minute=5, second=0, microsecond=0)
                secs = (tmr - now).total_seconds()
                log(f"今天课程都结束，休眠到 {tmr:%m-%d %H:%M}")
                write_status(state="sleeping", wakeAt=f"{tmr:%m-%d %H:%M}")
                # ⚠️ 必须分片睡 + 每分钟写心跳。
                # 原来是一觉睡几小时（time.sleep(max(60, secs))），期间心跳不更新，
                # 控制台会算出 heartbeatAge 很大而误判「进程可能已死」——实际它只是在等明天。
                while True:
                    remain = (tmr - dt.datetime.now()).total_seconds()
                    if remain <= 0:
                        break
                    time.sleep(min(60, max(1, remain)))
                    heartbeat()
                done_windows.clear()
                continue
            a, b, s = nx
            key = (s.get("courseId"), s.get("date"), tuple(s.get("periods") or []))
            if key in done_windows:
                # 这一节已经跑过了（失败也算），别再重来
                rest = (b - now).total_seconds()
                if rest > 0:
                    log(f"{s['course']} 本节已处理过，等它到 {b:%H:%M} 再找下一节")
                    while (b - dt.datetime.now()).total_seconds() > 0:
                        time.sleep(min(60, max(1, (b - dt.datetime.now()).total_seconds())))
                        heartbeat()
                continue
            if now < a:
                left = (a - now).total_seconds()
                log(f"等下一节：{s['course']}，{a:%H:%M} 开始盯（还有 {left/60:.0f} 分钟）")
                write_status(state="waiting", course=s["course"],
                             startAt=f"{a:%m-%d %H:%M}", secsLeft=int(left))
                # 分片睡，避免系统时间被调整后错过；同时保持心跳
                while (a - dt.datetime.now()).total_seconds() > 0:
                    time.sleep(min(60, max(1, (a - dt.datetime.now()).total_seconds())))
                    heartbeat()
                continue
            # 已在窗口内
            done_windows.add(key)
            run_window(a, b, s, args.dry_run)
            time.sleep(5)
    except KeyboardInterrupt:
        log("收到 Ctrl+C，退出")
    except Exception as e:
        log(f"[!] 致命错误 {type(e).__name__}: {str(e)[:300]}")
        raise
    finally:
        try:
            os.remove(PID_FILE)
        except Exception:
            pass
        write_status(state="stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
