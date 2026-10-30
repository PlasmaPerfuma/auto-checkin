# -*- coding: utf-8 -*-
"""探一下 mlearning.sjtu.edu.cn（扫码签到真正的落地平台）的公开面。
只做只读 GET，不碰任何签到接口。"""
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49")
HDRS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def probe(url, follow=False):
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(url, headers=HDRS)
    try:
        with opener.open(req, timeout=25) as r:
            body = r.read(1200)
            print(f"\n=== {url}")
            print(f"  status={r.status}  final={r.url[:120]}")
            print(f"  content-type={r.headers.get('content-type')}")
            print(f"  body[:400]={body[:400]!r}")
            return body
    except urllib.error.HTTPError as e:
        print(f"\n=== {url}")
        print(f"  HTTPError {e.code}  Location={e.headers.get('Location')}")
        print(f"  body[:300]={e.read(300)!r}")
    except Exception as e:
        print(f"\n=== {url}")
        print(f"  {type(e).__name__}: {str(e)[:160]}")
    return None


probe("https://mlearning.sjtu.edu.cn/")
probe("https://mlearning.sjtu.edu.cn/lms/mobile2/forscan/")
probe("https://mlearning.sjtu.edu.cn/lms/mobile2/forscan/?courseCode=95173"
      "&rollCallToken=x_NORMAL_y&signHistoryId=z&fromType=scanRollCall")
