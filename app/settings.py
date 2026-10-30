# -*- coding: utf-8 -*-
r"""
用户可调设置（签到坐标 / 截图间隔 / 课后继续盯多久）。

存放：`data/settings.json`（可写目录，打包后落 %APPDATA%）。
设计原则：
    · **文件不存在就是默认值**——绝不因为缺文件而报错，签到照常跑；
    · 每一项都有上下限，越界直接拒绝（抛 ConfigError），不"猜用户想要什么"；
    · 坐标必须落在交大闵行校园范围内（用户明确要求过）；
    · `load()` 永远不会抛异常：某个字段坏了就该项回落默认值，其它照用。
"""
import copy
import os

import config_store
import paths

SETTINGS = os.path.join(paths.DATA, "settings.json")

DEFAULTS = {
    "checkin": {
        "lat": "31.0269",
        "lng": "121.4335",
        "address": "上海市闵行区东川路800号上海交通大学",
    },
    "watch": {
        "sampleGap": 0.6,     # 两次截图之间歇多久（秒）
        "postMin": 10,        # 课后继续盯多久（分钟）
        "liveEvery": 5,       # 控制台实时画面多久刷新一次（秒）；0 = 关闭
    },
}

# 交大闵行校区的大致外接矩形（宽松一点，留出东川路/剑川路两侧）
LAT_RANGE = (30.96, 31.09)
LNG_RANGE = (121.36, 121.52)
GAP_RANGE = (0.2, 3.0)
POST_MIN_RANGE = (0, 30)
LIVE_EVERY_RANGE = (0, 60)     # 0 表示不写实时画面（省磁盘/CPU）


def _deep_merge(base, patch):
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load():
    """返回完整设置。**永不抛异常**：坏掉的项回落默认值。"""
    out = copy.deepcopy(DEFAULTS)
    try:
        raw = config_store.read(SETTINGS, None)
    except Exception:
        return out
    if not isinstance(raw, dict):
        return out
    merged = _deep_merge(out, raw)
    # 逐项校验，坏的单独回落（不影响其它项）
    try:
        validate(merged)
        return merged
    except Exception:
        pass
    for section, keys in DEFAULTS.items():
        for key, dv in keys.items():
            try:
                cand = copy.deepcopy(out)
                cand[section][key] = merged.get(section, {}).get(key, dv)
                validate(cand)
                out[section][key] = cand[section][key]
            except Exception:
                out[section][key] = dv
    return out


def _num(v, code, label, lo, hi, integer=False):
    try:
        x = int(v) if integer else float(v)
    except Exception:
        raise config_store.ConfigError(code, f"{label} 必须是数字，收到 {v!r}")
    if x < lo or x > hi:
        raise config_store.ConfigError(code, f"{label} 必须在 {lo}~{hi} 之间，收到 {x}")
    return x


def validate(d):
    """严格校验，不合规抛 ConfigError。返回规范化后的字典。"""
    if not isinstance(d, dict):
        raise config_store.ConfigError("bad_args", "设置必须是一个对象")

    ck = d.get("checkin")
    if not isinstance(ck, dict):
        raise config_store.ConfigError("bad_args", "缺少 checkin 段")
    # 分两步报错，提示才准：先确认"是数字"，再确认"在校园范围内"
    lat = _num(ck.get("lat"), "bad_lat", "纬度", -90, 90)
    lng = _num(ck.get("lng"), "bad_lng", "经度", -180, 180)
    if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LNG_RANGE[0] <= lng <= LNG_RANGE[1]):
        raise config_store.ConfigError(
            "out_of_campus",
            f"坐标必须落在交大闵行校区范围内"
            f"（纬度 {LAT_RANGE[0]}~{LAT_RANGE[1]}，经度 {LNG_RANGE[0]}~{LNG_RANGE[1]}），"
            f"收到 {lat}, {lng}")
    addr = ck.get("address")
    if not isinstance(addr, str) or not addr.strip():
        raise config_store.ConfigError("bad_args", "address 不能为空")

    w = d.get("watch")
    if not isinstance(w, dict):
        raise config_store.ConfigError("bad_args", "缺少 watch 段")
    gap = _num(w.get("sampleGap"), "bad_gap", "截图间隔",
               GAP_RANGE[0], GAP_RANGE[1])
    post = _num(w.get("postMin"), "bad_post", "课后继续盯", POST_MIN_RANGE[0],
                POST_MIN_RANGE[1], integer=True)
    live = _num(w.get("liveEvery"), "bad_live", "实时画面刷新间隔",
                LIVE_EVERY_RANGE[0], LIVE_EVERY_RANGE[1], integer=True)

    return {
        "checkin": {"lat": f"{lat}", "lng": f"{lng}", "address": addr.strip()},
        "watch": {"sampleGap": gap, "postMin": post, "liveEvery": live},
    }


def save(patch):
    """合并 + 校验 + 原子写。返回 (新设置, 备份文件名)。"""
    cur = load()
    cand = _deep_merge(cur, patch or {})
    clean = validate(cand)
    bak = config_store.write_json(SETTINGS, clean)
    return clean, bak
