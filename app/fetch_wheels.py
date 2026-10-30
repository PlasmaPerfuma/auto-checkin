"""Download the wheels we need straight from PyPI (pip's unpack step is blocked by the sandbox)."""
import json
import os
import sys
import urllib.request
import zipfile

import paths                      # 项目唯一的路径真源（app/paths.py）
HERE = paths.ROOT                 # 兼容旧代码：HERE 一律指项目根
LIBS = os.path.join(HERE, "libs")
WHEELS = os.path.join(HERE, "wheels")

TARGETS = [
    # (pypi project, tag fragments that must all appear in the filename)
    ("zxing-cpp", ["cp312", "abi3", "win_amd64"]),
    ("mss", ["py3", "any"]),
    ("playwright", ["py3", "none", "win_amd64"]),
    ("greenlet", ["cp312", "win_amd64"]),
    ("pyee", ["py3", "none", "any"]),
    ("typing-extensions", ["py3", "none", "any"]),
]


def pick(files, fragments):
    for f in files:
        name = f["filename"]
        if all(frag in name for frag in fragments):
            return f
    return None


def main():
    os.makedirs(LIBS, exist_ok=True)
    os.makedirs(WHEELS, exist_ok=True)
    for project, fragments in TARGETS:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{project}/json", timeout=30) as r:
            data = json.load(r)
        f = pick(data["urls"], fragments)
        if f is None:
            print(f"[!] no wheel matching {fragments} for {project}")
            continue
        dest = os.path.join(WHEELS, f["filename"])
        if not os.path.exists(dest):
            print(f"[+] downloading {f['filename']}")
            urllib.request.urlretrieve(f["url"], dest)
        else:
            print(f"[=] cached {f['filename']}")
        print(f"    unpacking -> libs/")
        with zipfile.ZipFile(dest) as z:
            z.extractall(LIBS)
    print("done")


if __name__ == "__main__":
    # 这个脚本没有 argparse，任何参数（包括 --help）都会被忽略并直接开跑，
    # 会重新下载 38MB 轮子。加个显式守卫，避免误触。
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print("用法: python app/fetch_wheels.py")
        print("作用: 从 PyPI 重新下载 zxing-cpp / mss / playwright 等轮子并解压到 libs/。")
        print("注意: 会重建 wheels/ 缓存目录；平时不需要跑。")
        sys.exit(0)
    main()
