# -*- coding: utf-8 -*-
"""把 v.sjtu.edu.cn 的静态资源（播放器 JS、配置）抓下来分析。
这些是公开静态文件，不需要登录。目的是搞清楚「直播」用的是什么协议。"""
import os
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import paths                      # 项目唯一的路径真源（app/paths.py）
HERE = paths.ROOT                 # 兼容旧代码：HERE 一律指项目根
OUT = os.path.join(HERE, "vendor")
os.makedirs(OUT, exist_ok=True)

BASE = "https://v.sjtu.edu.cn/jy-application-resourcemanage-ui/"
FILES = [
    "static/scripts/kmedia/kmedia-uni.js",
    "static/config/global-production.json",
    "static/js/index.29e19ca7.js",
]

HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"),
    "Referer": "https://v.sjtu.edu.cn/jy-application-resourcemanage-ui/",
    "Accept": "*/*",
}

for f in FILES:
    url = BASE + f
    dest = os.path.join(OUT, os.path.basename(f))
    try:
        req = urllib.request.Request(url, headers=HDRS)
        with urllib.request.urlopen(req, timeout=45) as r:
            data = r.read()
        with open(dest, "wb") as fh:
            fh.write(data)
        print(f"[+] {len(data):>9} bytes  {f}")
    except Exception as e:
        print(f"[!] {f} 失败: {type(e).__name__}: {str(e)[:120]}")
print("done ->", OUT)
