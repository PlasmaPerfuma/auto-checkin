# -*- coding: utf-8 -*-
"""
探 /sign/scan/gis 的参数名。
安全做法：signHistoryId 用一个**全 0 的伪造值**——不存在的场次，
服务器不可能为它生成任何签到记录，纯靠参数校验的报错来反推要哪些参数。
token 从本地 mlearning-token.txt 读，绝不打印。
"""
import json
import os
import sys
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import paths                      # 项目唯一的路径真源（app/paths.py）
HERE = paths.ROOT                 # 兼容旧代码：HERE 一律指项目根
BASE = "https://mlearning.sjtu.edu.cn/lms-lti-rollcall-sjtu"
FAKE_SID = "0" * 32          # 伪造场次，不可能命中任何真实签到
COURSE = "95173"             # 用户那门课，用于 /sign/records

tok = open(paths.TOKEN, encoding="utf-8").read().strip()
print(f"[i] token 已读取（{len(tok)} 字符，不显示）\n")


def get(path, params=None, label=""):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": tok, "Accept": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=30)
        body = r.read().decode("utf-8", "replace")
        st = r.status
    except urllib.error.HTTPError as e:
        st = e.code
        body = e.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"[{label}] 异常 {type(e).__name__}: {str(e)[:120]}")
        return None, ""
    print(f"[{label}] HTTP {st}")
    print(f"    {body[:400]}")
    return st, body


print("=" * 70)
print("① 验证 token + 带 courseCode 调 /sign/records（只读）")
get("/sign/records", {"courseCode": COURSE}, "records")

print("=" * 70)
print("② 探 /sign/scan/gis 需要哪些参数（用伪造 signHistoryId，不可能产生签到）")
get(f"/sign/scan/gis/{FAKE_SID}", None, "gis 无参")

candidates = [
    {"courseCode": COURSE},
    {"courseCode": COURSE, "rollCallToken": "x_NORMAL_y"},
    {"courseCode": COURSE, "rollCallToken": "x_NORMAL_y", "latitude": "31.0257"},
    {"courseCode": COURSE, "rollCallToken": "x_NORMAL_y", "latitude": "31.0257",
     "longitude": "121.4325"},
]
for i, p in enumerate(candidates, 1):
    print("-" * 70)
    get(f"/sign/scan/gis/{FAKE_SID}", p, f"gis 第{i}组 {list(p.keys())}")
