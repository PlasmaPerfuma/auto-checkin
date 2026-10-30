# -*- coding: utf-8 -*-
"""
「课堂视频新版」打不开的定向取证脚本 v2。

v1 的教训：脚本还没登录就去点按钮，必然超时，什么也没测到。
v2 的改动：
  * **先等登录**（轮询「课堂视频新版」链接出现，最长 3 分钟，期间你尽管手动登录）
  * 用 role=link 精确定位，避免点错
  * **记录所有 sjtu 域名的请求/响应，并在结束时列出「发出去了但永远没回来」的请求**
    ← 这一条能直接指出卡住的是哪一跳
  * 结束时打印每个标签页的最终状态

用法：双击 run-diag.cmd
日志：lti-diag.log
"""
import argparse
import json
import os
import sys
import tempfile
import time

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

from playwright.sync_api import sync_playwright  # noqa: E402

ARGS = [
    "--start-maximized",
    "--autoplay-policy=no-user-gesture-required",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
    "--disable-blink-features=AutomationControlled",
]
IGNORE_DEFAULT_ARGS = ["--enable-automation"]

LOGS = []
INTERESTING = ("sjtu.edu.cn", "oc.sjtu", "v.sjtu", "jaccount")


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    LOGS.append(line)
    print(line, flush=True)


def interesting(url):
    return any(k in url for k in INTERESTING)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://oc.sjtu.edu.cn/courses/95173")
    ap.add_argument("--click", default="课堂视频新版")
    ap.add_argument("--login-wait", type=float, default=180, help="等登录出现按钮的最长秒数")
    ap.add_argument("--wait", type=float, default=40, help="点完之后观察多少秒")
    ap.add_argument("--profile", default=PROFILE)
    args = ap.parse_args()

    pages = {}
    pending = {}          # request -> (method, url, start_time)

    def instrument(pg):
        if id(pg) in pages:
            return
        pages[id(pg)] = None
        log(f"◆ 新页面出现: {pg.url[:130]}")
        pg.on("console", lambda m: log(f"    [console/{m.type}] {m.text[:200]}"))
        pg.on("pageerror", lambda e: log(f"    [JS异常] {str(e)[:200]}"))
        pg.on("framenavigated",
              lambda f: log(f"    [导航] {f.url[:130]}") if f == pg.main_frame else None)

        def on_request(r):
            pending[r] = (r.method, r.url, time.time())
            if interesting(r.url):
                log(f"    → 请求 {r.method} {r.url[:120]}")

        def on_response(r):
            pending.pop(r.request, None)
            if interesting(r.url):
                log(f"    ← 响应 {r.status} {r.url[:110]}")

        def on_failed(r):
            pending.pop(r, None)
            log(f"    ✗ 请求失败 {r.failure}  {r.url[:120]}")

        pg.on("request", on_request)
        pg.on("response", on_response)
        pg.on("requestfailed", on_failed)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            args.profile, channel="chrome", headless=False, no_viewport=True,
            ignore_default_args=IGNORE_DEFAULT_ARGS, args=ARGS,
        )
        ctx.on("page", instrument)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        instrument(page)

        log("=" * 72)
        log(f"步骤 1：打开 {args.url}")
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"    [!] goto 异常: {type(e).__name__}: {str(e)[:150]}")
        time.sleep(2)

        try:
            info = page.evaluate("() => ({webdriver: navigator.webdriver, ua: navigator.userAgent})")
            log(f"自动化探测: webdriver={info['webdriver']}  {info['ua'][:90]}")
        except Exception:
            pass

        log("=" * 72)
        log(f"步骤 2：等待「{args.click}」出现（未登录请现在手动登录，最长 {args.login_wait:.0f} 秒）")
        link = page.get_by_role("link", name=args.click)
        ok = False
        deadline = time.time() + args.login_wait
        while time.time() < deadline:
            try:
                if link.first.is_visible():
                    ok = True
                    break
            except Exception:
                pass
            time.sleep(1)
            if int(time.time()) % 10 == 0:
                try:
                    log(f"    ……等待中，当前: {page.url[:110]}")
                except Exception:
                    pass
                time.sleep(1)

        if not ok:
            log("    [!] 一直没等到那个链接，放弃了。把本日志发我即可。")
        else:
            log(f"    √ 找到了，当前页: {page.url[:110]}")
            log("=" * 72)
            log(f"步骤 3：点击「{args.click}」")
            try:
                link.first.click(timeout=20000)
                log("    √ 点击已发出")
            except Exception as e:
                log(f"    [!] 点击失败: {type(e).__name__}: {str(e)[:180]}")

        log("=" * 72)
        log(f"步骤 4：观察 {args.wait:.0f} 秒（请留意浏览器窗口里发生了什么）")
        end = time.time() + args.wait
        while time.time() < end:
            for pg in list(ctx.pages):
                if pg.is_closed():
                    continue
                instrument(pg)
                try:
                    cur = pg.url
                except Exception:
                    continue
                if pages.get(id(pg)) != cur:
                    pages[id(pg)] = cur
                    log(f"    ▸ 地址变为: {cur[:130]}")
            time.sleep(0.5)

        log("=" * 72)
        log("★ 关键：发出去了但始终没有回来的请求（这些就是卡住的元凶）")
        now = time.time()
        if not pending:
            log("  （没有挂起的请求）")
        for r, (method, url, t0) in list(pending.items()):
            log(f"  ⧗ 已挂起 {now - t0:6.1f}s  {method} {url[:130]}")

        log("=" * 72)
        log("结局：所有标签页最终状态")
        for pg in list(ctx.pages):
            if pg.is_closed():
                log("  - (已关闭)")
                continue
            try:
                st = pg.evaluate("""() => ({
                  url: location.href, title: document.title, ready: document.readyState,
                  htmlLen: document.documentElement ? document.documentElement.outerHTML.length : 0,
                  textLen: document.body ? document.body.innerText.length : 0,
                  text: document.body ? document.body.innerText.slice(0, 300) : '',
                  videos: document.querySelectorAll('video').length,
                  frames: document.querySelectorAll('iframe').length,
                })""")
                log(f"  ▸ {json.dumps(st, ensure_ascii=False)[:600]}")
            except Exception as e:
                log(f"  - 读取失败: {type(e).__name__}: {str(e)[:120]}")

        try:
            ctx.close()
        except Exception:
            pass

    out = os.path.join(paths.LOGS, "lti-diag.log")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(LOGS))
    print(f"\n[i] 日志已写入: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
