# -*- coding: utf-8 -*-
"""
抓码观察器 —— 只观察，不提交任何东西。

作用：盯着屏幕（或指定的直播窗口），把出现的二维码实时解出来并打印，
      记录每个码的出现时间 / 存活时长 / 内容变化，回答三个问题：
        1. 直播画面里的二维码，到底能不能稳定解出来？
        2. 码内容真的是每 3 秒变一次吗？变的是哪一段？
        3. 我们一轮"截图→解码"要多久，够不够抢在码失效前？

输出：
  控制台实时打印 + codes.jsonl（每解出一个码追加一行）
  结束后打印每个码的存活时长汇总。

用法：
  最省事：直接双击 run-watch.cmd（盯全屏）
  只盯直播窗口：python watch.py --window "直播"
  解出网址后自动在浏览器打开一次：python watch.py --open
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time

import numpy as np
import zxingcpp
import mss

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import paths                      # 项目唯一的路径真源（app/paths.py）
HERE = paths.ROOT                 # 兼容旧代码：HERE 一律指项目根
LOG_PATH = os.path.join(paths.OUTPUT, "codes.jsonl")

_kernel32 = ctypes.windll.kernel32
_user32 = ctypes.windll.user32


def set_title(text):
    try:
        _kernel32.SetConsoleTitleW(text)
    except Exception:
        pass


def find_windows(substr):
    """按标题片段找可见窗口，返回 [(hwnd, title), ...]"""
    hits = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        n = _user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            _user32.GetWindowTextW(hwnd, buf, n + 1)
            if substr.lower() in buf.value.lower():
                hits.append((hwnd, buf.value))
        return True

    _user32.EnumWindows(proc(cb), 0)
    return hits


def window_rect(hwnd):
    r = wt.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def clamp_region(region, full):
    """把区域裁到屏幕范围内，mss 不接受越界。"""
    left = max(region["left"], full["left"])
    top = max(region["top"], full["top"])
    right = min(region["left"] + region["width"], full["left"] + full["width"])
    bottom = min(region["top"] + region["height"], full["top"] + full["height"])
    if right <= left or bottom <= top:
        return dict(full)
    return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def parse_region(s):
    x, y, w, h = (int(v) for v in s.replace(" ", "").split(","))
    return {"left": x, "top": y, "width": w, "height": h}


def shorten(text, width=110):
    text = text.replace("\n", "\\n")
    return text if len(text) <= width else text[: width - 1] + "…"


def main():
    ap = argparse.ArgumentParser(description="二维码抓码观察器（只观察，不提交）")
    ap.add_argument("--window", default=None, help="只截标题包含该文字的窗口")
    ap.add_argument("--region", default=None, help="手动指定截取区域 x,y,w,h")
    ap.add_argument("--open", action="store_true", help="首次解出网址时自动用浏览器打开一次")
    ap.add_argument("--seconds", type=float, default=0, help="运行时长（秒），0 表示一直跑到你按 Ctrl+C")
    ap.add_argument("--save-dir", default=None, help="把截到二维码的那一帧存成 PNG，便于排查")
    ap.add_argument("--shot", default=None, help="只抓一帧存成 PNG 后退出（用来检查抓屏是不是黑的）")
    args = ap.parse_args()

    with mss.MSS() as sct:
        full = dict(sct.monitors[0])
        print(f"[i] 屏幕: {full['width']}x{full['height']}")

        region = full
        if args.region:
            region = clamp_region(parse_region(args.region), full)
        elif args.window:
            hits = find_windows(args.window)
            if not hits:
                print(f"[!] 没找到标题包含「{args.window}」的可见窗口。")
                print("    先把直播窗口打开、标题露出来，再重跑；或者不加 --window 直接截全屏。")
                return 1
            for hwnd, title in hits[:10]:
                x, y, w, h = window_rect(hwnd)
                print(f"[i] 窗口 0x{hwnd:X}  {w}x{h} @ ({x},{y})  {title}")
            x, y, w, h = window_rect(hits[0][0])
            region = clamp_region({"left": x, "top": y, "width": w, "height": h}, full)
        print(f"[i] 截取区域: {region['width']}x{region['height']} @ ({region['left']},{region['top']})")

        if args.shot:
            raw = sct.grab(region)
            shot = np.asarray(raw)
            mss.tools.to_png(raw.rgb, raw.size, output=args.shot)
            mean = shot[:, :, :3].mean()
            print(f"[i] 已保存一帧到 {args.shot}（平均亮度 {mean:.1f}/255）")
            if mean < 5:
                print("[!] 这一帧几乎是全黑的 —— 说明抓不到画面内容。")
                print("    常见于浏览器/播放器开了硬件加速。解决办法：")
                print("    在浏览器设置里关掉『使用硬件加速』，或把播放器切成窗口模式，再试。")
            else:
                print("    （亮度正常，说明能抓到画面）")
            return 0

        if args.save_dir:
            os.makedirs(args.save_dir, exist_ok=True)

        seen = {}          # text -> {first, last, count}
        order = []         # 首次出现顺序
        last_text = None
        last_change = None
        opened = False
        frames = 0
        t_start = time.time()
        next_beat = t_start + 2.0

        logf = open(LOG_PATH, "a", encoding="utf-8")
        print("[i] 开始盯屏……（随时 Ctrl+C 结束，会自动打印汇总）")
        print("-" * 72)

        try:
            while True:
                loop_t0 = time.perf_counter()
                raw = sct.grab(region)
                rgb = np.asarray(raw)[:, :, :3]

                found = zxingcpp.read_barcodes(
                    rgb, formats=zxingcpp.BarcodeFormat.QRCode
                )
                now = time.time()
                frames += 1

                text = found[0].text if found else None

                if found and not text:
                    # 看见了二维码但认不出内容 —— 说明码太小/太糊/被压花了
                    if last_text != "<UNREADABLE>":
                        print(f"[{time.strftime('%H:%M:%S')}] 检测到二维码但内容无法识别 "
                              f"（码可能太小或被视频压花了）")
                        last_text = "<UNREADABLE>"

                if text:
                    is_new = text != last_text
                    if text not in seen:
                        seen[text] = {"first": now, "last": now, "count": 0}
                        order.append(text)
                    seen[text]["last"] = now
                    seen[text]["count"] += 1

                    if is_new:
                        gap = f"  (上一个码活了 {now - last_change:.2f}s)" if last_change else ""
                        print(f"[{time.strftime('%H:%M:%S')}]{gap}  #{order.index(text) + 1}")
                        print(f"    {shorten(text)}")
                        last_text = text
                        last_change = now

                        logf.write(json.dumps(
                            {"t": now, "iso": time.strftime("%Y-%m-%d %H:%M:%S"), "text": text},
                            ensure_ascii=False) + "\n")
                        logf.flush()

                        if args.save_dir:
                            name = time.strftime("qr-%H%M%S-") + f"{int(now * 1000) % 1000:03d}.png"
                            mss.tools.to_png(raw.rgb, raw.size,
                                             output=os.path.join(args.save_dir, name))

                        if args.open and not opened and text.lower().startswith(("http://", "https://")):
                            opened = True
                            print("    >>> 已在浏览器打开该网址（只开这一次；"
                                  "请开着 F12 的 Network 面板走完流程）")
                            os.startfile(text)

                if now >= next_beat:
                    fps = frames / (now - t_start)
                    set_title(f"抓码中… 已见 {len(order)} 个码 | {fps:.1f} fps")
                    if last_text is None:
                        print(f"    ……等待中 ({time.strftime('%H:%M:%S')}, {fps:.1f} fps)")
                    next_beat = now + 2.0

                if args.seconds and now - t_start >= args.seconds:
                    break

                # 控制节奏：每轮最多占用 ~80ms
                spent = time.perf_counter() - loop_t0
                if spent < 0.08:
                    time.sleep(0.08 - spent)

        except KeyboardInterrupt:
            print("\n[i] 收到 Ctrl+C，收工。")
        finally:
            logf.close()
            set_title("抓码观察器")

    print("-" * 72)
    if not order:
        print("[结果] 一个二维码都没解出来。")
        print("       可能原因：① 盯屏期间签到码没出现；② 直播画面里码太小/太糊；")
        print("       ③ 窗口被遮挡；④ 画面是纯黑的（GPU 全屏独占，需要换抓屏方式）。")
        return 2

    print(f"[结果] 共解出 {len(order)} 个不同的码：")
    for i, text in enumerate(order, 1):
        info = seen[text]
        life = info["last"] - info["first"]
        print(f"  #{i}  存活 {life:6.2f}s  读到 {info['count']:3d} 次   {shorten(text, 80)}")

    if len(order) >= 2:
        gaps = [seen[order[i]]["first"] - seen[order[i - 1]]["first"] for i in range(1, len(order))]
        avg = sum(gaps) / len(gaps)
        print(f"\n[结果] 相邻两个新码的平均间隔：{avg:.2f}s（学校说 3 秒一变，对得上吗？）")

    print(f"\n[i] 完整记录已写入: {LOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
