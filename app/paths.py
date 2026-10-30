# -*- coding: utf-8 -*-
r"""
全项目【唯一】的路径真源。

以前每个脚本自己 `HERE = dirname(__file__)` 再拼子路径，一动文件就全错。
现在所有脚本只 `import paths`，从这里取路径；将来打包 exe 时也只需要动这一个文件。

目录约定（迁移后）：
    auto-checkin/
    ├── run-*.cmd          双击入口
    ├── app/               所有 .py（含本文件）
    ├── data/  配置与数据：periods/schedule/courses/credentials/token/extension
    ├── logs/  所有 .log
    ├── output/ 产物：out/（侦察快照）、found/（命中帧）
    ├── probe/ 一次性探测用的 .cmd
    ├── libs/  zxing-cpp + mss + playwright（走 PYTHONPATH）
    ├── vendor/ 抓到本地的公开前端资源
    ├── chrome-profile/  Playwright 的独立浏览器配置（登录态在里面）
    └── tmp/   Playwright 的 TEMP（本机 AppData\Local\Temp 不可用）

打包 exe 时：设环境变量 AUTOCHECKIN_HOME 指向 %APPDATA%\auto-checkin，
数据/配置/profile 就都落到可写目录，不用改任何代码。
"""
import os
import sys
import tempfile

# ---------------------------------------------------------------- 打包/源码
# 打包后（PyInstaller）环境完全不一样，必须先分清三件事：
#   · 程序文件在哪 —— 只读，可能在 sys._MEIPASS 这个临时解包目录里
#   · 用户数据在哪 —— 必须可写，放 %APPDATA%
#   · 用户看到程序在哪 —— exe 所在目录
# 只有真的打包过才知道 __file__ 会指到临时目录，所以要在这里一次算清。
FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    # onedir 模式下资源在 exe 同级的 _internal；onefile 模式在 _MEIPASS 临时目录。
    RES_DIR = getattr(sys, "_MEIPASS", EXE_DIR)
    APP_DIR = os.path.join(RES_DIR, "app")
    ROOT = EXE_DIR
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))      # ...\auto-checkin\app
    RES_DIR = os.path.dirname(APP_DIR)                        # ...\auto-checkin
    ROOT = RES_DIR

# 「用户看到的位置」：源码模式下就是项目根，打包后是 exe 所在目录
EXE_HOME = ROOT


def _default_home():
    """打包后数据默认放 %APPDATA%\\auto-checkin；源码模式仍放项目里。"""
    if not FROZEN:
        return EXE_HOME
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "auto-checkin")


# 可写根目录：环境变量优先，其次按上面规则
HOME_DIR = os.environ.get("AUTOCHECKIN_HOME") or _default_home()

DATA = os.path.join(HOME_DIR, "data")
LOGS = os.path.join(HOME_DIR, "logs")
OUTPUT = os.path.join(HOME_DIR, "output")

PROFILE = os.path.join(HOME_DIR, "chrome-profile")
TMP = os.path.join(HOME_DIR, "tmp")

VENDOR = os.path.join(RES_DIR, "vendor")
LIBS = os.path.join(RES_DIR, "libs")
# 首次运行用的数据模板（随程序一起打包，只读）
TEMPLATES = os.path.join(APP_DIR, "templates")

# —— 具体文件 ——
PERIODS = os.path.join(DATA, "periods.json")
SCHEDULE = os.path.join(DATA, "schedule.json")
COURSES = os.path.join(DATA, "courses.json")
CREDENTIALS = os.path.join(DATA, "credentials.json")
CREDENTIALS_EXAMPLE = os.path.join(DATA, "credentials.example.json")
TOKEN = os.path.join(DATA, "mlearning-token.txt")
EXTENSION = os.path.join(DATA, "extension.txt")

OUTPUT_OUT = os.path.join(OUTPUT, "out")
OUTPUT_FOUND = os.path.join(OUTPUT, "found")

ALL_DIRS = [DATA, LOGS, OUTPUT, OUTPUT_OUT, OUTPUT_FOUND, TMP, PROFILE]


def ensure_dirs():
    for d in ALL_DIRS:
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass


# 首次运行引导：把缺失的配置文件从模板补上
#   · **只补缺失的文件，绝不覆盖已有文件**（这是硬规矩：用户改过的不能被模板盖回去）
#   · credentials.json 故意不自动创建（里面是占位符「你的学号」，直接拿去登录只会浪费一次验证码），
#     而是让用户照着 credentials.example.json 自己填
_bootstrapped = False


def bootstrap():
    """首次运行时补齐缺失的配置。返回本次新建的文件名列表。"""
    global _bootstrapped
    if _bootstrapped:
        return []
    _bootstrapped = True

    created = []
    for name in ("periods.json", "schedule.json", "credentials.example.json"):
        dst = os.path.join(DATA, name)
        if os.path.exists(dst):
            continue
        src = os.path.join(TEMPLATES, name)
        if not os.path.isfile(src):
            continue
        try:
            with open(src, "rb") as f:
                data = f.read()
            with open(dst, "wb") as f:
                f.write(data)
            created.append(name)
        except Exception:
            pass

    if not created:
        return created

    # 只记一次，方便排查"为什么首次启动这么慢/为什么多了文件"
    try:
        import datetime as _dt
        os.makedirs(LOGS, exist_ok=True)
        with open(os.path.join(LOGS, "first-run.log"), "a", encoding="utf-8") as f:
            f.write(_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    + f"  首次运行，已从模板生成: {', '.join(created)}\n")
            f.write(f"          数据目录: {DATA}\n")
            f.write("          下一步：把 data\\credentials.example.json 复制成 "
                    "data\\credentials.json，并填入你的 jAccount 账号密码\n")
            f.write("          然后加课表：编辑 data\\schedule.json，或用控制台的「全部课程」页\n")
    except Exception:
        pass
    return created


def setup_env():
    """必须在 import playwright / zxingcpp 之前调用。"""
    ensure_dirs()
    for k in ("TEMP", "TMP", "TMPDIR"):
        os.environ[k] = TMP
    tempfile.tempdir = TMP
    # 打包后脚本都躺在 <RES_DIR>\app 里，而 `import commands` 这种同目录导入
    # 靠的是 sys.path 里有那一层 —— 源码模式天然满足（入口就在 app\ 下），
    # 打包模式必须显式补上，否则 dashboard / commands 互相 import 全会炸。
    if os.path.isdir(APP_DIR) and APP_DIR not in sys.path:
        sys.path.insert(0, APP_DIR)
    # 源码模式靠 libs/ 目录拼出依赖；打包后依赖已经在包里，不用也不能再插
    if not FROZEN and os.path.isdir(LIBS) and LIBS not in sys.path:
        sys.path.insert(0, LIBS)

    # 首次运行补齐缺失的配置。放在最后且整体吞异常：
    # 引导失败也绝不能挡住主流程（否则连 --check-env 都跑不出来）。
    try:
        bootstrap()
    except Exception:
        pass


def check(verbose=True):
    """自检：把每个预期路径解析出来，报告存在与否。返回问题列表。"""
    problems = []
    rows = [
        ("程序根 ROOT", ROOT, True),
        ("资源目录 RES_DIR", RES_DIR, True),
        ("代码目录 APP_DIR", APP_DIR, True),
        ("可写根 HOME_DIR", HOME_DIR, True),
        ("数据目录 DATA", DATA, True),
        ("日志目录 LOGS", LOGS, True),
        ("产物目录 OUTPUT", OUTPUT, True),
        ("浏览器 profile", PROFILE, True),
        ("临时目录 TMP", TMP, True),
        # 打包后由 PyInstaller 打包，不再有 libs/ 目录，所以不设成必需
        ("依赖目录 LIBS", LIBS, not FROZEN),
        ("课时表 periods.json", PERIODS, True),
        ("课表 schedule.json", SCHEDULE, True),
        ("课程 ID courses.json", COURSES, False),
        ("凭据 credentials.json", CREDENTIALS, False),
        ("mlearning token", TOKEN, False),
    ]
    if verbose:
        print(f"运行模式：{'打包(exe)' if FROZEN else '源码'}")
        print(f"{'项目':<24}{'状态':<8}路径")
        print("-" * 78)
    for label, p, required in rows:
        ok = os.path.exists(p)
        state = "OK" if ok else ("缺失(必需)" if required else "缺失(可选)")
        if required and not ok:
            problems.append(f"{label} 缺失: {p}")
        if verbose:
            print(f"{label:<24}{state:<8}{p}")
    return problems


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    setup_env()
    print("=" * 78)
    probs = check()
    print("=" * 78)
    if probs:
        print(f"[!] {len(probs)} 个必需路径缺失：")
        for x in probs:
            print("   -", x)
        sys.exit(2)
    print("路径自检通过 [OK]")
