# -*- coding: utf-8 -*-
"""抠 Tampermonkey 用户脚本，并把 LevelDB 碎片造成的控制字符滤掉，尽量还原可读源码。"""
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
D = os.path.join(os.environ["LOCALAPPDATA"], "Google", "Chrome", "User Data", "Default",
                 "Local Extension Settings", "dhdgffkkebhmkfjojejmpbldmpobfkfo")

blob = b""
for fn in sorted(os.listdir(D)):
    if fn.endswith((".ldb", ".log")):
        blob += open(os.path.join(D, fn), "rb").read()

MARK, BS, Q = b'"value":"', 0x5C, 0x22


def scan(buf, start):
    out = bytearray()
    i, n = start, len(buf)
    while i < n:
        c = buf[i]
        if c == BS:
            if i + 1 >= n:
                return None
            out.append(c)
            out.append(buf[i + 1])
            i += 2
            continue
        if c == Q:
            return bytes(out)
        out.append(c)
        i += 1
    return None


def clean(text):
    """保留可打印字符；把不可打印的碎屑折叠成 ⟪?⟫，避免整段没法读。"""
    text = text.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\")
    out = []
    for ch in text:
        o = ord(ch)
        if ch in "\n\t" or 32 <= o < 127 or o > 0x2000:
            out.append(ch)
        elif o > 127:
            out.append(ch)
        else:
            out.append("⟪?⟫")
    s = "".join(out)
    s = re.sub(r"[A-Za-z0-9+/]{100,}={0,2}", "⟪base64图标⟫", s)
    s = re.sub(r"(⟪\?⟫)+", "⟪?⟫", s)
    return s


want = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
pos, found = 0, 0
while True:
    i = blob.find(MARK, pos)
    if i < 0:
        break
    pos = i + len(MARK)
    raw = scan(blob, pos)
    if not raw:
        continue
    txt = raw.decode("utf-8", errors="replace")
    if "==UserScript==" not in txt:
        continue
    if want and want not in txt.lower():
        continue
    found += 1
    print("=" * 78)
    print(clean(txt)[:7000])
    print()
if not found:
    print("[i] 没找到匹配的脚本")
