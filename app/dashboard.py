# -*- coding: utf-8 -*-
"""
本地控制台（纯标准库，零依赖）。

启动一个只绑 127.0.0.1 的 HTTP 服务，页面用 Edge/Chrome 的 --app 模式打开，
看起来就像一个独立小软件（没有地址栏、没有标签页）。

功能：
  · 看 autowatch 是否在跑、下一节课是什么
  · 看本周课表与每条监视窗口
  · 实时日志
  · 最新抓到的签到画面
  · 一键启停 autowatch

安全：只监听 127.0.0.1，且每次启动生成随机 token，URL 与接口都要带它，
避免本机其它程序（或误开的网页）随便点。
"""
import argparse
import datetime as dt
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paths

paths.setup_env()

# 命令总线：界面与将来的 AI 都只通过它调用功能（见 commands.py 顶部注释）
import commands

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

APP = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(APP, "dashboard.html")
PYEXE = sys.executable
PYWEXE = os.path.join(os.path.dirname(PYEXE), "pythonw.exe")

AUTOWATCH = os.path.join(APP, "autowatch.py")
LOG = os.path.join(paths.LOGS, "autowatch.log")
STATUS_JSON = os.path.join(paths.LOGS, "autowatch-status.json")
PID_FILE = os.path.join(paths.LOGS, "autowatch.pid")
FRAMES = [
    # 监视期间每 5 秒覆盖一张，用来在控制台看"它现在在看什么"
    os.path.join(paths.OUTPUT, "autowatch-live.png"),
    os.path.join(paths.OUTPUT, "autowatch-qr.png"),
    os.path.join(paths.OUTPUT, "checkin-qr.png"),
    os.path.join(paths.OUTPUT, "checkin-qr.jpg"),
    os.path.join(paths.OUTPUT, "live-last-frame.png"),
]

# 画面超过这么久没更新就判为「过期」，界面上不再展示旧图
FRESH_SEC = 300.0

DAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
TOKEN = secrets.token_urlsafe(12)


# ------------------------------------------------------------------ 工具
def read_json(p, default=None):
    try:
        with open(p, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def pid_alive(pid):
    if not pid:
        return False
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259
    except Exception:
        return False


def tail(path, n=200):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return [ln.rstrip("\n") for ln in lines[-n:]]
    except Exception:
        return []


_BLANK_THRESHOLD = 2.0      # 灰度标准差低于这个值 = 纯色画面
_blank_cache = {}           # path -> (mtime, bool)，避免每次轮询都重算


def is_blank_image(path):
    """判断一张截图是不是"纯色画面"（全黑 / 全白 / 单色）。

    为什么要判：直播还没开始时，页面那块就是一片黑或一片白。旧逻辑只看文件
    新不新，于是把这种"什么都没有"的画面当成正常画面显示 —— 用户分不清
    「程序坏了」和「确实还没开始」。

    做法：缩到 160px 再算灰度标准差。幻灯片/摄像头画面标准差通常 20~60，
    纯色画面接近 0。慢得要死的话也不会 —— 结果按 (path, mtime) 缓存。
    任何异常都当成"不是空白"（宁可多显示，也不要误判成没画面）。
    """
    try:
        st = os.stat(path)
        key = (st.st_mtime, st.st_size)
        hit = _blank_cache.get(path)
        if hit and hit[0] == key:
            return hit[1]

        from PIL import Image
        import numpy as np

        with Image.open(path) as im:
            im = im.convert("L")
            im.thumbnail((160, 160))
            arr = np.asarray(im, dtype="float32")
        blank = float(arr.std()) < _BLANK_THRESHOLD

        _blank_cache.clear()          # 只留最后一张，别让字典无限长
        _blank_cache[path] = (key, blank)
        return blank
    except Exception:
        return False


def frame_info():
    """返回最新一张抓图的信息。

    关键：**绝不因为"没有新图"而报错或空白**。过期不是错误，是正常状态
    （没在监视时本来就不该有画面），所以这里永远返回一个结构，
    由调用方决定怎么展示；出错也只降级成 available=False + reason。
    """
    best = None
    for f in FRAMES:
        try:
            if not os.path.isfile(f):
                continue
            st = os.stat(f)
            age = time.time() - st.st_mtime
            if best is None or st.st_mtime > best["mtime"]:
                best = {"path": f, "name": os.path.basename(f),
                        "mtime": st.st_mtime, "age": age,
                        "size": st.st_size}
        except OSError:
            continue          # 单个文件读不到不影响其它
        except Exception:
            continue
    if best is None:
        return {"available": False, "reason": "empty",
                "label": "还没有截到画面", "age": None}
    age = max(0.0, best["age"])
    if age > FRESH_SEC:
        return {"available": False, "reason": "stale",
                "label": "未在抓取画面", "age": round(age, 1),
                "last": best["name"],
                "lastTime": dt.datetime.fromtimestamp(best["mtime"]
                                                      ).strftime("%m-%d %H:%M:%S")}
    # 图片是新的，但如果整张是纯色（黑屏/白屏），同样不当作有效画面
    if is_blank_image(best["path"]):
        return {"available": False, "reason": "blank",
                "label": "画面是空白的（直播可能还没开始）",
                "age": round(age, 1), "last": best["name"],
                "lastTime": dt.datetime.fromtimestamp(best["mtime"]
                                                      ).strftime("%H:%M:%S")}
    return {"available": True, "reason": "fresh", "label": "正在抓取画面",
            "age": round(age, 1), "name": best["name"], "path": best["path"],
            "takenAt": dt.datetime.fromtimestamp(best["mtime"]).strftime("%H:%M:%S")}


def week_sessions(include_disabled=True):
    """本周全部课 + 每条的监视窗口。

    include_disabled 默认 True：**界面**要看到停用的课（否则取消勾选后整行消失，
    用户会以为"被删掉了"）。真正执行用的 scheduler.build_sessions() 仍默认跳过。
    """
    import scheduler
    S = read_json(paths.SCHEDULE, {}) or {}
    sem = dt.date.fromisoformat(S["semesterStart"])
    today = dt.date.today()
    wk = (today - sem).days // 7 + 1
    if wk < 1:
        return wk, []
    sessions, _sk, _sem = scheduler.build_sessions(wk, include_disabled=include_disabled)
    post_min = ((read_json(paths.PERIODS, {}) or {}).get("watch") or {}).get("post_min", 5)
    extra = 10 - post_min
    out = []
    for s in sessions:
        a = s["watch"][0]
        b = s["watch"][1] + dt.timedelta(minutes=extra)
        out.append({
            "date": s["date"].isoformat(),
            "day": s["day"],
            "course": s["course"],
            "courseId": s.get("courseId"),
            "periods": s.get("periods") or [],
            "room": s.get("room", ""),
            "start": a.strftime("%m-%d %H:%M"),
            "end": b.strftime("%H:%M"),
            "startISO": a.isoformat(),
            "endISO": b.isoformat(),
            "isToday": s["date"] == today,
            "passed": b < dt.datetime.now(),
            "enabled": s.get("enabled", True),
        })
    return wk, out


def next_one(sessions):
    now = dt.datetime.now()
    for s in sessions:
        if s.get("enabled", True) is False:
            continue          # 停用的课不该被当成"下一节"
        if dt.datetime.fromisoformat(s["endISO"]) > now:
            return s
    return None


def start_autowatch():
    if pid_alive(read_json_text(PID_FILE)):
        return False, "已经在运行"

    env = dict(os.environ)
    env["PYTHONPATH"] = paths.LIBS
    env["PYTHONIOENCODING"] = "utf-8"
    for k in ("TEMP", "TMP", "TMPDIR"):
        env[k] = paths.TMP

    if paths.FROZEN:
        # 打包后没有解释器也没有 app\autowatch.py 的"可执行"形式，
        # 必须重新拉起自己，让 main.py 用 --internal-run 在进程内执行它。
        cmd = [sys.executable, "--internal-run", "autowatch.py", "--run"]
    else:
        exe = PYWEXE if os.path.isfile(PYWEXE) else PYEXE
        cmd = [exe, AUTOWATCH, "--run"]

    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(cmd, cwd=paths.ROOT, env=env,
                     creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    return True, "已启动"


def read_json_text(p):
    try:
        return int(open(p).read().strip())
    except Exception:
        return None


def pid_image(pid):
    """取进程的可执行文件全路径；取不到返回空串。

    用 QueryFullProcessImageNameW，只要拿得到 QUERY_LIMITED_INFORMATION 就能读，
    不像 tasklist 那样在受限环境里会看不到别的进程。
    """
    if not pid:
        return ""
    try:
        import ctypes
        import ctypes.wintypes as wt
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value or ""
            return ""
        finally:
            k.CloseHandle(h)
    except Exception:
        return ""


def pid_is_ours(pid):
    """确认这个 PID 真的是我们的 python 进程。

    为什么需要：PID 会被系统复用。若 autowatch 早已退出、PID 文件残留，
    那个号可能已经分给了别的程序 —— 直接 os.kill 就会误杀无关进程。
    查不到映像名时一律当作"不是我们的"，宁可不杀。
    """
    if not pid:
        return False
    img = pid_image(pid).lower()
    if not img:
        return False
    return img.endswith("pythonw.exe") or img.endswith("python.exe")


def stop_autowatch():
    pid = read_json_text(PID_FILE)
    if not pid_alive(pid):
        return False, "没在运行"
    if not pid_is_ours(pid):
        img = pid_image(pid) or "unknown"
        return False, (f"PID {pid} 不是 autowatch（映像是 {img}），"
                       f"可能已被系统复用，为安全起见不杀；请手动检查该进程")
    try:
        os.kill(pid, 15)   # Windows 上等价 TerminateProcess 语义
        return True, f"已停止 PID {pid}"
    except PermissionError:
        return False, (f"权限不足，停不掉 PID {pid}（它可能在更高权限下启动）。"
                       f"请双击 stop-autowatch.cmd 用普通权限停止")
    except Exception as e:
        return False, f"停止失败: {e}"


# ------------------------------------------------------------------ HTTP
class Srv(ThreadingHTTPServer):
    # Windows 上 HTTPServer 默认 allow_reuse_address=1 会让两个进程**同时**
    # 绑上同一端口，导致"端口已占用"的检测永远失效。关掉它，bind 才会报错。
    allow_reuse_address = False
    daemon_threads = True


class H(BaseHTTPRequestHandler):
    server_version = "autowatch-ui/1.0"

    def log_message(self, *a):
        pass

    # ---- 基础响应 ----
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _bad(self, msg):
        self._send(403, {"ok": False, "error": msg})

    def _authed(self, q):
        return q.get("token", [""])[0] == TOKEN

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = u.path

        if p in ("/", "/index.html"):
            # 页面本身不校验 token（它自己会把 token 拼进 API 请求）
            try:
                html = open(HTML, encoding="utf-8").read()
            except Exception as e:
                return self._send(500, f"dashboard.html 读取失败: {e}", "text/plain; charset=utf-8")
            return self._send(200, html, "text/html; charset=utf-8")

        if p == "/favicon.ico":
            return self._send(204, b"")

        if not self._authed(q):
            return self._bad("token 无效")

        if p == "/api/state":
            # 走命令总线，和 /api/cmd 用同一份实现，避免两套逻辑漂移
            ok, err, data = commands.dispatch("state.get", {})
            if not ok:
                return self._send(200, {"ok": False, "error": err})
            return self._send(200, {"ok": True, **data})

        if p == "/api/log":
            n = int((q.get("n") or ["160"])[0])
            return self._send(200, {"ok": True, "lines": tail(LOG, max(1, min(2000, n)))})

        if p == "/api/frame":
            fi = frame_info()
            if not fi.get("available"):
                # 过期/没有图不是错误：返回 200 + 一句说明，让页面自己决定怎么画，
                # 免得控制台里冒出一个「加载失败」的红叉
                return self._send(200, {"ok": False,
                                        "reason": fi.get("reason"),
                                        "label": fi.get("label")})
            try:
                data = open(fi["path"], "rb").read()
                # 按**文件头**判断真实格式，别信扩展名：
                # checkin-qr.jpg 其实是 Playwright 截出来的 PNG，按扩展名给 content-type 会让网页加载失败
                if data[:8] == b"\x89PNG\r\n\x1a\n":
                    ctype = "image/png"
                elif data[:2] == b"\xff\xd8":
                    ctype = "image/jpeg"
                elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
                    ctype = "image/webp"
                elif data[:3] == b"GIF":
                    ctype = "image/gif"
                else:
                    return self._send(200, {"ok": False, "reason": "unreadable",
                                            "label": "画面格式无法识别"})
                return self._send(200, data, ctype)
            except Exception as e:
                return self._send(200, {"ok": False, "reason": "unreadable",
                                        "label": f"画面读不出来：{type(e).__name__}"})

        return self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if not self._authed(q):
            return self._bad("token 无效")

        # —— 命令总线 ——
        # POST /api/cmd?token=...   body: {"name": "log.tail", "args": {"n": 50}}
        # 返回 {ok: true, data: ...} 或 {ok: false, error: {code, message}}
        # 永远 200：业务失败不是 HTTP 错误，界面只看 ok。
        if u.path == "/api/cmd":
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except Exception:
                n = 0
            raw = self.rfile.read(n) if n > 0 else b""
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                return self._send(200, {"ok": False,
                                        "error": {"code": "bad_json",
                                                  "message": "请求体不是合法 JSON"}})
            if not isinstance(payload, dict):
                return self._send(200, {"ok": False,
                                        "error": {"code": "bad_json",
                                                  "message": "请求体必须是 JSON 对象"}})
            name = payload.get("name") or ""
            args = payload.get("args")
            if not isinstance(args, dict):
                args = {}
            ok, err, data = commands.dispatch(name, args)
            if not ok:
                return self._send(200, {"ok": False, "error": err})
            return self._send(200, {"ok": True, "data": data})

        # 旧接口保留：老页面/老脚本还在用，改成走同一条命令
        if u.path == "/api/start":
            ok, err, data = commands.dispatch("autowatch.start", {})
            return self._send(200, {"ok": ok,
                                    "msg": (data or {}).get("message") if ok else (err or {}).get("message")})
        if u.path == "/api/stop":
            ok, err, data = commands.dispatch("autowatch.stop", {})
            return self._send(200, {"ok": ok,
                                    "msg": (data or {}).get("message") if ok else (err or {}).get("message")})
        return self._send(404, {"ok": False, "error": "not found"})


def _find_app_window(title_hint, width, height, timeout=12.0):
    """找 Edge/Chrome 的 --app 顶层窗口 hwnd。优先按标题，其次按尺寸。"""
    try:
        import ctypes
        import ctypes.wintypes as wt
    except Exception:
        return None
    u32 = ctypes.windll.user32
    deadline = time.time() + timeout
    while time.time() < deadline:
        hits = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def cb(hwnd, _):
            if not u32.IsWindowVisible(hwnd):
                return True
            n = u32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            r = wt.RECT()
            u32.GetWindowRect(hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if buf.value == title_hint:
                hits.append((0, hwnd, w, h))
            elif abs(w - width) <= 40 and abs(h - height) <= 60:
                hits.append((1, hwnd, w, h))
            return True

        u32.EnumWindows(cb, 0)
        if hits:
            hits.sort(key=lambda x: x[0])
            return hits[0][1]
        time.sleep(0.4)
    return None


def _strip_frame(hwnd, x, y, w, h):
    """去掉标题栏与边框，并摆到指定位置/尺寸。"""
    try:
        import ctypes
        u32 = ctypes.windll.user32
        GWL_STYLE = -16
        WS_CAPTION = 0x00C00000
        WS_THICKFRAME = 0x00040000
        WS_SYSMENU = 0x00080000
        WS_MINIMIZEBOX = 0x00020000
        WS_MAXIMIZEBOX = 0x00010000
        WS_POPUP = 0x80000000
        SWP_FRAMECHANGED = 0x0020
        SWP_SHOWWINDOW = 0x0040
        style = u32.GetWindowLongW(hwnd, GWL_STYLE)
        new = (style & ~(WS_CAPTION | WS_THICKFRAME | WS_SYSMENU |
                         WS_MINIMIZEBOX | WS_MAXIMIZEBOX)) | WS_POPUP
        u32.SetWindowLongW(hwnd, GWL_STYLE, new & 0xFFFFFFFF)
        u32.SetWindowPos(hwnd, 0, int(x), int(y), int(w), int(h),
                         SWP_FRAMECHANGED | SWP_SHOWWINDOW)
        return True
    except Exception:
        return False


def open_app_window(url, width=1180, height=820):
    """用 Edge/Chrome 的 --app 模式打开，并去掉系统标题栏 → 看起来就是独立软件。"""
    # 屏幕居中
    x = y = 80
    try:
        import ctypes
        u32 = ctypes.windll.user32
        sw, sh = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2 - 30)
    except Exception:
        pass

    cands = [
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                     r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     r"Google\Chrome\Application\chrome.exe"),
    ]
    for exe in cands:
        if not os.path.isfile(exe):
            continue
        try:
            subprocess.Popen([exe, f"--app={url}",
                              f"--window-size={width},{height}",
                              f"--window-position={x},{y}",
                              "--no-first-run",
                              "--no-default-browser-check",
                              "--disable-features=msEdgeFirstRunExperience",
                              "--user-data-dir=" + os.path.join(paths.TMP, "uiapp")],
                             close_fds=True)
        except Exception:
            continue
        hwnd = _find_app_window("自动签到控制台", width, height)
        if hwnd:
            _strip_frame(hwnd, x, y, width, height)
        return exe
    try:
        os.startfile(url)   # 兜底：默认浏览器（会有标题栏）
    except Exception:
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--no-open", action="store_true", help="不自动开窗口")
    args = ap.parse_args()

    try:
        srv = Srv(("127.0.0.1", args.port), H)
    except OSError:
        # 端口已被占用：可能真是另一个控制台在跑，也可能是别的程序占着。
        # ⚠️ 必须核对端口——dashboard-url.txt 可能残留着别的端口的旧 URL，
        # 直接拿去开窗口会得到"127.0.0.1 拒绝连接"（2026-10-28 真实发生）。
        uf = os.path.join(paths.LOGS, "dashboard-url.txt")
        old = ""
        try:
            old = open(uf, encoding="utf-8").read().strip()
        except Exception:
            old = ""
        if ":{0}/".format(args.port) in old:
            print(f"已有控制台在运行，直接打开：{old}", flush=True)
            if not args.no_open:
                open_app_window(old)
        else:
            print(f"[!] 端口 {args.port} 被别的程序占用了，而且没有对应的控制台地址。", flush=True)
            print("    先关掉占用它的程序，或者重启电脑后再试。", flush=True)
        return
    url = f"http://127.0.0.1:{args.port}/?token={TOKEN}"
    url_file = os.path.join(paths.LOGS, "dashboard-url.txt")
    pid_file = os.path.join(paths.LOGS, "dashboard.pid")
    try:
        with open(url_file, "w", encoding="utf-8") as f:
            f.write(url)
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    print("=" * 64, flush=True)
    print("  自动签到控制台已启动", flush=True)
    print(f"  {url}", flush=True)
    print("=" * 64, flush=True)
    if not args.no_open:
        exe = open_app_window(url)
        print(f"  已用 {os.path.basename(exe) if exe else '默认浏览器'} 打开窗口")
    print("  （关掉这个窗口/按 Ctrl+C 结束控制台）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()
        try:
            os.remove(os.path.join(paths.LOGS, "dashboard.pid"))
        except Exception:
            pass


def _log_crash():
    """把崩溃写进 logs/dashboard-crash.log。

    为什么需要：启动器用 pythonw.exe 拉起本进程，**它没有控制台**，
    stdout/stderr 全是冲掉的 —— 任何异常都表现为"进程悄悄没了"，
    启动器只能报一句"backend did not start"，什么线索都没有。
    """
    import traceback
    try:
        os.makedirs(paths.LOGS, exist_ok=True)
        p = os.path.join(paths.LOGS, "dashboard-crash.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            f.write(dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
            f.write(traceback.format_exc())
            f.write("\n")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        _log_crash()
        raise
