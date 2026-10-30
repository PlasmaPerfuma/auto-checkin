# -*- coding: utf-8 -*-
"""命令总线的 HTTP 端到端测试：在随机端口起一个真实服务，用真 HTTP 请求打。

不碰正式后端（不写 dashboard-url.txt / dashboard.pid），不启停任何进程。
"""
import json
import sys
import threading
import urllib.error
import urllib.request

sys.path.insert(0, "app")
import paths

paths.setup_env()
import dashboard as d

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
print(f"测试服务: {base}  token={tok[:6]}...")

RO_CMDS_FORBIDDEN = {"autowatch.start", "autowatch.stop", "autowatch.restart"}


def post(name, args=None):
    body = json.dumps({"name": name, "args": args or {}}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/cmd?token={tok}", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def get(path):
    with urllib.request.urlopen(f"{base}{path}", timeout=10) as r:
        return r.status, r.read()


print("=== 1) POST /api/cmd → commands.list ===")
st, js = post("commands.list")
check("HTTP 200", st == 200)
check("ok=True", js.get("ok") is True, str(js)[:120])
check("data 是列表", isinstance(js.get("data"), list))
check("命令数 >= 10", len(js["data"]) >= 10)

print("=== 2) POST /api/cmd → state.get（真 HTTP 拿到的结构与页面一致）===")
st, js = post("state.get")
check("ok=True", js.get("ok") is True, str(js)[:120])
data = js.get("data") or {}
need = {"now", "week", "running", "pid", "status", "heartbeat", "next", "sessions", "frame"}
check("字段齐全", need <= set(data), f"缺 {need - set(data)}")
check("heartbeat 带 phase", "phase" in (data.get("heartbeat") or {}))
print("     running=%s week=%s sessions=%s phase=%s stale=%s" % (
    data.get("running"), data.get("week"), len(data.get("sessions") or []),
    (data.get("heartbeat") or {}).get("phase"),
    (data.get("heartbeat") or {}).get("stale")))

print("=== 3) 未知命令 ===")
st, js = post("nope.nope")
check("HTTP 仍 200（业务失败不是 HTTP 错误）", st == 200)
check("ok=False", js.get("ok") is False)
check("code=unknown_command", (js.get("error") or {}).get("code") == "unknown_command")

print("=== 4) 坏 JSON ===")
req = urllib.request.Request(f"{base}/api/cmd?token={tok}", data=b"{not json",
                             method="POST", headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=10) as r:
    st, raw = r.status, r.read()
js = json.loads(raw.decode("utf-8"))
check("HTTP 200", st == 200)
check("code=bad_json", (js.get("error") or {}).get("code") == "bad_json", str(js))

print("=== 5) 无 token 必须被拒 ===")
try:
    urllib.request.urlopen(
        urllib.request.Request(f"{base}/api/cmd", data=b"{}", method="POST"), timeout=10)
    check("无 token 被拒", False, "居然通过了")
except urllib.error.HTTPError as e:
    check("无 token 被拒（403）", e.code == 403, f"实际 {e.code}")

print("=== 6) 旧接口 /api/state 仍然可用（向后兼容）===")
st, raw = get(f"/api/state?token={tok}")
js = json.loads(raw.decode("utf-8"))
check("HTTP 200", st == 200)
check("ok=True", js.get("ok") is True)
check("字段齐全", need <= set(js), f"缺 {need - set(js)}")

print("=== 7) 旧接口 /api/log ===")
st, raw = get(f"/api/log?token={tok}&n=2")
js = json.loads(raw.decode("utf-8"))
check("ok=True 且有 2 行", js.get("ok") is True and len(js.get("lines") or []) == 2)

print("=== 8) /api/frame 对过期图返回 200+JSON（不是 500）===")
st, raw = get(f"/api/frame?token={tok}")
check("HTTP 200", st == 200)
js = json.loads(raw.decode("utf-8"))
check("返回 JSON 说明", js.get("ok") is False and js.get("reason") in ("stale", "empty"), str(js))

srv.shutdown()
print()
print("失败数:", fail)
sys.exit(1 if fail else 0)
