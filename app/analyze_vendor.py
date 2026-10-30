# -*- coding: utf-8 -*-
"""从公开的播放器/应用 JS 里挖出 API 端点与直播相关线索。"""
import os
import re
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
V = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")

blobs = {}
for fn in ("kmedia-uni.js", "index.29e19ca7.js"):
    p = os.path.join(V, fn)
    if os.path.isfile(p):
        blobs[fn] = open(p, encoding="utf-8", errors="ignore").read()

EP = re.compile(r"""['"`](/?(?:v\d|api|jy-application-resourcemanage|lms|live)[A-Za-z0-9_\-/{}.$\[\]:]*)['"`]""")

for fn, txt in blobs.items():
    eps = Counter(m.group(1) for m in EP.finditer(txt))
    keep = [e for e in eps if len(e) > 6 and not e.endswith((".js", ".css", ".png", ".jpg"))]
    print(f"\n===== {fn}: {len(keep)} 个疑似端点 =====")
    for e in sorted(keep)[:120]:
        print(f"  {e}")

print("\n\n===== kmedia 里的直播相关字符串 =====")
txt = blobs.get("kmedia-uni.js", "")
pats = [
    r"['\"`]([^'\"`]{0,60}(?:live|Live)[^'\"`]{0,60})['\"`]",
    r"['\"`]([^'\"`]{0,50}m3u8[^'\"`]{0,50})['\"`]",
]
seen = Counter()
for pat in pats:
    for m in re.finditer(pat, txt):
        s = m.group(1).strip()
        if 2 < len(s) < 90:
            seen[s] += 1
for s, c in seen.most_common(60):
    print(f"  [{c:>3}] {s}")
