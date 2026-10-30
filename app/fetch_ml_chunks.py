# -*- coding: utf-8 -*-
"""从 mlearning SPA 的 webpack 清单里找出签到页所在分片，下载并搜参数名。"""
import json
import os
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import paths                      # 项目唯一的路径真源（app/paths.py）
HERE = paths.ROOT                 # 兼容旧代码：HERE 一律指项目根
OUT = os.path.join(HERE, "vendor")
BASE = "https://mlearning.sjtu.edu.cn/lms/mobile/"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49")

src = open(os.path.join(OUT, "ml-index.js"), encoding="utf-8").read()


def take_obj(s, start):
    """s[start] 必须是 '{'，返回 (对象文本, 结束位置)。"""
    depth, in_str, esc, i = 0, False, False, start
    while i < len(s):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return s[start:i + 1], i
        i += 1
    return None, i


k = src.find("static/js/")
if k < 0:
    print("[!] 没找到 static/js/")
    sys.exit(1)
p1 = src.find("{", k)
names_txt, end1 = take_obj(src, p1)
hash_start = src.find("{", end1)
hash_txt, _ = take_obj(src, hash_start)
print(f"[i] 名字表 {len(names_txt)} 字节，哈希表 {len(hash_txt)} 字节")

names = dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', names_txt))
hashes = dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', hash_txt))
print(f"[i] {len(names)} 个分片名 / {len(hashes)} 个哈希")

targets = [cid for cid, nm in names.items() if "scan" in nm.lower()]
print(f"[i] 跟 scan 相关的分片: {[(c, names[c]) for c in targets]}")

os.makedirs(os.path.join(OUT, "chunks"), exist_ok=True)
for cid in targets:
    nm = names[cid]
    h = hashes.get(cid)
    if not h:
        print(f"  [!] {nm} 没有哈希")
        continue
    url = f"{BASE}static/js/{nm}.{h}.js"
    try:
        d = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}),
                                   timeout=40).read()
    except Exception as e:
        print(f"  [!] {nm} 下载失败 {type(e).__name__}")
        continue
    txt = d.decode("utf-8", "replace")
    dest = os.path.join(OUT, "chunks", f"{nm}.js")
    open(dest, "w", encoding="utf-8").write(txt)
    print(f"  [+] {nm}  {len(txt)//1024}KB")
    for kw in ("latitude", "longitude", "signScanGis", "gis", "accuracy"):
        n = txt.count(kw)
        if n:
            print(f"       {kw} × {n}")
            for m in list(re.finditer(kw, txt))[:2]:
                s = max(0, m.start() - 260)
                print("       … " + txt[s:m.start() + 240].replace("\n", " ")[:480])
