# -*- coding: utf-8 -*-
r"""
配置文件的**安全写入**：原子替换 + 自动备份 + 事后可回滚。

为什么要有这一层（2026-10-23，用户要求「写的同时防止基础功能失效」）：
    直接 open(path,'w') 写 json 有三个坑：
      ① 写到一半崩了 → 文件半截 → scheduler 解析失败 → 签到整条链挂掉；
      ② 写错字段/写错格式 → 没有任何人拦；
      ③ 事后想回退 → 没备份。
    这里统一解决：写前备份、写用「临时文件 + os.replace」原子替换、
    写后由调用方跑自检，自检不过就 restore()。

约定：
    · 备份放 <DATA>/backups/，文件名带时间戳，只保留最近 MAX_BACKUPS 份。
    · JSON 一律 UTF-8 无 BOM；scheduler 用 utf-8-sig 读，两种都兼容。
    · 失败抛 ConfigError（带 code），不 print、不 sys.exit。
"""
import glob
import json
import os
import time

import paths

MAX_BACKUPS = 30


class ConfigError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def backups_dir():
    d = os.path.join(paths.DATA, "backups")
    os.makedirs(d, exist_ok=True)
    return d


def read(path, default=None):
    """读 JSON。文件不存在/为空 → 返回 default。解析失败抛 ConfigError。"""
    if not os.path.isfile(path):
        return default
    try:
        with open(path, encoding="utf-8-sig") as f:
            raw = f.read()
    except Exception as e:
        raise ConfigError("read_failed", f"读不了 {os.path.basename(path)}：{e}")
    if not raw.strip():
        return default
    try:
        return json.loads(raw)
    except Exception as e:
        raise ConfigError("bad_json",
                          f"{os.path.basename(path)} 不是合法 JSON：{e}")


def _backup(path):
    """把现有文件复制到 backups/，返回备份文件名（没有原文件则返回空串）。"""
    if not os.path.isfile(path):
        return ""
    base = os.path.basename(path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{base}.{stamp}.bak"
    dst = os.path.join(backups_dir(), name)
    i = 1
    while os.path.exists(dst):          # 同一秒内多次写，加序号防覆盖
        name = f"{base}.{stamp}-{i}.bak"
        dst = os.path.join(backups_dir(), name)
        i += 1
    try:
        with open(path, "rb") as s, open(dst, "wb") as t:
            t.write(s.read())
    except Exception as e:
        raise ConfigError("backup_failed", f"备份失败，为安全起见不写：{e}")
    _prune_backups(base)
    return name


def _prune_backups(base, keep=MAX_BACKUPS):
    try:
        files = sorted(glob.glob(os.path.join(backups_dir(), f"{base}.*.bak")))
        for f in files[:-keep]:
            os.remove(f)
    except Exception:
        pass


def write_json(path, obj):
    """原子写：临时文件 → fsync → os.replace；写前自动备份。

    返回备份文件名（空串表示原本没有文件）。
    """
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    bak = _backup(path)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)          # 原子替换：要么旧内容，要么新内容，不会有半截
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise ConfigError("write_failed", f"写入失败：{e}")
    return bak


def list_backups(path):
    base = os.path.basename(path)
    try:
        files = sorted(glob.glob(os.path.join(backups_dir(), f"{base}.*.bak")), reverse=True)
        return [os.path.basename(f) for f in files]
    except Exception:
        return []


def restore(path, name=None):
    """从备份恢复。name 为空则取最近一份。返回被恢复的备份名。"""
    base = os.path.basename(path)
    if name:
        src = os.path.join(backups_dir(), name)
        if not os.path.isfile(src) or not name.startswith(base + ".") or not name.endswith(".bak"):
            raise ConfigError("bad_backup", f"没有这个备份：{name}")
    else:
        items = list_backups(path)
        if not items:
            raise ConfigError("no_backup", f"{base} 没有可用备份")
        name = items[0]
        src = os.path.join(backups_dir(), name)
    try:
        with open(src, "rb") as f:
            data = f.read()
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception as e:
        raise ConfigError("restore_failed", f"恢复失败：{e}")
    return name
