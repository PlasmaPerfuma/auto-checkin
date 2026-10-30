# -*- coding: utf-8 -*-
"""
侦察脚本 v2 —— 让我（AI）看清这套 Canvas 界面的真实结构。

相比 v1 的关键修复：
  * **跟踪所有标签页/弹窗**（v1 只盯第一个页面，"课堂视频新版"是开新窗口的，所以拍不到）
  * **抓 console 日志 / JS 异常 / 请求失败 / 4xx-5xx 响应** —— 弹窗变 about:blank 的真正原因会在这里现形
  * **不传 --enable-automation**，让浏览器看起来更像普通 Chrome（有些站点会检测自动化并拒绝加载）

用法：
    双击 run-recon.cmd
    每到一个想记录的位置，回到本窗口按一次【回车】。
"""
import argparse
import json
import os
import sys
import tempfile
import threading
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import paths                      # 项目唯一的路径真源（app/paths.py）
HERE = paths.ROOT                 # 兼容旧代码：HERE 一律指项目根
PROFILE = paths.PROFILE          # 用 paths 的真源：打包后 ROOT 是 exe 目录，profile 必须在可写目录

# 这台机器的 AppData\Local\Temp 建不了目录（Playwright 会 EPERM mkdtemp），
# 所以必须在启动浏览器之前，把 TEMP/TMP 指到工作区里自己的 tmp 目录。
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

# 把 Playwright 默认会加、但可能让站点识别出"这是自动化浏览器"的参数去掉
IGNORE_DEFAULT_ARGS = ["--enable-automation"]

DUMP_JS = r"""
() => {
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)};
  };
  const path = (el) => {
    const bits = [];
    for (let n = el; n && n.nodeType === 1 && bits.length < 4; n = n.parentElement) {
      let s = n.tagName.toLowerCase();
      if (n.id) { s += '#' + n.id; bits.unshift(s); break; }
      if (n.className && typeof n.className === 'string')
        s += '.' + n.className.trim().split(/\s+/).slice(0, 2).join('.');
      bits.unshift(s);
    }
    return bits.join(' > ');
  };

  const videos = [...document.querySelectorAll('video')].map(v => ({
    path: path(v), box: box(v), id: v.id, cls: typeof v.className === 'string' ? v.className : '',
    videoWidth: v.videoWidth, videoHeight: v.videoHeight,
    readyState: v.readyState, paused: v.paused,
    currentTime: Math.round(v.currentTime * 10) / 10,
    src: (v.src || '').slice(0, 300),
    currentSrc: (v.currentSrc || '').slice(0, 300),
  }));

  const clickable = [...document.querySelectorAll('button, a, [role=button], [class*=btn i], [class*=tab i], [class*=switch i]')]
    .map(el => {
      const t = (el.innerText || el.getAttribute('aria-label') || el.title || '').trim().replace(/\s+/g, ' ');
      return t ? {text: t.slice(0, 60), tag: el.tagName.toLowerCase(), box: box(el), path: path(el)} : null;
    })
    .filter(Boolean).slice(0, 300);

  const iframes = [...document.querySelectorAll('iframe')].map(f => ({
    path: path(f), box: box(f), src: (f.src || '').slice(0, 300)
  }));

  const inputs = [...document.querySelectorAll('input, select, textarea')].map(el => ({
    tag: el.tagName.toLowerCase(), type: el.type || '',
    id: el.id, name: el.name, placeholder: el.placeholder || '', box: box(el),
  }));

  return {
    url: location.href, title: document.title,
    videos, iframes, clickable, inputs,
    bodyText: (document.body ? document.body.innerText : '')
      .replace(/\n{2,}/g, '\n').slice(0, 4000),
  };
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://oc.sjtu.edu.cn/courses/95173", help="起始网址")
    ap.add_argument("--out", default=paths.OUTPUT_OUT, help="快照输出目录")
    ap.add_argument("--auto-login", default="1",
                    help="启动后先自动登录 Canvas（需要 credentials.json）。传 0 关闭")
    ap.add_argument("--auto-every", type=float, default=0,
                    help="每隔多少秒自动存一份快照（0=不自动存，只按回车时存）")
    ap.add_argument("--profile", default=PROFILE,
                    help="Chrome 用户数据目录（默认 ./chrome-profile）")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.profile, exist_ok=True)

    trigger = threading.Event()
    quiet = threading.Event()          # 存快照时不希望被日志刷屏

    def reader():
        while True:
            try:
                if not sys.stdin.readline():
                    break
            except Exception:
                break
            trigger.set()

    threading.Thread(target=reader, daemon=True).start()

    print("=" * 74)
    print("侦察模式 v2")
    print("  1. 浏览器已打开，请在里面登录 Canvas")
    print("  2. 依次走到这几个地方，每到一个就回本窗口按一次【回车】：")
    print("       ① 课程页面（能看到「课堂视频新版」按钮）")
    print("       ② 进入课堂视频后的【点播】界面")
    print("       ③ 切到【直播】后的界面")
    print("       ④ 把电脑屏幕直播切到中间大窗口后的界面  ← 最重要")
    print("  3. 弹窗/新标签页也会被自动记录，不用你操心")
    print("  4. 结束按 Ctrl+C")
    print(f"  快照 -> {args.out}")
    print("=" * 74)

    n = 0
    next_auto = time.time() + args.auto_every if args.auto_every else None

    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                args.profile,
                channel="chrome",
                headless=False,
                no_viewport=True,
                ignore_default_args=IGNORE_DEFAULT_ARGS,
                args=ARGS,
            )
        except Exception as e:
            print(f"\n[!] 启动 Chrome 失败: {type(e).__name__}")
            print(f"    {str(e)[:300]}\n")
            print("    1. 你日常的 Chrome 正开着，占用了同一个用户数据目录 —— 先关掉它")
            print(f"    2. 临时目录不可写 —— 本脚本已把 TEMP 指到 {_TMP}")
            return 1

        tracked = {}   # id(page) -> {"page":..., "url": None}

        def note(msg):
            if not quiet.is_set():
                print(f"  {msg}")

        def instrument(pg):
            if id(pg) in tracked:
                return
            tracked[id(pg)] = {"page": pg, "url": None}
            print(f"  [新页面] {pg.url[:110]}")
            pg.on("console", lambda m: note(f"[console:{m.type}] {m.text[:180]}"))
            pg.on("pageerror", lambda e: note(f"[JS异常] {str(e)[:180]}"))
            pg.on("requestfailed", lambda r: note(f"[请求失败] {r.failure}  {r.url[:110]}"))
            pg.on("response", lambda r: note(f"[HTTP {r.status}] {r.url[:110]}") if r.status >= 400 else None)
            pg.on("framenavigated",
                  lambda f: note(f"[导航] {f.url[:110]}") if f == pg.main_frame else None)
            pg.on("close", lambda: note("  [页面已关闭]"))

        ctx.on("page", instrument)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        instrument(page)

        # 先自动登录——recon 之前"打不开课堂视频"，最可能是 Canvas 会话已过期，
        # 页面停留在登录页/跳转中，点了链接也没用。
        if str(args.auto_login) != "0":
            try:
                import sjtu_login
                if os.path.isfile(sjtu_login.CRED):
                    creds = sjtu_login.load_creds()
                    if creds:
                        print("[i] 先自动登录 Canvas…")
                        ok = sjtu_login.do_login(page, ctx, creds[0], creds[1])
                        print(f"[i] 自动登录{'成功' if ok else '失败（你可以手动登）'}")
                else:
                    print("[i] 没有 credentials.json，跳过自动登录（你手动登）")
            except Exception as e:
                print(f"[i] 自动登录跳过: {type(e).__name__}: {str(e)[:120]}")

        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"[!] 打开起始页失败（不影响，可手动导航）: {type(e).__name__}")

        def dump(pg, reason):
            nonlocal n
            n += 1
            quiet.set()
            try:
                try:
                    pg.wait_for_load_state("domcontentloaded", timeout=3000)
                except Exception:
                    pass
                state = pg.evaluate(DUMP_JS)
                base = os.path.join(args.out, f"{n:02d}")
                try:
                    with open(base + ".png", "wb") as f:
                        f.write(pg.screenshot(full_page=False))
                except Exception as e:
                    note(f"[截图失败] {type(e).__name__}")
                with open(base + ".json", "w", encoding="utf-8") as f:
                    json.dump({"_reason": reason, **state}, f, ensure_ascii=False, indent=1)
                print(f"  [{time.strftime('%H:%M:%S')}] #{n} 已存 ({reason})  "
                      f"video×{len(state['videos'])}  input×{len(state['inputs'])}  "
                      f"可点×{len(state['clickable'])}")
                print(f"       {state['url'][:110]}")
            except Exception as e:
                print(f"  [!] 第 {n} 次快照失败: {type(e).__name__}: {str(e)[:140]}")
            finally:
                quiet.clear()

        try:
            while True:
                alive = [pg for pg in list(ctx.pages) if not pg.is_closed()]
                for pg in alive:
                    instrument(pg)

                if trigger.is_set():
                    trigger.clear()
                    tgt = alive[-1] if alive else page
                    dump(tgt, "手动回车")

                for pg in alive:
                    info = tracked.get(id(pg))
                    if not info:
                        continue
                    try:
                        cur = pg.url
                    except Exception:
                        continue
                    if cur != info["url"]:
                        # 首个 about:blank 不值得存
                        if info["url"] is not None or cur.startswith("http"):
                            dump(pg, f"URL变化 {info['url'] or '初始'} -> {cur[:50]}")
                        info["url"] = cur

                if next_auto and time.time() >= next_auto:
                    dump(alive[-1] if alive else page, "自动")
                    next_auto = time.time() + args.auto_every
                time.sleep(0.4)
        except KeyboardInterrupt:
            print("\n[i] 收到 Ctrl+C，收工。")
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    print(f"[结果] 共 {n} 份快照 -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
