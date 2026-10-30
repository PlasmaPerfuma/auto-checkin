# -*- coding: utf-8 -*-
"""
自动认出所有课程的 courseId / toolId —— 你不用一门一门点。

原理：登录后走 Canvas 自己的 API（同一套 cookie），
  GET /api/v1/courses?enrollment_state=active&per_page=100   → 课程列表
  GET /api/v1/courses/<id>/tabs                              → 每门课的导航项
  在导航项里找 label 含「课堂视频新版」的那条，它的 html_url 形如
  /courses/<courseId>/external_tools/<toolId>，两个 id 就都在里面了。

结果写到 courses.json，并打印一张表。
用法：python app\\main.py discover
"""
import json
import os
import re
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
BASE = "https://oc.sjtu.edu.cn"

LOGS = []


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    LOGS.append(line)
    print(line, flush=True)


def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, channel="chrome", headless=True, no_viewport=True,
            ignore_default_args=IGNORE_DEFAULT_ARGS, args=ARGS,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # 先确保登录态
        need_login = True
        try:
            r = ctx.request.get(f"{BASE}/api/v1/users/self/profile", timeout=20000)
            if r.status == 200:
                need_login = False
                log(f"√ 已是登录态: {r.json().get('name') or r.json().get('short_name')}")
        except Exception:
            pass

        if need_login:
            log("未登录，开始自动登录（会短暂弹出浏览器）")
            try:
                import sjtu_login
                page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
                do = getattr(sjtu_login, "do_login", None)
                creds = sjtu_login.load_creds() if hasattr(sjtu_login, "load_creds") else None
                if do and creds:
                    ok = do(page, ctx, creds[0], creds[1])
                    log(f"登录 {'成功' if ok else '失败'}")
                else:
                    log("[!] 没有 credentials.json，没法自动登录。请先跑 python app\\main.py login")
                    ctx.close()
                    return 1
            except Exception as e:
                log(f"[!] 登录环节异常: {type(e).__name__}: {str(e)[:150]}")
                ctx.close()
                return 1

        log("拉课程列表…")
        r = ctx.request.get(f"{BASE}/api/v1/courses?enrollment_state=active&per_page=100",
                            timeout=40000)
        if r.status != 200:
            log(f"[!] 拉课程失败 HTTP {r.status}: {r.text()[:200]}")
            ctx.close()
            return 1
        courses = [c for c in r.json() if c.get("name") and c.get("id")]
        log(f"共 {len(courses)} 门课，逐个查「课堂视频新版」的链接…")

        result = []
        for c in courses:
            cid, name = c["id"], c["name"]
            tool_id, hit_label = None, None
            try:
                tr = ctx.request.get(f"{BASE}/api/v1/courses/{cid}/tabs", timeout=25000)
                if tr.status == 200:
                    for t in tr.json():
                        label = (t.get("label") or "")
                        url = (t.get("html_url") or "")
                        if "课堂视频" in label:
                            m = re.search(r"external_tools/(\d+)", url)
                            if m:
                                tool_id = int(m.group(1))
                                hit_label = label
                                break
            except Exception as e:
                log(f"  [{cid}] tabs 异常 {type(e).__name__}")
            row = {"courseId": cid, "toolId": tool_id, "name": name,
                   "code": c.get("course_code") or "", "tabLabel": hit_label}
            result.append(row)
            flag = "√" if tool_id else "×"
            log(f"  {flag} {cid:>7}  tool={tool_id}  {name[:46]}")

        with open(paths.COURSES, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        got = [x for x in result if x["toolId"]]
        log(f"\n拿到 {len(got)} 门课的 toolId，已写入 courses.json")
        log("下一步：我可以按这张表把它们填进 schedule.json")
        try:
            ctx.close()
        except Exception:
            pass

    out = os.path.join(paths.LOGS, "discover.log")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(LOGS))
    print(f"\n[i] 日志: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
