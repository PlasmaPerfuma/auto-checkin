# -*- coding: utf-8 -*-
"""
jAccount 登录自动化（可单独跑，也可被别的脚本 import）。

原理（都是从你那个 Tampermonkey 脚本里确认过的）：
  Canvas 登录页会自动跳转到 jaccount.sjtu.edu.cn/jaccount/jalogin?sid=...&client=...
  登录页上有  #captcha-img（验证码图） / #input-login-captcha（验证码输入框） / #login-form
  验证码交给公开服务识别：POST https://geek.sjtu.edu.cn/captcha-solver/  (multipart 字段名 image)

凭据从 credentials.json 读，**由你自己填**，脚本不打印、不外传。
用法：python app\\main.py login
"""
import argparse
import base64
import io
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import uuid

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import paths                      # 项目唯一的路径真源（app/paths.py）
HERE = paths.ROOT                 # 兼容旧代码：HERE 一律指项目根
PROFILE = paths.PROFILE          # 用 paths 的真源：打包后 ROOT 是 exe 目录，profile 必须在可写目录
CRED = paths.CREDENTIALS

_TMP = paths.TMP                 # 同上：临时目录也必须落在可写目录
os.makedirs(_TMP, exist_ok=True)
for _k in ("TEMP", "TMP", "TMPDIR"):
    os.environ[_k] = _TMP
tempfile.tempdir = _TMP

import urllib.request                        # noqa: E402
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

SOLVER = "https://geek.sjtu.edu.cn/captcha-solver/"
CANVAS = "https://oc.sjtu.edu.cn/"

LOGS = []
_LOG_FILE = os.path.join(paths.LOGS, "login.log")


def log(msg):
    """每行都**实时写盘**。之前只在程序结束时一次性写，结果跑一半关掉窗口
    就什么日志都不剩，看起来像"没写日志"。"""
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    LOGS.append(line)
    print(line, flush=True)
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def solve_captcha(img_bytes):
    """把验证码图 POST 给公开识别服务，返回识别出的文本。"""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=92)
        payload = buf.getvalue()
    except Exception:
        payload = img_bytes

    bnd = "----WebKitFormBoundary" + uuid.uuid4().hex[:16]
    parts = [f"--{bnd}\r\n".encode(),
             b'Content-Disposition: form-data; name="image"; filename="captcha.jpg"\r\n',
             b"Content-Type: image/jpeg\r\n\r\n",
             payload, b"\r\n",
             f"--{bnd}--\r\n".encode()]
    body = b"".join(parts)
    # 这个服务会挑 User-Agent：用 Python 默认 UA 会被回 415 UNSUPPORTED MEDIA TYPE，
    # 换成浏览器 UA 就正常（已实测：字段名必须 image、且必须带 filename）。
    req = urllib.request.Request(
        SOLVER, data=body, method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={bnd}",
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"),
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": "https://jaccount.sjtu.edu.cn",
            "Referer": "https://jaccount.sjtu.edu.cn/",
        })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body_txt = ""
        try:
            body_txt = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        log(f"    [!] 识别服务返回 HTTP {e.code}，响应体: {body_txt[:200]!r}")
        return ""
    try:
        data = json.loads(txt)
    except Exception:
        log(f"    [!] 识别服务返回的不是 JSON: {txt[:200]!r}")
        return ""
    if not data.get("result"):
        log(f"    [!] 识别服务说: {data.get('error')!r}，原始返回: {txt[:160]}")
    return data.get("result") or ""


def load_creds():
    if not os.path.isfile(CRED):
        log(f"[!] 缺少 {CRED}")
        log("    请把 credentials.example.json 复制成 credentials.json，填上账号密码。")
        return None
    try:
        d = json.load(open(CRED, encoding="utf-8"))
    except Exception as e:
        log(f"[!] credentials.json 读不了: {e}")
        return None
    u, p = (d.get("username") or "").strip(), (d.get("password") or "").strip()
    if not u or not p:
        log("[!] credentials.json 里的 username / password 还是空的。")
        return None
    return u, p


def is_logged_in(page):
    try:
        url = page.url
    except Exception:
        return False
    return "oc.sjtu.edu.cn" in url and "login" not in url


def click_jaccount(page):
    """Canvas 的 /login/canvas 页面默认显示的是「非 jAccount」账号密码表单；
    要走统一身份认证，必须先点那个 jAccount 按钮/链接。这里自动找到并点它。"""
    if "jaccount.sjtu.edu.cn" in (page.url or ""):
        return True

    # 用 JS 扫一遍，把最像「jAccount 登录」的元素打个临时 id
    found = page.evaluate("""() => {
      const bag = [...document.querySelectorAll('a,button,img,[onclick],[role=button]')];
      const score = (el) => {
        const par = el.closest ? el.closest('a,button,[role=button]') : null;
        const href = ((el.getAttribute && el.getAttribute('href')) ||
                      (par && par.getAttribute && par.getAttribute('href')) || '').toLowerCase();
        const src = ((el.getAttribute && el.getAttribute('src')) || '').toLowerCase();
        const text = ((el.innerText || (el.getAttribute && el.getAttribute('alt')) ||
                       (el.getAttribute && el.getAttribute('title')) || '') + '').toLowerCase();
        // 排除"创建账号 / 注册"这类陷阱链接
        if (text.includes('创建') || text.includes('注册') ||
            text.includes('register') || text.includes('sign up')) return 0;
        // 按可靠性排序；/login/openid_connect 是 Canvas 的统一认证入口，最可信
        if (href.includes('openid_connect')) return 100;
        if (href.includes('jaccount') || href.includes('/login/sso')) return 90;
        if (src.includes('logojaccount') || src.includes('jaccount')) return 80;
        if (text.includes('jaccount') || text.includes('统一身份')) return 60;
        if (((el.className || '') + '').toLowerCase().includes('jaccount')) return 50;
        return 0;
      };
      let best = null, bestScore = 0;
      for (const el of bag) {
        const r = el.getBoundingClientRect();
        if (r.width < 8 || r.height < 8) continue;
        const sc = score(el);
        if (sc > bestScore) { bestScore = sc; best = el; }
      }
      if (!best) {
        return {ok: false, candidates: bag.filter(e => {
          const r = e.getBoundingClientRect();
          return r.width > 8 && r.height > 8;
        }).slice(0, 25).map(e => ((e.innerText || e.getAttribute('alt') ||
              e.getAttribute('title') || e.getAttribute('href') || '')
              + '').trim().replace(/\\s+/g, ' ').slice(0, 50))};
      }
      best.setAttribute('data-dsh-jaccount', '1');
      return {ok: true,
              text: ((best.innerText || best.getAttribute('alt') ||
                      best.getAttribute('href') || '') + '').trim().slice(0, 80),
              tag: best.tagName.toLowerCase(),
              cls: ((best.className || '') + '').slice(0, 60)};
    }""")

    if found and found.get("ok"):
        log(f"  找到 jAccount 入口: {found.get('text')!r}"
            f"  <{found.get('tag','?')} class={found.get('cls','')[:40]!r}>，点击它")
        try:
            page.click("[data-dsh-jaccount='1']", timeout=15000)
            return True
        except Exception as e:
            log(f"  [!] 点击失败: {type(e).__name__}: {str(e)[:120]}")
            return False

    log("  [!] 没找到 jAccount 入口。页面上可点的东西有：")
    for t in (found or {}).get("candidates", [])[:20]:
        log(f"        - {t!r}")
    try:
        page.screenshot(path=os.path.join(paths.OUTPUT, "login-page.png"))
        log(f"  已截图: login-page.png（发我就能对上选择器）")
    except Exception:
        pass
    return False


def grab_captcha(page, timeout=10.0):
    """把当前页面上那张验证码图取成字节。
    jAccount 的 <img id="captcha-img" src=""> 初始是空的，必须 refreshCaptcha() 之后
    等它真的加载完，再用 canvas 取「屏幕上显示的那张」——不要重新 fetch 同一 URL，
    否则可能拿到换过的新码。取不到返回 None。"""
    page.evaluate("""() => {
      const img = document.querySelector('#captcha-img');
      if (!img) return;
      if ((!img.src || img.naturalWidth === 0) && typeof refreshCaptcha === 'function') {
        refreshCaptcha();
      }
    }""")
    deadline = time.time() + timeout
    ready = False
    while time.time() < deadline:
        try:
            ready = page.evaluate("""() => {
              const img = document.querySelector('#captcha-img');
              return !!(img && img.complete && img.naturalWidth > 0 &&
                        img.src && img.src.indexOf('http') === 0);
            }""")
        except Exception:
            ready = False
        if ready:
            break
        time.sleep(0.25)
    if not ready:
        return None
    try:
        data_url = page.evaluate("""() => {
          const img = document.querySelector('#captcha-img');
          const c = document.createElement('canvas');
          c.width = img.naturalWidth; c.height = img.naturalHeight;
          c.getContext('2d').drawImage(img, 0, 0);
          return c.toDataURL('image/jpeg', 0.95);
        }""")
        return base64.b64decode(data_url.split(",", 1)[1])
    except Exception:
        return None


def do_login(page, ctx, username, password, attempts=5):
    for n in range(1, attempts + 1):
        log(f"—— 第 {n} 次尝试 ——")
        try:
            page.goto(CANVAS, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"  [!] 打开 Canvas: {type(e).__name__}")
            continue
        time.sleep(2)

        if is_logged_in(page):
            log("  √ 已经是登录状态，不用再登。")
            return True

        # 关键：/login/canvas 默认是「非 jAccount」表单，得先进统一认证。
        # Canvas 侧的统一认证入口是固定路径 /login/openid_connect，直接访问它，
        # 比在 DOM 里猜按钮稳得多（我曾经猜成"创建jAccount账号"那个链接，点错了）。
        if "jaccount.sjtu.edu.cn" not in page.url:
            moved = False
            try:
                page.goto(CANVAS + "/login/openid_connect",
                          wait_until="domcontentloaded", timeout=60000)
                time.sleep(1.5)
                moved = "jaccount" in page.url
                log(f"  直接访问 /login/openid_connect → {'到了 jaccount' if moved else page.url[:90]}")
            except Exception as e:
                log(f"  [!] 直达入口失败: {type(e).__name__}: {str(e)[:100]}")
            if not moved and "jaccount" not in page.url:
                click_jaccount(page)

        # 等跳转到 jaccount
        for _ in range(30):
            if "jaccount.sjtu.edu.cn" in page.url:
                break
            time.sleep(0.5)
        if "jaccount" not in page.url:
            log(f"  [?] 没进到 jaccount，当前: {page.url[:110]}")
            if is_logged_in(page):
                return True

        # 找输入框
        try:
            # 2026-10-29 实测踩到的坑：刚跳到 jaccount 时页面往往还没渲染完，
            # 直接 query_selector 必然拿到 None → 之前这里直接 return False，
            # 一次 11 秒就整节放弃；其实再等十几秒就能登进去。
            try:
                page.wait_for_selector("#input-login-user", timeout=15000)
            except Exception:
                pass
            cap_img = page.query_selector("#captcha-img")
            cap_input = page.query_selector("#input-login-captcha")
            pwd = page.query_selector("input[type=password]")
            # 用户名：jAccount 页面用的是 #input-login-user
            user = None
            for sel in ("#input-login-user", "#input-login", "#input-username",
                        "input[type=text]", "input[name*=user]", "input[name*=account]"):
                el = page.query_selector(sel)
                if el and el.is_visible():
                    user = el
                    break
            if not (pwd and user):
                log("  [!] 找不到账号/密码输入框（页面可能还没渲染好），换下一次尝试。")
                page.screenshot(path=os.path.join(paths.OUTPUT, "login-fail.png"))
                continue

            user.fill(username)
            pwd.fill(password)

            if cap_img and cap_input:
                # 验证码 **4 位和 5 位都出现过**（用户实测确认），所以绝不能按长度硬卡——
                # 那会把合法的 5 位码直接判成错误、把能成的登录卡死。
                # 只挡掉明显异常：空、含非字母数字、长度离谱。
                code = ""
                for cap_try in range(1, 4):
                    raw = grab_captcha(page)
                    if raw is None:
                        log("  [!] 验证码图片没加载出来（refreshCaptcha 后仍为空）")
                        try:
                            page.screenshot(path=os.path.join(paths.OUTPUT, "login-page.png"))
                        except Exception:
                            pass
                        break
                    code = solve_captcha(raw)
                    log(f"  验证码图 {len(raw)} 字节 → 识别结果 {code!r}（{len(code)} 位）")
                    if code and re.fullmatch(r"[A-Za-z0-9]{3,6}", code):
                        break
                    log(f"    识别结果格式可疑（第 {cap_try} 张），换一张")
                    code = ""
                    page.evaluate("() => { if (typeof refreshCaptcha === 'function') refreshCaptcha(); }")
                    time.sleep(0.5)
                if not code:
                    log("  [!] 几张都没识别出可用的码，本次尝试放弃")
                    continue
                cap_input.fill(str(code))
            else:
                log("  [?] 这个页面没有验证码，直接提交")

            # 提交
            btn = None
            for sel in ("#submit-password-button", "#login-form button[type=submit]",
                        "input[type=submit].form-submit", "button[type=submit]",
                        "#submit-btn", "button:has-text('登录')"):
                try:
                    b = page.query_selector(sel)
                    if b and b.is_visible():
                        btn = b
                        break
                except Exception:
                    continue
            if btn:
                btn.click()
            else:
                pwd.press("Enter")
            log("  已提交，等跳回 Canvas…（最多 15 秒）")

            ok = False
            for i in range(30):
                time.sleep(0.5)
                if is_logged_in(page):
                    ok = True
                    break
                if "jaccount" not in (page.url or ""):
                    break
                if i and i % 6 == 0:
                    log(f"    …已等 {i * 0.5:.0f}s，当前页仍在 {page.url[:70]}")
            if ok:
                log(f"  √ 登录成功: {page.url[:110]}")
                return True
            log(f"  [!] 这次没成，当前页: {page.url[:110]}")
        except Exception as e:
            log(f"  [!] 异常 {type(e).__name__}: {str(e)[:150]}")
            try:
                page.screenshot(path=os.path.join(paths.OUTPUT, "login-fail.png"))
            except Exception:
                pass
            time.sleep(1)

    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true", help="不显示浏览器窗口")
    ap.add_argument("--profile", default=PROFILE)
    args = ap.parse_args()

    creds = load_creds()
    if not creds:
        return 1
    # 每次运行开一份新日志（之后的每行都会实时写盘）
    try:
        open(_LOG_FILE, "w", encoding="utf-8").close()
    except Exception:
        pass
    username, password = creds
    log(f"账号已读入（{username[:3]}***，密码不显示）")

    ok = False
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            args.profile, channel="chrome", headless=args.headless, no_viewport=True,
            ignore_default_args=IGNORE_DEFAULT_ARGS, args=ARGS,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ok = do_login(page, ctx, username, password)
        if ok:
            try:
                title = page.title()
                log(f"  落地页标题: {title}")
            except Exception:
                pass
        try:
            ctx.close()
        except Exception:
            pass

    out = _LOG_FILE    # 日志已在 log() 里逐行实时写入，这里不再整体重写

    # 明确收尾，避免看起来像"卡住不退出"
    print()
    print("=" * 66)
    if ok:
        print("结果：登录成功 ✅  （profile 里的 Canvas 会话已刷新，可直接跑 run-discover / run-capture）")
    else:
        print("结果：登录失败 ❌  共试了几轮都不成，日志里有每一步。把 login.log 发给 AI。")
        print("      常见原因：验证码识别不准（jAccount 是 4 位）、密码错、或账号被风控。")
    print(f"日志: {out}")
    print("=" * 66)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
