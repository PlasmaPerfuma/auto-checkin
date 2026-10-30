# -*- coding: utf-8 -*-
"""挖 mlearning.sjtu.edu.cn 的移动端 SPA，找签到接口与定位字段。"""
import os
import re
import sys
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49")
HDRS = {"User-Agent": UA}

import paths                      # 项目唯一的路径真源（app/paths.py）
HERE = paths.ROOT                 # 兼容旧代码：HERE 一律指项目根
OUT = os.path.join(HERE, "vendor")
os.makedirs(OUT, exist_ok=True)


def get(url, limit=6_000_000):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=HDRS), timeout=30)
    return r.read(limit), r.url


base = "https://mlearning.sjtu.edu.cn/lms/mobile/"
try:
    html, final = get(base)
except Exception as e:
    print(f"[!] {base} -> {type(e).__name__}: {str(e)[:150]}")
    sys.exit(0)

h = html.decode("utf-8", "replace")
open(os.path.join(OUT, "mobile-index.html"), "w", encoding="utf-8").write(h)
print(f"[i] {final}  len={len(h)}")
assets = set(re.findall(r"""(?:src|href)=["']([^"']+\.(?:js|css))["']""", h))
for a in sorted(assets):
    print("  ASSET:", a)

KEYWORDS = ("rollCallToken", "signHistoryId", "scanRollCall", "rollCall",
            "latitude", "longitude", "geolocation", "attendance", "签到")

for a in sorted(assets):
    if not a.endswith(".js"):
        continue
    url = urllib.parse.urljoin(final, a)
    try:
        body, _ = get(url)
    except Exception as e:
        print(f"[!] {a}: {type(e).__name__}")
        continue
    txt = body.decode("utf-8", "replace")
    name = os.path.basename(urllib.parse.urlparse(url).path) or "bundle.js"
    open(os.path.join(OUT, "ml-" + name), "w", encoding="utf-8").write(txt)
    hits = {k: txt.count(k) for k in KEYWORDS if k in txt}
    print(f"\n[+] {a}  {len(txt)//1024}KB  命中={hits}")
    for k in ("rollCallToken", "signHistoryId"):
        for m in list(re.finditer(k, txt))[:3]:
            s = max(0, m.start() - 220)
            print(f"   --- {k} 上下文 ---")
            print("   " + txt[s:m.start() + 220].replace("\n", " ")[:440])
