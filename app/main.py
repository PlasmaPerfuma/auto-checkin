# -*- coding: utf-8 -*-
r"""
auto-checkin 的**单一入口**。

为什么要有它（2026-10-24 与用户定案）：
    原来 19 个 `.cmd` 每个都自己写 python 的绝对路径、自己 set 环境变量、
    自己拼命令行。要做 exe 就得先把这些散落的入口收敛成**一个** —— 否则每个
    功能都要单独配一个启动器，打包和维护都会失控。

用法：
    python app\\main.py <子命令> [参数...]
    打包后：auto-checkin.exe <子命令> [参数...]

设计要点：
    · **开发态和打包态都能跑**。开发态直接起解释器执行目标脚本；打包态
      进程里已经没有解释器了，所以改成"重新拉起自己 + `--internal-run`"，
      由本文件在同一个进程里用 runpy 执行目标脚本。
    · 环境变量统一在这里设（TEMP/TMP/PYTHONPATH），启动器只要调本文件即可。
    · 每条子命令都要能在命令行里 --help 看到，别再靠记 .cmd 文件名。
"""
import os
import socket
import subprocess
import sys
import time

# ---- --home <目录>：数据目录在命令行里指定 ----
# 为什么需要：Windows 快捷方式（.lnk）**设不了环境变量**，而 .lnk 是不弹黑框的
# 唯一入口。有了这个参数，快捷方式可以直接写
#     auto-checkin.exe --home D:\某处 dashboard
# 必须在 `import paths` **之前**处理 —— paths 在 import 时就把 HOME_DIR 定死了。
if "--home" in sys.argv:
    try:
        _i = sys.argv.index("--home")
        _h = sys.argv[_i + 1] if _i + 1 < len(sys.argv) else ""
        if _h:
            os.environ["AUTOCHECKIN_HOME"] = os.path.abspath(_h)
        del sys.argv[_i:_i + 2]
    except Exception:
        pass

import paths  # noqa: E402

paths.setup_env()

# ⚠️ 必须用 paths.APP_DIR，不能用 dirname(__file__)：
# 打包后 main.py 的 __file__ 指向 `_internal\main.py`，dirname 就是 `_internal`，
# 而脚本实际在 `_internal\app\` 里 —— 直接用 __file__ 会找不到任何子脚本。
APP = paths.APP_DIR
ROOT = paths.ROOT
FROZEN = paths.FROZEN
INTERNAL = "--internal-run"

# ---- 无窗口构建下的 stdout 兜底 ----
# 用 --noconsole 打包后，进程没有控制台，`sys.stdout` / `sys.stderr` 是 **None**。
# 这时任何 print() 都会抛 AttributeError 直接把程序打死（pythonw 上踩过一模一样的坑）。
# 所以：有句柄就正常用；没有就接到日志文件，保证「看不到黑框」和「日志不丢」同时成立。
_NO_CONSOLE = False
try:
    if sys.stdout is None or sys.stderr is None:
        _NO_CONSOLE = True
        _logp = os.path.join(paths.LOGS, "cli.log")
        os.makedirs(paths.LOGS, exist_ok=True)
        # 超过 2 MB 就从头开始，免得日志无限长（这里是唯一还无界增长的日志）
        try:
            if os.path.isfile(_logp) and os.path.getsize(_logp) > 2 * 1024 * 1024:
                os.replace(_logp, _logp + ".old")
        except Exception:
            pass
        _f = open(_logp, "a", encoding="utf-8", buffering=1)
        try:
            import datetime as _dt
            _f.write("\n" + "=" * 60 + "\n")
            _f.write(_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                     + "  argv: " + " ".join(sys.argv[1:]) + "\n")
        except Exception:
            pass
        sys.stdout = _f
        sys.stderr = _f
except Exception:
    pass

# 输出被重定向到文件时，Python 默认按系统 ANSI 编码写，中文会变乱码；
# 接到终端时不要动它（Windows 控制台走 Unicode API，本来就是对的）。
try:
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PY = sys.executable
_EXE_DIR = os.path.dirname(PY)
PYW = os.path.join(_EXE_DIR, "pythonw.exe")
if not os.path.isfile(PYW):
    PYW = PY


# ----------------------------------------------------------------- 进程工具
def _env():
    e = dict(os.environ)
    e["PYTHONPATH"] = paths.LIBS
    e["PYTHONIOENCODING"] = "utf-8"
    for k in ("TEMP", "TMP", "TMPDIR"):
        e[k] = paths.TMP
    return e


def _script(name):
    return os.path.join(APP, name)


def run_script(name, args=None, hidden=False, detach=False, wait=False):
    """执行 `app/<name>` 这个脚本。

    hidden=True  → 开发态用 pythonw（没有黑框）；打包态用自己（无窗口构建）
    detach=True  → 脱离父进程独立存活（常驻进程用）
    wait=True    → 阻塞到它结束并返回退出码
    """
    args = list(args or [])
    path = _script(name)
    if not os.path.isfile(path):
        print(f"[!] 找不到脚本：{path}", file=sys.stderr)
        return 1

    if FROZEN:
        # 打包态：解释器已不存在，重新拉起自己，让它在本进程里 runpy 执行目标
        cmd = [sys.executable, INTERNAL, name] + args
    else:
        exe = PYW if hidden else PY
        cmd = [exe, path] + args

    flags = 0
    if os.name == "nt":
        if detach:
            flags = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        if hidden and not wait:
            flags |= 0x08000000               # CREATE_NO_WINDOW

    try:
        if wait:
            return subprocess.call(cmd, cwd=ROOT, env=_env())
        subprocess.Popen(cmd, cwd=ROOT, env=_env(),
                         creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL if (hidden or detach) else None,
                         stderr=subprocess.DEVNULL if (hidden or detach) else None)
        return 0
    except Exception as e:
        print(f"[!] 启动失败：{e}", file=sys.stderr)
        return 1


def internal_run(argv):
    """打包态下由 `--internal-run <脚本> [参数]` 进入，把目标脚本当 __main__ 跑。"""
    import runpy

    if not argv:
        print("[!] --internal-run 缺少脚本名", file=sys.stderr)
        return 2
    name, rest = argv[0], argv[1:]
    path = _script(name)
    if not os.path.isfile(path):
        print(f"[!] 找不到脚本：{path}", file=sys.stderr)
        return 1
    sys.argv = [path] + list(rest)
    runpy.run_path(path, run_name="__main__")
    return 0


# ----------------------------------------------------------------- 端口/窗口
def _port_up(port, timeout=25.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.4):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def _listener_pid(port):
    """用 netstat 查谁在监听这个端口（Get-NetTCPConnection 在某些环境会静默返回空）。"""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             timeout=10).stdout
    except Exception:
        return 0
    for line in out.splitlines():
        if (":%d" % port) in line and "LISTENING" in line.upper():
            toks = line.split()
            if toks and toks[-1].isdigit():
                return int(toks[-1])
    return 0


def _stop_stale_backend(port):
    """端口被自己的旧后端占着时把它停掉。只认 python / pythonw / auto-checkin。"""
    import dashboard as d

    pid = 0
    try:
        pid = int(open(os.path.join(paths.LOGS, "dashboard.pid")).read().strip())
    except Exception:
        pid = 0
    if not pid:
        pid = _listener_pid(port)
    if not pid or pid == os.getpid():
        return False
    img = os.path.basename((d.pid_image(pid) or "")).lower()
    if not any(k in img for k in ("python", "auto-checkin")):
        return False
    try:
        os.kill(pid, 15)
    except Exception:
        return False
    for _ in range(20):
        time.sleep(0.2)
        if not _port_up(port, timeout=0.2):
            return True
    return False


def _read_url(default):
    p = os.path.join(paths.LOGS, "dashboard-url.txt")
    try:
        with open(p, encoding="utf-8") as f:
            u = f.read().strip()
        return u or default
    except Exception:
        return default


def _bom_copy(src):
    """把脚本按 UTF-8 读出来、写成**正好一个** BOM 的副本。

    PowerShell 5.1 读无 BOM 的文件按 ANSI 解析，中文注释会变成语法错误；
    编辑工具又会把 BOM 剥掉，所以每次运行时重新写一份最省心。

    ⚠️ 2026-10-30 踩到：源文件自己带了 BOM 时，`open(encoding="utf-8")` 会把它
    读成 U+FEFF 留在文本里，再用 `utf-8-sig` 写出就变成 **两个 BOM**
    （EF BB BF EF BB BF）→ PowerShell 只吃掉一个，剩下的 U+FEFF 挡在 `param(`
    前面 → 整个 param 块报语法错误 → **窗口一声不响地打不开**。
    所以这里必须先把开头的 U+FEFF 全部剥掉。
    """
    dst = os.path.join(paths.TMP, "_console_win.bom.ps1")
    with open(src, encoding="utf-8") as f:
        text = f.read()
    text = text.lstrip("\ufeff")
    os.makedirs(paths.TMP, exist_ok=True)
    with open(dst, "w", encoding="utf-8-sig", newline="\n") as f:
        f.write(text)
    return dst


def _console_pid():
    """控制台窗口进程的 pid（console_win.ps1 启动时写的）。"""
    try:
        return int(open(os.path.join(paths.LOGS, "console-win.pid")).read().strip())
    except Exception:
        return 0


def _activate_console(pid):
    """把已经开着的那个控制台窗口切到前台（最小化也能还原）。"""
    code = (
        "Add-Type -Name W -Namespace N -MemberDefinition '"
        "[DllImport(\"user32.dll\")] public static extern bool ShowWindow(System.IntPtr h,int c);"
        "[DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(System.IntPtr h);';"
        "$p=Get-Process -Id " + str(pid) + " -ErrorAction SilentlyContinue;"
        "if($p -and $p.MainWindowHandle -ne 0){"
        "[N.W]::ShowWindow($p.MainWindowHandle,9)|Out-Null;"
        "[N.W]::SetForegroundWindow($p.MainWindowHandle)|Out-Null}"
    )
    try:
        subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-Command", code],
                         creationflags=0x08000000, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def open_console(url, tag="", backend_pid=0):
    """用 console_win.ps1 打开控制台窗口。

    ⚠️ 单实例：已经有一个窗口在跑就把那个切到前台，绝不再开第二个。
    2026-10-29 教训：用户连点了几次，托盘里堆了十几个控制台窗口——每个都是
    一个独立的 powershell 进程，谁也不管谁。
    """
    if not tag:
        old = _console_pid()
        if old:
            import dashboard as _dash

            if _dash.pid_alive(old):
                img = os.path.basename(_dash.pid_image(old) or "").lower()
                if "powershell" in img:
                    _activate_console(old)
                    print(f"已经有一个控制台窗口（PID {old}），已把它切到前台。",
                          file=sys.stderr)
                    return 0

    src = _script("console_win.ps1")
    if not os.path.isfile(src):
        print(f"[!] 找不到窗口脚本：{src}", file=sys.stderr)
        return 1
    try:
        bom = _bom_copy(src)
    except Exception as e:
        print(f"[!] 准备窗口脚本失败：{e}", file=sys.stderr)
        return 1
    cmd = ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
           "-File", bom, "-Root", ROOT, "-AppDir", APP, "-Url", url]
    if tag:
        cmd += ["-Tag", tag]
    if backend_pid:
        # 交给窗口：它退出时顺手停掉这个后端 pid（关窗口 = 真的关掉）
        cmd += ["-BackendPid", str(backend_pid)]
    try:
        # 窗口进程要独立存活，stdout/stderr 必须切断：
        # 否则它会继承调用方的管道，写满缓冲区后两边互相卡死（这个坑踩过一次）。
        # ⚠️ 还必须带 CREATE_NO_WINDOW：powershell.exe 是控制台程序，而父进程
        # （打包后的 exe）是 GUI 子系统、没有控制台 → 子进程会被分配一个新控制台，
        # 用户先看到一个黑框闪一下、再看到真正的窗口。2026-10-28 用户实测报的就是这个。
        flags = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW
        subprocess.Popen(cmd, cwd=ROOT, close_fds=True, creationflags=flags,
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return 0
    except Exception as e:
        print(f"[!] 打开窗口失败：{e}", file=sys.stderr)
        return 1


# ----------------------------------------------------------------- 子命令
def cmd_run(_args):
    """启动常驻签到（后台，无窗口）。"""
    import dashboard as d

    pid = d.read_json_text(os.path.join(paths.LOGS, "autowatch.pid"))
    if d.pid_alive(pid):
        print(f"已经在运行（PID {pid}）")
        return 0
    run_script("autowatch.py", ["--run"], hidden=True, detach=True)
    print("已启动常驻签到（后台运行，可用 status 查看）")
    return 0


def cmd_stop(_args):
    """停止常驻签到。"""
    import commands

    ok, err, data = commands.dispatch("autowatch.stop")
    if ok:
        print(data.get("message") or "已停止")
        return 0
    print(f"[!] {err.get('message')}")
    return 1


def cmd_status(_args):
    """看常驻签到是否在跑。"""
    return run_script("autowatch.py", ["--status"], wait=True)


def cmd_dashboard(args):
    """打开控制台（启动后端 + 开窗口）。"""
    port = 8791
    if args:
        try:
            port = int(args[0])
        except ValueError:
            print(f"[!] 端口不是数字：{args[0]}", file=sys.stderr)
            return 2

    # 后端可能已经在跑：它发现端口被占会立刻退出，我们只用等端口通
    run_script("dashboard.py", ["--port", str(port), "--no-open"], hidden=True, detach=True)

    if not _port_up(port):
        crash = os.path.join(paths.LOGS, "dashboard-crash.log")
        print(f"[!] 后端没起来（端口 {port} 没通）。", file=sys.stderr)
        if os.path.isfile(crash):
            print("-" * 60, file=sys.stderr)
            try:
                with open(crash, encoding="utf-8") as f:
                    print("".join(f.readlines()[-40:]), file=sys.stderr)
            except Exception:
                pass
        else:
            print("    没有崩溃日志，可以用 dashboard-foreground 看实时报错。", file=sys.stderr)
        return 1

    # ⚠️ 不能盲信 dashboard-url.txt：它可能残留着上一次（甚至是我测试时）
    # 写的别的端口。曾经因此把窗口指向一个没人监听的端口，用户看到
    # "127.0.0.1 拒绝连接"。所以必须核对**端口和 token 都在**。
    # 2026-10-28 又踩一次：文件缺失时 _read_url 会返回不带 token 的兜底地址，
    # 只查端口的话它会"通过检查"→ 页面显示「缺少访问令牌」。
    want = ":{0}/?token=".format(port)
    url = ""
    for _ in range(50):          # 最多等约 15 秒（冷启动要时间）
        url = _read_url("")
        if want in url:
            break
        time.sleep(0.3)
    if want not in url:
        # 端口通、却拿不到带 token 的地址：最可能是**我们自己的旧后端**还占着
        # 端口（比如 URL 文件被手工删了）。停掉它再起一次，别让用户卡在死循环里。
        print("[i] 端口上有旧的还是我们自己的后端，先停掉它再重开…", file=sys.stderr)
        if _stop_stale_backend(port):
            run_script("dashboard.py", ["--port", str(port), "--no-open"], hidden=True, detach=True)
            if _port_up(port):
                for _ in range(50):
                    url = _read_url("")
                    if want in url:
                        break
                    time.sleep(0.3)
    if want not in url:
        print(f"[!] 后端在 {port} 上起来了，但拿不到带 token 的地址"
              f"（现在读到：{url or '空'}）。", file=sys.stderr)
        print("    多半是别的程序占着这个端口。关掉它，或重启电脑后再试。",
              file=sys.stderr)
        return 1

    # 把后端 pid 传给窗口：窗口关闭时由它顺手停掉后端。
    # 这样"关窗口"才是真的关干净（否则后端 detached 存活、占端口占 exe 文件）。
    try:
        backend_pid = int(open(os.path.join(paths.LOGS, "dashboard.pid")).read().strip())
    except Exception:
        backend_pid = 0
    return open_console(url, backend_pid=backend_pid)


def cmd_dashboard_foreground(args):
    """前台跑后端（排查用：报错直接打在控制台上）。"""
    return run_script("dashboard.py", ["--port", str(args[0]) if args else "8791"],
                      wait=True)


def cmd_stop_dashboard(_args):
    """停掉控制台后端。"""
    import dashboard as d

    pid = d.read_json_text(os.path.join(paths.LOGS, "dashboard.pid"))
    if not d.pid_alive(pid):
        print("控制台没在运行")
        return 0
    if d.pid_is_ours(pid):
        try:
            os.kill(pid, 15)
            print(f"已停止控制台（PID {pid}）")
        except PermissionError:
            print(f"[!] 权限不足，停不掉 PID {pid}；试试用任务管理器结束 pythonw。")
            return 1
        except Exception as e:
            print(f"[!] 停止失败：{e}")
            return 1
    else:
        print(f"[!] PID {pid} 不是控制台进程（可能已被系统复用），为安全起见不杀。")
        return 1
    try:
        for f in ("dashboard.pid", "dashboard-url.txt"):
            os.remove(os.path.join(paths.LOGS, f))
    except Exception:
        pass
    return 0


def cmd_check(_args):
    """跑全部自检（改完代码用这个）。"""
    tests = [
        ("check-ui.py", "界面"),
        ("check-config.py", "配置写入"),
        ("check-commands.py", "命令总线"),
        ("check-http.py", "HTTP 接口"),
        ("selftest.py", "离线自检"),
    ]
    bad = []
    for name, label in tests:
        path = os.path.join(ROOT, name) if name != "selftest.py" else _script(name)
        if not os.path.isfile(path):
            print(f"  跳过 {label}：找不到 {os.path.basename(path)}")
            continue
        sys.stdout.write(f"  {label:8s} ... ")
        sys.stdout.flush()
        rc = subprocess.call([PY, path], cwd=ROOT, env=_env(),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("FAIL" if rc else "OK")
        if rc:
            bad.append(label)
    print()
    if bad:
        print("有失败：" + "、".join(bad) + "（单独跑那个脚本看详细输出）")
        return 1
    print("全部通过")
    return 0


# 名称 -> (处理函数, 说明)
COMMANDS = {
    "run": (cmd_run, "启动常驻签到（后台，无窗口）"),
    "stop": (cmd_stop, "停止常驻签到"),
    "status": (cmd_status, "查看常驻签到状态"),
    "dashboard": (cmd_dashboard, "打开控制台窗口（默认端口 8791）"),
    "dashboard-foreground": (cmd_dashboard_foreground, "前台跑控制台后端（排查用）"),
    "stop-dashboard": (cmd_stop_dashboard, "停止控制台后端"),
    "login": (lambda a: run_script("sjtu_login.py", a, wait=True), "登录 Canvas（走 jAccount）"),
    "discover": (lambda a: run_script("discover_courses.py", a, wait=True), "重新发现课程 ID"),
    "capture": (lambda a: run_script("capture_checkin.py", a, wait=True), "单次抓码并提交"),
    "capture-dryrun": (lambda a: run_script("capture_checkin.py",
                                           ["--course", "95168",
                                            "--course-url", "https://oc.sjtu.edu.cn/courses/95168",
                                            "--dry-run", "--wait", "45"] + a, wait=True),
                       "演练：抓码但不提交"),
    "get-token": (lambda a: run_script("get_token.py", a, wait=True), "取 mlearning 的 Access_Token"),
    "live-probe": (lambda a: run_script("live_probe.py",
                                        ["--course", "95168", "--seconds", "120"] + a, wait=True),
                   "探测直播页结构"),
    "schedule": (lambda a: run_script("scheduler.py", ["--show"] + a, wait=True), "打印本周监视线"),
    "scheduler": (lambda a: run_script("scheduler.py", ["--run", "--spawn"] + a, wait=True),
                  "按课表循环执行（前台）"),
    "selftest": (lambda a: run_script("selftest.py", a, wait=True), "离线自检"),
    "check": (cmd_check, "跑全部五个自检脚本"),
}


def usage():
    print("用法：main.py [--home <数据目录>] <子命令> [参数...]\n")
    print("子命令：")
    for name, (_fn, desc) in sorted(COMMANDS.items()):
        print(f"  {name:22s} {desc}")
    print()
    print("--home 用来指定数据目录（默认 %APPDATA%\\auto-checkin）；")
    print("   Windows 快捷方式设不了环境变量，所以快捷方式里用这个参数。")
    print()
    print("打包后等价于：auto-checkin.exe <子命令>\n")
    return 0


def main(argv):
    if not argv:
        return usage()
    name, rest = argv[0], argv[1:]
    if name in ("-h", "--help", "help"):
        return usage()
    item = COMMANDS.get(name)
    if item is None:
        print(f"[!] 没有这个子命令：{name}\n")
        usage()
        return 2
    return item[0](rest)


def _log_crash():
    """无窗口构建下崩溃时唯一能留下的线索。"""
    import traceback
    try:
        import datetime as _dt
        os.makedirs(paths.LOGS, exist_ok=True)
        with open(os.path.join(paths.LOGS, "main-crash.log"), "a", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            f.write(_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    + "  argv=" + " ".join(sys.argv[1:]) + "\n")
            f.write(traceback.format_exc() + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        if INTERNAL in sys.argv:
            i = sys.argv.index(INTERNAL)
            sys.exit(internal_run(sys.argv[i + 1:]))
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except BaseException:
        _log_crash()
        raise
