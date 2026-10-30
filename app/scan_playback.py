# -*- coding: utf-8 -*-
"""
点播录像扫码测试 —— 用真实的播放器 + 真实的录像，验证「抓屏 → 解码二维码」这条链路。

为什么现在能做：录播里那些「点名签到」片段本来就带着二维码。虽然那些码早就过期了、
没法用来签到，但它们能告诉我们两件事：
  1. 从真实播放器画面上，我们到底能不能稳定把码解出来（这是唯一的硬骨头）
  2. 二维码里的内容长什么样（签到网址的格式）——**这样就不必等直播了**

做法：不靠"实时看"，而是**按时间点跳着取样**（每 step 秒跳一次），比实时播放快得多。

用法：双击 run-scan.cmd
输出：scan-result.log + found/ 目录里存下解出二维码的帧
"""
import argparse
import io
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

import numpy as np                      # noqa: E402
import zxingcpp                         # noqa: E402
from PIL import Image                   # noqa: E402
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

LOGS = []


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    LOGS.append(line)
    print(line, flush=True)


LIST_VIDEOS = """() => {
  const vs = [...document.querySelectorAll('video')];
  return vs.map((v, i) => {
    const r = v.getBoundingClientRect();
    return {
      idx: i,
      w: Math.round(r.width), h: Math.round(r.height),
      area: Math.round(r.width * r.height),
      muted: v.muted,
      videoWidth: v.videoWidth, videoHeight: v.videoHeight,
      duration: v.duration || 0,
      paused: v.paused,
      currentSrc: (v.currentSrc || v.src || '').slice(0, 300),
    };
  });
}"""

SEEK = """([idx, t]) => new Promise(res => {
  const v = document.querySelectorAll('video')[idx];
  if (!v) return res(false);
  let done = false;
  const fin = (ok) => { if (!done) { done = true; v.removeEventListener('seeked', onSeek); res(ok); } };
  const onSeek = () => fin(true);
  v.addEventListener('seeked', onSeek);
  try { v.pause(); } catch (e) {}
  try { v.currentTime = t; } catch (e) { return fin(false); }
  setTimeout(() => fin(false), 2500);
})"""


def decode(png_bytes):
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    # 小窗口里的二维码像素太少，先放大再解，能明显提高识别率
    if im.width < 1100:
        k = 2 if im.width >= 520 else 3
        im = im.resize((im.width * k, im.height * k), Image.LANCZOS)
    arr = np.asarray(im)
    res = zxingcpp.read_barcodes(arr, formats=zxingcpp.BarcodeFormat.QRCode)
    return [b.text for b in res if b.text], arr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://oc.sjtu.edu.cn/courses/95173")
    ap.add_argument("--click", default="课堂视频新版")
    ap.add_argument("--login-wait", type=float, default=180)
    ap.add_argument("--step", type=float, default=3.0, help="每隔多少秒录像时间取一个样")
    ap.add_argument("--max-minutes", type=float, default=999, help="最多扫描多少分钟的录像")
    ap.add_argument("--video", default="all",
                    help="解哪个画面：all=所有 video 都解（默认，最稳）；largest=只解最大的；或给下标如 0")
    ap.add_argument("--log", default="scan-result.log", help="日志文件名")
    ap.add_argument("--save-max", type=int, default=8,
                    help="最多保存多少张命中画面（缩到 1280 宽存 JPEG，省空间）")
    ap.add_argument("--profile", default=PROFILE)
    args = ap.parse_args()

    found_dir = paths.OUTPUT_FOUND
    os.makedirs(found_dir, exist_ok=True)
    hits = {}

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            args.profile, channel="chrome", headless=False, no_viewport=True,
            ignore_default_args=IGNORE_DEFAULT_ARGS, args=ARGS,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        log("=" * 72)
        log(f"步骤 1：打开 {args.url}")
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"    [!] {type(e).__name__}: {str(e)[:120]}")
        time.sleep(2)

        log(f"步骤 2：等待「{args.click}」（未登录请现在手动登录，最长 {args.login_wait:.0f}s）")
        link = page.get_by_role("link", name=args.click)
        deadline = time.time() + args.login_wait
        ok = False
        while time.time() < deadline:
            try:
                if link.first.is_visible():
                    ok = True
                    break
            except Exception:
                pass
            time.sleep(1)
        if not ok:
            log("    [!] 等不到那个链接，放弃。")
            ctx.close()
            return 1
        log("    √ 找到了")
        log(f"步骤 3：点击「{args.click}」")
        try:
            link.first.click(timeout=20000)
        except Exception as e:
            log(f"    [!] 点击失败: {str(e)[:150]}")

        log("步骤 4：等待播放器出现（最多 90 秒）")
        vpage = None
        vids = None
        dl = time.time() + 90
        while time.time() < dl:
            for pg in list(ctx.pages):
                if pg.is_closed():
                    continue
                try:
                    cand = pg.evaluate(LIST_VIDEOS)
                except Exception:
                    continue
                live = [v for v in cand if v["videoWidth"] > 0 and v["w"] > 40]
                if live:
                    vpage, vids = pg, cand
                    break
            if vpage:
                break
            time.sleep(1)
        if not vpage:
            log("    [!] 一直没等到有画面的 video 元素。")
            ctx.close()
            return 1

        log(f"    共找到 {len(vids)} 个 video：")
        for v in vids:
            kind = "有音频(像是摄像头)" if not v["muted"] else "静音(像是电脑屏幕)"
            log(f"      [{v['idx']}] 屏幕尺寸 {v['w']}x{v['h']}  画面 {v['videoWidth']}x{v['videoHeight']}"
                f"  {kind}  时长 {v['duration']:.1f}s")
            log(f"           源: {v['currentSrc'][:150]}")

        playable = [v for v in vids if v["videoWidth"] > 0 and v["w"] > 40]
        if args.video == "all":
            targets = [v["idx"] for v in playable]
        elif args.video == "largest":
            targets = [max(playable, key=lambda v: v["area"])["idx"]]
        else:
            targets = [int(args.video)]
        log(f"    → 本次要解的画面下标: {targets}"
            f"（可用 --video all|largest|<下标> 调整）")

        dur = min(max(v["duration"] or 0 for v in playable), args.max_minutes * 60)
        if dur <= 0:
            log("    [!] 时长读不到，放弃。")
            ctx.close()
            return 1

        times = [round(t, 1) for t in np.arange(2.0, dur, args.step)]
        log("=" * 72)
        log(f"步骤 5：跳过取样 —— 每 {args.step}s 一个点，共 {len(times)} 个点 × "
            f"{len(targets)} 个画面（耗时估计 {len(times) * 0.45 * len(targets) / 60:.1f} 分钟）")

        locators = {i: vpage.locator("video").nth(i) for i in targets}
        saved_count = 0
        t0 = time.time()
        for n, t in enumerate(times, 1):
            for idx in targets:
                try:
                    vpage.evaluate(SEEK, [idx, t])
                except Exception:
                    continue
            time.sleep(0.18)
            for idx in targets:
                try:
                    png = locators[idx].screenshot(timeout=5000)
                except Exception:
                    continue
                try:
                    texts, _arr = decode(png)
                except Exception:
                    continue
                for txt in texts:
                    if txt and (idx, txt) not in hits:
                        hits[(idx, txt)] = t
                        saved = None
                        if saved_count < args.save_max:
                            try:
                                im = Image.open(io.BytesIO(png)).convert("RGB")
                                if im.width > 1280:
                                    im = im.resize((1280, round(im.height * 1280 / im.width)),
                                                   Image.LANCZOS)
                                name = f"v{idx}-t{int(t)}s.jpg"
                                im.save(os.path.join(found_dir, name), "JPEG", quality=80)
                                saved = name
                                saved_count += 1
                            except Exception:
                                saved = None
                        log(f"    ★ [{idx}] 在 {t:.1f}s 解出二维码：")
                        log(f"        {txt[:300]}")
                        if saved:
                            log(f"        （画面已存到 found/{saved}）")
            if n % 25 == 0:
                el = time.time() - t0
                log(f"    ……进度 {n}/{len(times)}（{t:.0f}s / {dur:.0f}s），已发现 {len(hits)} 个码")

        log("=" * 72)
        if hits:
            by_v = {}
            for (idx, _txt), t in hits.items():
                by_v.setdefault(idx, []).append(t)
            log(f"结果：共发现 {len(hits)} 个不同的码")
            for idx, ts in sorted(by_v.items()):
                log(f"   —— 画面 [{idx}]：{len(ts)} 个，时间点 {sorted(round(x) for x in ts)}")
            log("   明细：")
            for (idx, txt), t in sorted(hits.items(), key=lambda kv: kv[1]):
                log(f"   [{idx}] @{t:6.1f}s  {txt[:200]}")
        else:
            log("结果：一个码都没解出来（可能需要调 step / 或换个 --video 下标）")

        try:
            ctx.close()
        except Exception:
            pass

    out = os.path.join(paths.LOGS, args.log)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(LOGS))
    print(f"\n[i] 日志已写入: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
