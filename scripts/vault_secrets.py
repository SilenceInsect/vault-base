#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_secrets.py — 公共环境凭据与连接的唯一入口。

凭据来源（按优先级）：
  1. 环境变量 VAULT_SECRETS 指向的 json 文件
  2. <vault>/_kb/secrets.local.json
  3. 复用 WebPlatform@AMRD 的 config.yml 解出（见 bootstrap 脚本）

本模块不打印任何密码明文。
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import vault_root                                   # noqa: E402

DEFAULT_REDIS = {"host": "198.51.100.10", "port": 6379, "db": 0}
DEFAULT_MYSQL_SECTION = "mysql_shared"


class RespLite:
    """最小 RESP 客户端，兼容 Redis 3.2，无第三方依赖。

    只实现本项目用到的命令：GET/SET/DEL/EXISTS/LLEN/LPUSH/LREM/LRANGE/
    BRPOPLPUSH/INCR/TTL/EXPIRE/PING/INFO/CONFIG/COMMAND/SCAN
    """

    def __init__(self, host: str, port: int, password: str | None = None,
                 timeout: float = 3.0):
        self.sock = socket.create_connection((host, port), timeout)
        self.fp = self.sock.makefile("rb")
        if password:
            self.cmd("AUTH", password)

    def cmd(self, *args):
        buf = b"*%d\r\n" % len(args)
        for a in args:
            b = str(a).encode("utf-8")
            buf += b"$%d\r\n%s\r\n" % (len(b), b)
        self.sock.sendall(buf)
        return self._read()

    def _read(self):
        line = self.fp.readline()
        if not line:
            raise IOError("连接已被对端关闭")
        tag, body = line[:1], line[1:-2]
        if tag == b"+":
            return body.decode("utf-8", "replace")
        if tag == b"-":
            raise IOError(body.decode("utf-8", "replace"))
        if tag == b":":
            return int(body)
        if tag == b"$":
            n = int(body)
            if n == -1:
                return None
            return self.fp.read(n + 2)[:-2].decode("utf-8", "replace")
        if tag == b"*":
            n = int(body)
            return [self._read() for _ in range(n)] if n >= 0 else None
        raise IOError("未知响应: %r" % line)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def load_secrets(root: Path | None = None) -> dict:
    import os
    p = Path(os.environ["VAULT_SECRETS"]) if os.environ.get("VAULT_SECRETS") \
        else (root or vault_root()) / "_kb" / "secrets.local.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def redis_conf(root: Path | None = None) -> dict:
    sec = load_secrets(root)
    r = dict(DEFAULT_REDIS)
    r.update(sec.get("redis", {}) or {})
    return r


def redis_conn(root: Path | None = None, timeout: float = 3.0) -> RespLite | None:
    """建立 Redis 连接。不可达时返回 None，调用方据此走降级分支。"""
    conf = redis_conf(root)
    try:
        return RespLite(conf["host"], conf["port"], conf.get("password"),
                        timeout=timeout)
    except Exception:
        return None


def mysql_conf(root: Path | None = None,
               section: str = DEFAULT_MYSQL_SECTION) -> dict | None:
    sec = load_secrets(root).get("sections", {}).get(section)
    if not sec:
        return None
    return {
        "host": sec.get("host"), "port": int(sec.get("port", 3306)),
        "user": sec.get("user"), "password": sec.get("password"),
        "database": sec.get("database"),
    }
