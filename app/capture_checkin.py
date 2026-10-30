# -*- coding: utf-8 -*-
"""
真实签到时的「抓码 + 试提交 + 自验证」脚本。

它做的事：
  1. 打开课程 → 课堂视频新版 → 找到「电脑屏幕」那一路（muted===true），
     用 CSS 把它撑满视口，截图 → 解码出 rollCallToken / signHistoryId
  2. 先读一次 /sign/records 记下当前记录数（作为基线）
  3. 依次试两种签到接口，**每试完都回读一次记录**，看记录数有没有增加：
       A. GET /sign/scan/gis/{sid}?lat=&lng=&address=   （带定位）
       B. GET /sign/scan/{token}/{sid}                  （不带定位）
     一旦记录数增加就停手，不再试下一种。
  4. 全程把请求 URL、返回原文、记录数变化写进 checkin-capture.log

注意：A 成功后不会再去试 B，避免重复记录。

用法：python app\\main.py capture（参数见下方说明）
"""
import argparse
import io
import json
import os
import random
import sys
import tempfile
import time
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import paths                      # 项目唯一的路径真源（app/paths.py）
HERE = paths.ROOT                 # 兼容旧代码：HERE 一律指项目根
PROFILE = paths.PROFILE          # 用 paths 的真源：打包后 ROOT 是 exe 目录，profile 必须在可写目录

_TMP = paths.TMP                 # 同上：临时目录也必须落在可写目录
os.makedirs(_TMP, exist_ok=True)
for _k in ("TEMP", "TMP", "TMPDIR"):
    os.environ[_k] = _TMP
tempfile.tempdir = _TMP

import numpy as np                       # noqa: E402
import zxingcpp                          # noqa: E402
from PIL import Image                    # noqa: E402
from playwright.sync_api import sync_playwright   # noqa: E402

ARGS = [
    "--start-maximized",
    "--autoplay-policy=no-user-gesture-required",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
    "--disable-blink-features=AutomationControlled",
]
IGNORE_DEFAULT_ARGS = ["--enable-automation"]

BASE = "https://mlearning.sjtu.edu.cn/lms-lti-rollcall-sjtu"
LOGS = []


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    LOGS.append(line)
    print(line, flush=True)
    # 实时落盘：以前是攒在内存里、最后一并写，**中途崩了就什么都不留** ——
    # 用户明确要求过「日志每行实时 append + flush」，所以这里每行都写。
    try:
        p = os.path.join(paths.LOGS, "checkin-capture.log")
        os.makedirs(paths.LOGS, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# 找一个 muted 的（=电脑屏幕）video，撑满视口，返回它的下标
FIND_SCREEN_VIDEO = """() => {
  const vs = [...document.querySelectorAll('video')];
  let idx = -1;
  vs.forEach((v, i) => {
    const r = v.getBoundingClientRect();
    if (idx < 0 && v.muted && r.width > 40 && v.videoWidth > 0) idx = i;
  });
  if (idx < 0) {
    vs.forEach((v, i) => {
      const r = v.getBoundingClientRect();
      if (idx < 0 && r.width > 40 && v.videoWidth > 0) idx = i;
    });
  }
  return {idx, count: vs.length};
}"""

EXPAND = """(idx) => {
  const v = document.querySelectorAll('video')[idx];
  if (!v) return false;
  if (!v.dataset._oldStyle) v.dataset._oldStyle = v.getAttribute('style') || '';
  v.setAttribute('style', 'position:fixed;left:0;top:0;width:100vw;height:100vh;' +
                          'object-fit:fill;z-index:2147483647;background:#000');
  return true;
}"""

RESTORE = """(idx) => {
  const v = document.querySelectorAll('video')[idx];
  if (!v) return false;
  if (v.dataset._oldStyle !== undefined) v.setAttribute('style', v.dataset._oldStyle);
  return true;
}"""

SCREEN_SELECT = """(idx) => {
  const v = document.querySelectorAll('video')[idx];
  if (!v) return null;
  const r = v.getBoundingClientRect();
  return {x: r.x, y: r.y, w: r.width, h: r.height};
}"""


def decode_png(png):
    im = Image.open(io.BytesIO(png)).convert("RGB")
    if im.width < 1100:
        k = 2 if im.width >= 520 else 3
        im = im.resize((im.width * k, im.height * k), Image.LANCZOS)
    res = zxingcpp.read_barcodes(np.asarray(im), formats=zxingcpp.BarcodeFormat.QRCode)
    return [b.text for b in res if b.text]


def parse_qr(text):
    """把二维码网址拆成 courseCode / rollCallToken / signHistoryId。"""
    if "forscan" not in text and "scanRollCall" not in text:
        return None
    q = urllib.parse.parse_qs(urllib.parse.urlparse(text).query)
    out = {k: (v[0] if v else None) for k, v in q.items()}
    if not out.get("signHistoryId") or not out.get("rollCallToken"):
        return None
    return out


def api_get(path, params, token):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": token,
                                               "Accept": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:160]}"


def record_count(token, course):
    st, body = api_get("/sign/records",
                       {"courseCode": course, "pageNum": 1, "pageSize": 100}, token)
    try:
        return int(json.loads(body)["body"]["total"])
    except Exception:
        return None


# 点「直播」入口。
# 2026-10-22 教训：原先要求 innerText **精确等于** "直播"，实际上页面上的标签常带
# 装饰（● 圆点、图标、前后空白），导致一次都没点中，全程卡在点播页。
# 现在分三档匹配，并且**返回详细结果**（点中了什么 / 页面上有哪些候选），
# 免得"点了没点中"这件事在日志里完全看不见。
LIVE_JS = """() => {
  const BAD = ['回放', '点播', '结束', '暂无', '未开始', '已结束'];
  const GOOD = ['直播中', '切换直播', '直播'];
  const norm = (s) => (s || '')
      .replace(/[\\u25CF\\u25CB\\u25AA\\u25AB\\u2022\\u00B7\\u25B8\\u25BA\\u2192\\u2026]/g, '')
      .replace(/\\s+/g, '')
      .trim();
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const els = [...document.querySelectorAll('a,button,div,span,li,p,label,em,strong')];
  const cands = [];
  for (const el of els) {
    const raw = (el.innerText || '').trim();
    if (!raw || raw.length > 12) continue;
    if (!vis(el)) continue;
    const n = norm(raw);
    if (!n.includes('直播')) continue;
    if (BAD.some((b) => n.includes(b))) continue;
    // 只保留最内层元素，避免点到外层容器
    if ([...el.children].some((c) => norm(c.innerText || '').includes('直播'))) continue;
    let score = 0;
    if (n === '直播') score = 100;
    else if (GOOD.includes(n)) score = 90;
    else if (n.startsWith('直播')) score = 70;
    else score = 50;
    cands.push({ el, raw, n, score });
  }
  cands.sort((a, b) => b.score - a.score);
  if (!cands.length) {
    return { hit: '', seen: [], note: 'no-candidate' };
  }
  const seen = cands.slice(0, 6).map((c) => c.raw);
  const best = cands[0];
  try { best.el.click(); } catch (e) {
    return { hit: '', seen, note: 'click-threw:' + (e && e.name) };
  }
  return { hit: best.raw, seen, note: 'ok' };
}"""


def brightness(png_bytes):
    """平均亮度 0~255；用来区分「有画面」和「黑屏」。"""
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(png_bytes)).convert("L")
        im.thumbnail((200, 200))
        px = list(im.getdata())
        return round(sum(px) / max(1, len(px)), 1)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--course-url", default=None,
                    help="直接指定课程页地址；不给就用 --course 拼")
    ap.add_argument("--course", default="95173",
                    help="课程 ID（同时决定打开的课程页和提交用的 courseCode）")
    ap.add_argument("--click", default="课堂视频新版")
    ap.add_argument("--wait", type=float, default=600, help="最多等多少秒抓到一个码")
    ap.add_argument("--lat", default="31.0269")
    ap.add_argument("--lng", default="121.4335")
    ap.add_argument("--address", default="上海市闵行区东川路800号上海交通大学")
    ap.add_argument("--profile", default=PROFILE)
    ap.add_argument("--dry-run", action="store_true",
                    help="只抓码、把画面存下来，绝不提交（验证直播页用）")
    ap.add_argument("--no-live", action="store_true", help="不自动点「直播」")
    ap.add_argument("--headless", action="store_true",
                    help="不显示浏览器窗口（AI 自测用；正式签到建议有窗口好观察）")
    ap.add_argument("--link-wait", type=float, default=180,
                    help="等「课堂视频新版」链接出现的秒数（默认 180，是留给人工登录的；"
                         "调试时传 15，别每次都白等 3 分钟）")
    ap.add_argument("--check-env", action="store_true",
                    help="只打印运行环境和路径就退出（不开浏览器，2 秒出结果）")
    args = ap.parse_args()

    # 以前 --course 只影响提交用的 courseCode、不改打开的页面（默认永远去 95173），
    # 传 --course 95166 却打开 95173 非常容易看错。现在没给 --course-url 就按 --course 拼。
    if not args.course_url:
        args.course_url = f"https://oc.sjtu.edu.cn/courses/{args.course}"

    tok_path = paths.TOKEN
    if not os.path.isfile(tok_path):
        log(f"[!] 找不到 {tok_path}，先跑一次 python app\\main.py get-token")
        return 1
    token = open(tok_path, encoding="utf-8").read().strip()
    log(f"token 已读取（{len(token)} 字符，不显示）")
    # 打包后最容易出问题的就是"到底在用哪个 profile"。打出来，省得下次靠猜。
    if args.dry_run:
        log(f"  模式 = {'打包(exe)' if paths.FROZEN else '源码'}    HOME = {paths.HOME_DIR}")
        log(f"  profile = {args.profile}   存在 = {os.path.isdir(args.profile)}")

    # ---- --check-env：只报环境、不开浏览器就退出 ----
    # 排查"没登录 / 打不开页面"时，一半的问题其实是"用错了目录"。
    # 以前要跑一整条 3 分钟的链路才发现，现在 2 秒就能看出来。
    if args.check_env:
        prof = args.profile
        log("---- 运行环境 ----")
        log(f"  模式        = {'打包(exe)' if paths.FROZEN else '源码'}")
        log(f"  ROOT        = {paths.ROOT}")
        log(f"  RES_DIR     = {paths.RES_DIR}")
        log(f"  APP_DIR     = {paths.APP_DIR}")
        log(f"  HOME_DIR    = {paths.HOME_DIR}")
        log(f"  DATA        = {paths.DATA}")
        log(f"  TMP         = {paths.TMP}")
        log(f"  profile     = {prof}   存在 = {os.path.isdir(prof)}")
        ck = os.path.join(prof, "Default", "Network", "Cookies")
        log(f"  登录态文件   = {os.path.isfile(ck)}")
        log(f"  课程页       = {args.course_url}")
        log(f"  token       = {tok_path}（{len(token)} 字符）")
        log("------------------")
        return 0

    # 位置做一点点抖动（仍在闵行校内）
    lat = float(args.lat) + random.uniform(-0.0002, 0.0002)
    lng = float(args.lng) + random.uniform(-0.0002, 0.0002)
    log(f"将使用的坐标: lat={lat:.6f} lng={lng:.6f}")

    info = None
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            args.profile, channel="chrome", headless=args.headless, no_viewport=True,
            ignore_default_args=IGNORE_DEFAULT_ARGS, args=ARGS,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        log(f"打开 {args.course_url}")
        try:
            page.goto(args.course_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"  [!] {type(e).__name__}")

        log(f"等待课程页就绪 / 你手动登录（最长 {int(args.link_wait)}s），然后点「{args.click}」")
        link = page.get_by_role("link", name=args.click)
        try:
            link.first.wait_for(state="visible", timeout=int(args.link_wait * 1000))
            link.first.click(timeout=20000)
            log("  已点击，等播放器…")
        except Exception as e:
            log(f"  [!] 点击失败: {str(e)[:150]}")
            # 失败时把"我到底站在哪一页"打出来 —— 找不到元素时第一步永远是看这个，
            # 否则只能猜是没登录、页面坏了、还是选择器错了。
            try:
                log(f"      当前 URL   : {page.url}")
                log(f"      页面标题   : {page.title()}")
                log(f"      链接总数   : {page.locator('a').count()}")
                body = (page.inner_text("body") or "")[:300].replace("\n", " ")
                log(f"      正文开头   : {body}")
            except Exception as e2:
                log(f"      （连页面状态都读不出来: {type(e2).__name__}）")

        vpage = None
        dl = time.time() + 120
        while time.time() < dl and not vpage:
            for pg in list(ctx.pages):
                if pg.is_closed():
                    continue
                try:
                    r = pg.evaluate(FIND_SCREEN_VIDEO)
                except Exception:
                    continue
                if r and r.get("idx", -1) >= 0:
                    vpage = pg
                    break
            time.sleep(1)
        if not vpage:
            log("[!] 没等到播放器。")
            ctx.close()
            return 1

        # 课堂视频默认可能停在点播/历史，先切到「直播」
        if not args.no_live:
            hit = ""
            seen = []
            note = ""
            for _ in range(4):
                try:
                    r = vpage.evaluate(LIVE_JS)
                except Exception:
                    r = None
                if isinstance(r, dict):
                    hit = (r.get("hit") or "").strip()
                    seen = r.get("seen") or []
                    note = r.get("note") or ""
                else:
                    hit = (r or "").strip()
                if hit:
                    break
                time.sleep(1.5)
            if hit:
                log(f"  已点「{hit}」，切到直播")
                time.sleep(2.5)
            elif seen:
                log(f"  [!] 没点中直播；页面上含「直播」的元素有: {seen[:5]}（{note}）")
            else:
                log(f"  [i] 页面上没有含「直播」的可点元素（{note}），可能默认就在直播，继续")

        # 撑满 + 循环截图找码
        deadline = time.time() + args.wait
        log(f"开始找码（最长 {args.wait:.0f}s，1 秒截一次）")
        last_png = None
        while time.time() < deadline and not info:
            try:
                r = vpage.evaluate(FIND_SCREEN_VIDEO)
                idx = r["idx"]
                vpage.evaluate(EXPAND, idx)
                time.sleep(0.35)
                png = vpage.screenshot(timeout=8000)
                vpage.evaluate(RESTORE, idx)
            except Exception as e:
                log(f"  [!] 截图异常: {type(e).__name__}: {str(e)[:100]}")
                time.sleep(1)
                continue
            last_png = png
            for txt in decode_png(png):
                got = parse_qr(txt)
                if got:
                    info = got
                    with open(os.path.join(paths.OUTPUT, "checkin-qr.jpg"), "wb") as f:
                        f.write(png)
                    log("  ★ 抓到签到码：")
                    for k in ("courseCode", "signHistoryId"):
                        log(f"      {k} = {info.get(k)}")
                    log(f"      rollCallToken = {info.get('rollCallToken')[:20]}…（已截断显示）")
                    break
            if not info:
                time.sleep(0.6)

        if not info:
            # 把最后一帧存下来——这样能分清"没码"和"根本没画面"
            if last_png:
                p = os.path.join(paths.OUTPUT, "live-last-frame.png")
                with open(p, "wb") as f:
                    f.write(last_png)
                log(f"  最后一帧已存 {p}（平均亮度 {brightness(last_png)}）")
            log("[!] 一直没抓到码。")
            if args.dry_run:
                log("    dry-run：本次只验证「能不能看到画面」，不做任何提交。")
            ctx.close()
            out = os.path.join(paths.LOGS, "checkin-capture.log")
            pass   # 日志已由 log() 逐行实时写入，这里不再整体重写（否则会截断前面的内容）
            print(f"\n[i] 日志: {out}")
            return 0 if args.dry_run else 1

        if args.dry_run:
            log("=" * 70)
            log("[dry-run] 已抓到签到码，但**不提交**。")
            for k in ("courseCode", "signHistoryId"):
                log(f"      {k} = {info.get(k)}")
            log(f"      画面已存 {os.path.join(paths.OUTPUT, 'checkin-qr.jpg')}")
            ctx.close()
            out = os.path.join(paths.LOGS, "checkin-capture.log")
            pass   # 日志已由 log() 逐行实时写入，这里不再整体重写（否则会截断前面的内容）
            print(f"\n[i] 日志: {out}")
            return 0

        sid = info["signHistoryId"]
        rtok = info["rollCallToken"]
        course = info.get("courseCode") or args.course

        log("=" * 70)
        base_n = record_count(token, course)
        log(f"当前签到记录数（基线）: {base_n}")

        plan = [
            ("A 带定位 /sign/scan/gis",
             f"/sign/scan/gis/{sid}",
             {"lat": f"{lat:.6f}", "lng": f"{lng:.6f}", "address": args.address}),
            ("B 不带定位 /sign/scan",
             f"/sign/scan/{rtok}/{sid}", {}),
        ]
        for label, path, params in plan:
            log("-" * 70)
            log(f"试 {label}")
            st, body = api_get(path, params, token)
            log(f"  HTTP {st}")
            log(f"  返回: {body[:400]}")
            time.sleep(1.2)
            n = record_count(token, course)
            log(f"  记录数: {base_n} -> {n}")
            if n is not None and base_n is not None and n > base_n:
                log(f"  ★★ 签到成功！（记录数 +{n - base_n}）后面的方案不再执行")
                break
        else:
            log("-" * 70)
            log("两种都没让记录数增加。把这份日志发我，我按返回内容继续调。")

        try:
            ctx.close()
        except Exception:
            pass

    out = os.path.join(paths.LOGS, "checkin-capture.log")
    # 日志已由 log() 逐行实时写入；这里只提示位置，不再整体重写（会截断前面的内容）
    print(f"\n[i] 日志: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
