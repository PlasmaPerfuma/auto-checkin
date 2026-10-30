# -*- coding: utf-8 -*-
"""
控制台的「真·无边框窗口」版本。

用 pywebview（底层是系统的 WebView2，跟 Edge 同内核）承载页面：
  · frameless=True  → 没有系统标题栏
  · easy_drag=True  → 按住窗口任意非交互区域就能拖

同一个进程里：后台线程跑 HTTP 服务，主线程开窗口。
如果 pywebview 起不来，自动退回 dashboard.py（Edge --app 模式，有细标题栏）。

用法：双击 run-dashboard.cmd
"""
import os
import sys
import threading
import time

import paths

paths.setup_env()

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import dashboard as db      # 复用服务端（H / Srv / TOKEN / 控制接口）

# pythonnet 默认会去找 .NET Framework（netfx）的 Python.Runtime.dll，在本机加载失败；
# 实测 coreclr(-.NET 8/9) 可用，必须在 import webview 之前设好。
os.environ.setdefault("PYTHONNET_RUNTIME", "coreclr")

PORT = 8791
WIDTH = 1180
HEIGHT = 820


def start_server():
    """在后台线程起服务；端口被占就直接用已存在的那个实例。"""
    try:
        srv = db.Srv(("127.0.0.1", PORT), db.H)
    except OSError:
        uf = os.path.join(paths.LOGS, "dashboard-url.txt")
        try:
            return None, open(uf, encoding="utf-8").read().strip()
        except Exception:
            return None, f"http://127.0.0.1:{PORT}/"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{PORT}/?token={db.TOKEN}"
    for name, val in (("dashboard-url.txt", url), ("dashboard.pid", str(os.getpid()))):
        try:
            with open(os.path.join(paths.LOGS, name), "w", encoding="utf-8") as f:
                f.write(val)
        except Exception:
            pass
    return srv, url


def main():
    srv, url = start_server()
    print("=" * 60, flush=True)
    print("  自动签到控制台", flush=True)
    print(f"  {url}", flush=True)
    print("=" * 60, flush=True)

    try:
        import webview
    except Exception as e:
        print(f"[!] pywebview 不可用（{type(e).__name__}: {e}），"
              f"退回 Edge --app 模式", flush=True)
        try:
            db.open_app_window(url, WIDTH, HEIGHT)
        except Exception:
            import webbrowser
            webbrowser.open(url)
        if srv:
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                pass
        return 0

    try:
        webview.create_window(
            "自动签到控制台",
            url,
            width=WIDTH,
            height=HEIGHT,
            frameless=True,       # 去掉系统标题栏
            easy_drag=True,       # 任意空白处可拖动
            resizable=True,
            background_color="#ffffff",
            text_select=False,
        )
        webview.start()           # 阻塞到窗口关闭
    except Exception as e:
        print(f"[!] 窗口启动失败 {type(e).__name__}: {e}", flush=True)
        print("    改用 Edge --app 模式", flush=True)
        db.open_app_window(url, WIDTH, HEIGHT)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    finally:
        try:
            os.remove(os.path.join(paths.LOGS, "dashboard.pid"))
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
