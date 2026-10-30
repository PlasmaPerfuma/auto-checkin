# -*- coding: utf-8 -*-
"""
直播页结构探测 —— 只观察，不签到。

目的：这是整个项目唯一没验证过的东西——
  直播页到底有几个 <video>？哪个是「电脑屏幕」？画面渲染出来了吗？
  现在有没有正在直播的课都能跑，没有直播时只有黑屏。

会做：
  1) 自动打开指定课程的「课堂视频新版」
  2) 尝试自动点「直播」入口；点不到也不影响——你可以自己在弹出的浏览器里切
  3) 每隔几秒把所有 <video> 的状态（静音/尺寸/分辨率/就绪度/源地址）打一遍
  4) 对每个有画面的 video 截图，并算平均亮度（区分"真画面"和"黑屏"）
  5) 结果写 logs/live-probe.log 与 output/live/

用法：python app\\main.py live-probe
    python app/live_probe.py --course 95168 --seconds 120
"""
import argparse
import os
import sys
import time

import paths  # noqa: E402  必须在 import playwright 之前（它会设 TEMP）

paths.setup_env()

from playwright.sync_api import sync_playwright  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

IGNORE_DEFAULT_ARGS = ["--enable-automation"]
ARGS = ["--disable-blink-features=AutomationControlled"]
LOG_FILE = os.path.join(paths.LOGS, "live-probe.log")
SHOT_DIR = os.path.join(paths.OUTPUT, "live")
os.makedirs(SHOT_DIR, exist_ok=True)

TOOL_ID = 8329
VIDEO_JS = """(args) => {
  const out = [];
  document.querySelectorAll('video').forEach((v, i) => {
    const r = v.getBoundingClientRect();
    let src = '';
    try { src = v.currentSrc || v.src || ''; } catch (e) { src = '(cross-origin)'; }
    if (src && src.length > 120) src = src.slice(0, 120) + '…';
    out.push({
      i: i,
      muted: v.muted,
      paused: v.paused,
      volume: v.volume,
      readyState: v.readyState,
      videoW: v.videoWidth,
      videoH: v.videoHeight,
      cssW: Math.round(r.width),
      cssH: Math.round(r.height),
      display: getComputedStyle(v).display,
      visible: r.width > 0 && r.height > 0,
      src: src,
    });
  });
  return out;
}"""


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def brightness(png_bytes):
    """返回 0~255 的平均亮度；取不到图返回 None。"""
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(png_bytes)).convert("L")
        im.thumbnail((160, 160))
        px = list(im.getdata())
        return round(sum(px) / max(1, len(px)), 1)
    except Exception:
        return None


def try_enter_live(page):
    """尽力找到并点「直播」入口。找到什么就记什么，找不到也不报错。"""
    candidates = []
    for fr in page.frames:
        for sel in ("text=直播", "text=切换直播", "text=直播中", "text=直播课时"):
            try:
                loc = fr.locator(sel)
                for k in range(min(loc.count(), 5)):
                    el = loc.nth(k)
                    if el.is_visible():
                        txt = (el.inner_text() or "").strip().replace("\n", " ")[:30]
                        candidates.append((fr.url[:50], sel, txt))
                        el.click(timeout=2000)
                        log(f"  已点击直播入口: {sel} 文案={txt!r}")
                        time.sleep(2)
                        return True
            except Exception:
                continue
    if candidates:
        log(f"  看到疑似直播入口但没点成: {candidates[:3]}")
    else:
        log("  没找到文字含「直播」的入口（可能默认就在直播，或需要手动切）")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", default="95168", help="课程 courseId")
    ap.add_argument("--seconds", type=int, default=120, help="观察多少秒")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    try:
        open(LOG_FILE, "w", encoding="utf-8").close()
    except Exception:
        pass

    url = (f"https://oc.sjtu.edu.cn/courses/{args.course}"
           f"/external_tools/{TOOL_ID}?display=borderless")
    log("=" * 66)
    log(f"直播页探测  course={args.course}  观察 {args.seconds}s")
    log(f"目标: {url}")
    log("=" * 66)

    summary = {}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            paths.PROFILE, channel="chrome", headless=args.headless,
            no_viewport=True, ignore_default_args=IGNORE_DEFAULT_ARGS, args=ARGS,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            log("打开课堂视频…")
            page.goto(url, wait_until="domcontentloaded", timeout=90000)
            for i in range(60):
                time.sleep(1)
                n = 0
                for fr in page.frames:
                    try:
                        n += fr.locator("video").count()
                    except Exception:
                        pass
                if n:
                    log(f"  {i + 1}s 后看到 {n} 个 <video>")
                    break
            time.sleep(3)
            try_enter_live(page)

            log("")
            log("开始逐轮观察（每 4 秒一轮）…")
            rounds = max(1, args.seconds // 4)
            for r in range(rounds):
                frames_info = []
                for fi, fr in enumerate(page.frames):
                    try:
                        vids = fr.evaluate(VIDEO_JS, None)
                    except Exception:
                        continue
                    if vids:
                        frames_info.append((fi, fr.url[:60], vids))
                if not frames_info:
                    if r % 5 == 0:
                        log(f"  [{r * 4:3d}s] 还没有 <video>（页面可能没加载完或没直播）")
                    time.sleep(4)
                    continue

                for fi, furl, vids in frames_info:
                    for v in vids:
                        key = (fi, v["i"])
                        if key not in summary:
                            summary[key] = dict(v, first_seen=r * 4, best_bright=0,
                                                shots=0, note="")
                        row = summary[key]
                        if v["readyState"] >= 2 and v["videoW"] > 0:
                            try:
                                loc = page.frames[fi].locator("video").nth(v["i"])
                                png = loc.screenshot(timeout=5000)
                                b = brightness(png)
                                row["shots"] += 1
                                if b is not None and b > (row["best_bright"] or 0):
                                    row["best_bright"] = b
                                    name = f"f{fi}-v{v['i']}-r{r:02d}-b{int(b)}.png"
                                    with open(os.path.join(SHOT_DIR, name), "wb") as f:
                                        f.write(png)
                            except Exception as e:
                                row["note"] = f"{type(e).__name__}"
                if r % 3 == 0:
                    bits = []
                    for fi, furl, vids in frames_info:
                        for v in vids:
                            bits.append(f"f{fi}v{v['i']} muted={v['muted']} "
                                        f"{v['cssW']}x{v['cssH']} rs={v['readyState']}")
                    log(f"  [{r * 4:3d}s] " + " | ".join(bits))
                time.sleep(4)
        except Exception as e:
            log(f"[!] 出错 {type(e).__name__}: {str(e)[:200]}")
        finally:
            try:
                page.screenshot(path=os.path.join(SHOT_DIR, "page-full.png"), timeout=8000)
            except Exception:
                pass
            try:
                ctx.close()
            except Exception:
                pass

    # ---- 收尾：明确结论 ----
    print()
    print("=" * 66)
    log("=== 结论 ===")
    if not summary:
        log("[X] 一个 <video> 都没看到。可能：页面没加载完 / 这节课没有直播 / 需要先登录。")
        log("    截图见 output/live/page-full.png")
    else:
        log(f"共看到 {len(summary)} 个 <video>：")
        for (fi, vi), v in sorted(summary.items()):
            b = v["best_bright"]
            verdict = "黑屏/无画面"
            if b is not None and b > 25:
                verdict = "有画面"
            log(f"  frame{fi} video[{vi}]  muted={v['muted']}  css={v['cssW']}x{v['cssH']}  "
                f"分辨率={v['videoW']}x{v['videoH']}  readyState={v['readyState']}  "
                f"最高亮度={b}  → {verdict}")
            log(f"       src={v['src'][:90]}")
        log("")
        log(f"截图目录: {SHOT_DIR}")
        log("判读方法：muted=true 且尺寸小的那个才是「电脑屏幕」——但那一个需要有画面才说明直播页能截到码。")
    print("=" * 66)
    print(f"日志: {LOG_FILE}")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
