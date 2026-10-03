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
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import vault_root                                   # noqa: E402

# 共享环境默认值：本仓不落任何真实内网地址 / 主机名。
# 取值优先级：本机 secrets.local.json 的 redis 段 → 下列环境变量 → 空值（调用方据此提示补配置）
DEFAULT_REDIS = {
    "host": os.environ.get("VAULT_REDIS_HOST", ""),
    "port": int(os.environ.get("VAULT_REDIS_PORT", "6379")),
    "db": 0,
}
# 共享库 section 名同样不写死：优先取 secrets.local.json 的 default_section 字段
DEFAULT_MYSQL_SECTION = os.environ.get("VAULT_MYSQL_SECTION", "")


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
    p = Path(os.environ["VAULT_SECRETS"]) if os.environ.get("VAULT_SECRETS") \
        else (root or vault_root()) / "_kb" / "secrets.local.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_mysql_section(data: dict, section: str | None) -> tuple[str, dict] | tuple[None, None]:
    """定位共享库 section。

    优先级：显式参数 → secrets.local.json 的 default_section → 环境变量 → 仅一个 section 时自动选中。
    section 名只在本地（gitignore 的）secrets.local.json 中出现，本仓不写死。
    """
    sec_all = data.get("sections") or {}
    name = (section or "").strip() or str(data.get("default_section") or "").strip() \
        or DEFAULT_MYSQL_SECTION
    if name and name in sec_all:
        return name, sec_all[name]
    if len(sec_all) == 1:
        only = next(iter(sec_all.items()))
        return only[0], only[1]
    return None, None


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


def mysql_section_name(root: Path | None = None,
                       section: str | None = None) -> str:
    """返回当前生效的共享库 section 名（供诊断/回显使用，不含密码）。"""
    name, _sec = _resolve_mysql_section(load_secrets(root), section)
    return name or ""


def mysql_conf(root: Path | None = None,
               section: str | None = None) -> dict | None:
    data = load_secrets(root)
    _name, sec = _resolve_mysql_section(data, section)
    if not sec:
        return None
    return {
        "host": sec.get("host"), "port": int(sec.get("port", 3306)),
        "user": sec.get("user"), "password": sec.get("password"),
        "database": sec.get("database"),
    }
