# -*- coding: utf-8 -*-
"""
拿 mlearning 的 Access_Token，并当场验证它能不能真的调通接口。

不需要等签到。流程：
  1. 用独立 Chrome profile 打开 https://mlearning.sjtu.edu.cn/lms/mobile/
  2. 你在里面用 jAccount 登录（账号密码验证码你自己输）
  3. 脚本轮询 localStorage，等 Access_Token 出现
  4. 存到本地文件 mlearning-token.txt（**不会打印全文**，只在屏幕上给你看头尾几位）
  5. 立刻拿这个 token 调一次 /sign/records，验证它是否真的有效

日志：get-token.log
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

SPA = "https://mlearning.sjtu.edu.cn/lms/mobile/"
ROLLCALL = "https://mlearning.sjtu.edu.cn/lms-lti-rollcall-sjtu"

LOGS = []


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    LOGS.append(line)
    print(line, flush=True)


def mask(tok):
    if not tok:
        return "(空)"
    if len(tok) <= 16:
        return tok[:4] + "*" * (len(tok) - 8) + tok[-4:]
    return f"{tok[:8]}…{tok[-6:]}（长度 {len(tok)}）"


READ_STORAGE = """() => {
  const out = {};
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    out[k] = localStorage.getItem(k);
  }
  return out;
}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=SPA)
    ap.add_argument("--wait", type=float, default=300, help="等登录的最长秒数")
    ap.add_argument("--profile", default=PROFILE)
    args = ap.parse_args()

    token = None

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            args.profile, channel="chrome", headless=False, no_viewport=True,
            ignore_default_args=IGNORE_DEFAULT_ARGS, args=ARGS,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        log("=" * 70)
        log(f"步骤 1：打开 {args.url}")
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"    [!] goto: {type(e).__name__}: {str(e)[:120]}")

        log(f"步骤 2：等你在浏览器里用 jAccount 登录（最长 {args.wait:.0f} 秒）")
        log("        —— 登录成功后它会自动跳到应用里；脚本会自己发现 token")
        deadline = time.time() + args.wait
        last_note = 0
        while time.time() < deadline:
            try:
                store = page.evaluate(READ_STORAGE)
            except Exception:
                store = None
            if store:
                tok = store.get("Access_Token") or store.get("accessToken") or store.get("token")
                if tok and len(str(tok)) > 10:
                    token = str(tok)
                    log(f"    √ 拿到 Access_Token: {mask(token)}")
                    log(f"    当前 localStorage 键: {sorted(store.keys())}")
                    break
            if time.time() - last_note > 15:
                last_note = time.time()
                try:
                    cur = page.url
                except Exception:
                    cur = "?"
                log(f"    ……等待中，当前页: {cur[:110]}")

        if not token:
            log("    [!] 没等到 token。可能没登录成功，或 token 存在别的地方。")
            try:
                store = page.evaluate(READ_STORAGE)
                log(f"    localStorage 键: {sorted(store.keys()) if store else '(读不到)'}")
            except Exception:
                pass
            ctx.close()
            out = os.path.join(paths.LOGS, "get-token.log")
            open(out, "w", encoding="utf-8").write("\n".join(LOGS))
            print(f"\n[i] 日志: {out}")
            return 1

        tok_file = paths.TOKEN
        with open(tok_file, "w", encoding="utf-8") as f:
            f.write(token)
        log(f"    已存到 {tok_file}（只有本机有，别贴进对话）")

        log("=" * 70)
        log("步骤 3：拿这个 token 调一次 /sign/records，验证它真的能用")

        def try_get(path, label):
            url = ROLLCALL + path
            try:
                r = ctx.request.get(url, headers={"Authorization": token},
                                    timeout=30000)
                body = r.text()
                log(f"    [{label}] HTTP {r.status}  {url}")
                log(f"        body[:260] = {body[:260]}")
                return r.status, body
            except Exception as e:
                log(f"    [{label}] 异常 {type(e).__name__}: {str(e)[:140]}")
                return None, None

        ok = False
        st, body = try_get("/sign/records", "sign/records 无参")
        if st == 200:
            ok = True
        else:
            st2, body2 = try_get("/sign/records?pageIndex=1&pageSize=10", "sign/records 带分页")
            if st2 == 200:
                ok = True

        log("=" * 70)
        if ok:
            log("★ 结论：Access_Token 有效，接口认这个 Authorization 头。")
            log("  下一步只差「带定位的签到接口参数名」——那个必须看一次真实签到请求。")
        else:
            log("✗ 结论：token 拿到了但接口没通。")
            log("  常见原因：token 要加前缀（如 'Bearer '）、或 /sign/records 需要额外参数。")
            log("  把这段日志发我即可，我按返回内容调整。")

        try:
            ctx.close()
        except Exception:
            pass

    out = os.path.join(paths.LOGS, "get-token.log")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(LOGS))
    print(f"\n[i] 日志: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
