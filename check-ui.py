# -*- coding: utf-8 -*-
"""界面自动检查：不用人点，直接验证 dashboard.html 与命令总线能不能配合工作。

覆盖四类最容易出错、又最难从画面上看出来的问题：
  A. HTML 本身：结构完整、关键元素在
  B. **JS 里引用的每个 id 是否真的存在于 HTML**（写错一个字母就白屏）
  C. 每个只读命令能否真的调通（接口改名/加必填参数会在这里炸）
  D. 写命令的非法输入是否被正确拒绝（校验退化会在这里炸）

在随机端口起一个临时服务，不碰正式后端、不改任何真实数据。
"""
import json
import re
import sys
import threading
import urllib.error
import urllib.request

sys.path.insert(0, "app")
import paths  # noqa: E402

paths.setup_env()
import dashboard as d  # noqa: E402

fail = 0


def check(label, cond, extra=""):
    global fail
    if cond:
        print(f"  OK   {label}")
    else:
        fail += 1
        print(f"  FAIL {label} {extra}")


srv = d.Srv(("127.0.0.1", 0), d.H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"
tok = d.TOKEN
print(f"临时服务: {base}")

HTML = open("app/dashboard.html", encoding="utf-8").read()


def post(name, args=None):
    body = json.dumps({"name": name, "args": args or {}}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/cmd?token={tok}", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


print("\n=== A) HTML 结构 ===")
for k in ("<!DOCTYPE html>", "</html>", "</script>", 'id="rows"',
          'id="frameBox"', 'id="logBody"', 'id="toast"'):
    check(f"含 {k}", k in HTML)

st, page = 200, HTML
with urllib.request.urlopen(f"{base}/", timeout=15) as r:
    st, page = r.status, r.read().decode("utf-8")
check("GET / = 200", st == 200)
check("GET / 返回的就是这份文件", len(page) > 5000 and 'id="rows"' in page,
      f"长度 {len(page)}")

print("\n=== B) JS 引用的每个 id 都必须在 HTML 里存在 ===")
html_ids = set(re.findall(r'id="([A-Za-z][\w-]*)"', HTML))
js_ids = set(re.findall(r'getElementById\("([A-Za-z][\w-]*)"\)', HTML))
missing = sorted(js_ids - html_ids)
check(f"引用的 id 共 {len(js_ids)} 个，全部存在", not missing, f"缺失: {missing}")

# 反向：JS 里 getElementById 出现但 HTML 中完全没有的，一定是笔误
print("     已引用:", ", ".join(sorted(js_ids))[:200])

print("\n=== C) 只读命令全部可调通 ===")
READ_ONLY = ["commands.list", "state.get", "autowatch.status", "log.tail",
             "frame.info", "schedule.get", "schedule.get.all", "course.list",
             "settings.get", "config.backups"]
for name in READ_ONLY:
    try:
        js = post(name)
        check(f"{name}", js.get("ok") is True, str(js.get("error"))[:120])
    except Exception as e:
        check(f"{name}", False, f"{type(e).__name__}: {e}")

print("\n=== D) 写命令的非法输入必须被拒（不真写）===")
cases = [
    ("schedule.setEnabled", {"index": 9999, "enabled": False}, "bad_index"),
    ("schedule.update", {"index": 0, "patch": {"day": "周八"}}, "bad_day"),
    ("settings.set", {}, "bad_args"),
    ("settings.set", {"checkin": {"lat": 40.0}}, "out_of_campus"),
    ("ui.report", {}, "bad_args"),
    ("no.such.command", {}, "unknown_command"),
]
for name, args, want in cases:
    js = post(name, args)
    got = (js.get("error") or {}).get("code")
    ok = js.get("ok") is False and got == want
    check(f"{name} {args} -> {want}", ok, f"实际 {got}")

print("\n=== E) 命令自我描述（给 AI / 表单用）===")
js = post("commands.list")
cmds = js.get("data") or []
check("命令数 >= 19", len(cmds) >= 19, str(len(cmds)))
check("每条都有 name/desc/args",
      all({"name", "desc", "args"} <= set(c) for c in cmds))
names = [c["name"] for c in cmds]
for need in ("state.get", "schedule.setEnabled", "schedule.remove",
             "settings.set", "ui.report"):
    check(f"含 {need}", need in names)

print("\n=== F) 前端异常自报能落盘 ===")
js = post("ui.report", {"kind": "selftest", "message": "check-ui 自检写入", "line": 1})
check("ui.report 可调用", js.get("ok") is True, str(js.get("error")))
import os  # noqa: E402
p = os.path.join(paths.LOGS, "ui-error.log")
check("ui-error.log 已生成", os.path.isfile(p))
if os.path.isfile(p):
    txt = open(p, encoding="utf-8").read()
    check("日志里有刚才那条", "check-ui 自检写入" in txt)
    # 自检自己写进去的痕迹不要留下，免得污染真实排错
    kept = [ln for ln in txt.split("\n-") if "check-ui 自检写入" not in ln]
    try:
        open(p, "w", encoding="utf-8").write("\n-".join(kept))
    except Exception:
        pass

print("\n=== G) 无效画面判定（纯色画面不能当成正常画面）===")
# 造三张图到 tmp/ 里测，用完就删 —— 绝不放进 output/，免得被当成本次真实抓图
import os  # noqa: E402
import random  # noqa: E402

tmpdir = paths.TMP
made = []
try:
    from PIL import Image as _Image

    def _mk(name, fill):
        p = os.path.join(tmpdir, name)
        _Image.new("RGB", (320, 180), fill).save(p)
        made.append(p)
        return p

    p_white = _mk("_blank_white.png", (255, 255, 255))
    p_black = _mk("_blank_black.png", (0, 0, 0))
    p_gray = _mk("_blank_gray.png", (18, 18, 18))

    # 造一张"有内容"的图：随机构造，标准差很大
    p_busy = os.path.join(tmpdir, "_blank_busy.png")
    im = _Image.new("RGB", (320, 180))
    px = im.load()
    rnd = random.Random(7)
    for y in range(180):
        for x in range(320):
            v = rnd.randint(0, 255)
            px[x, y] = (v, v, v)
    im.save(p_busy)
    made.append(p_busy)

    check("纯白判为空白", d.is_blank_image(p_white) is True)
    check("纯黑判为空白", d.is_blank_image(p_black) is True)
    check("深灰纯色判为空白", d.is_blank_image(p_gray) is True)
    check("有内容的图不算空白", d.is_blank_image(p_busy) is False)
    check("文件不存在时不报错（当作非空白）", d.is_blank_image("Z:/nope/x.png") is False)
    check("缓存在同一 mtime 下复用（二次调用仍正确）",
          d.is_blank_image(p_white) is True)
finally:
    for f in made:
        try:
            os.remove(f)
        except Exception:
            pass
    d._blank_cache.clear()
check("测试图已清理干净",
      not any(os.path.exists(os.path.join(tmpdir, n))
              for n in ("_blank_white.png", "_blank_black.png",
                        "_blank_gray.png", "_blank_busy.png")))

srv.shutdown()
print()
print("失败数:", fail)
sys.exit(1 if fail else 0)
