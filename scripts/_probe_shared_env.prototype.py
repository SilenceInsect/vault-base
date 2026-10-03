#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探测团队公共环境（Redis / MySQL）。

配置全部来自本机（环境变量或 gitignore 的 secrets.local.json），本仓不写死任何内网地址：
  VAULT_SECRETS        本机 secrets.local.json 路径
  VAULT_REDIS_HOST     共享 Redis 主机
  VAULT_REDIS_PORT     共享 Redis 端口（默认 6379）
  VAULT_MYSQL_SECTION  secrets 中共享库 section 名

纪律：
  1. 全程只读，不写任何键、不建任何表
  2. 不打印任何密码明文（requirepass 只报是否为空）
  3. stdout 输出纯 JSON；人读摘要走 stderr

用法：
  <KBPY> probe_shared_env.py            # 摘要 + JSON
  <KBPY> probe_shared_env.py --json     # 纯 JSON
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

SECRETS = Path(os.environ["VAULT_SECRETS"]).expanduser() if os.environ.get("VAULT_SECRETS") \
    else Path(os.environ.get("VAULT_ROOT", ".")).expanduser() / "_kb" / "secrets.local.json"
SEC_NAME = os.environ.get("VAULT_MYSQL_SECTION", "")
REDIS_HOST = os.environ.get("VAULT_REDIS_HOST", "")
REDIS_PORT = int(os.environ.get("VAULT_REDIS_PORT", "6379"))


class Resp:
    """最小 RESP 客户端，兼容 Redis 3.2，无第三方依赖。"""

    def __init__(self, host: str, port: int, timeout: float = 3.0):
        self.sock = socket.create_connection((host, port), timeout)
        self.fp = self.sock.makefile("rb")

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
            data = self.fp.read(n + 2)
            return data[:-2].decode("utf-8", "replace")
        if tag == b"*":
            n = int(body)
            return [self._read() for _ in range(n)] if n >= 0 else None
        raise IOError("未知响应: %r" % line)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def parse_info(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def probe_redis() -> dict:
    res = {"reachable": False}
    r = None
    try:
        r = Resp(REDIS_HOST, REDIS_PORT)
        res["reachable"] = True
        res["ping"] = r.cmd("PING")

        srv = parse_info(r.cmd("INFO", "server"))
        res["version"] = srv.get("redis_version")
        res["os"] = srv.get("os")
        res["arch_bits"] = srv.get("arch_bits")
        res["mode"] = srv.get("redis_mode")
        res["uptime_days"] = round(int(srv.get("uptime_in_seconds", 0)) / 86400.0, 1)
        res["config_file"] = srv.get("config_file") or "(未使用配置文件)"
        res["executable"] = srv.get("executable")

        mem = parse_info(r.cmd("INFO", "memory"))
        res["used_memory_human"] = mem.get("used_memory_human")
        res["maxmemory_human"] = mem.get("maxmemory_human")
        res["maxmemory_policy"] = mem.get("maxmemory_policy")
        res["maxmemory_zero_means_unlimited"] = mem.get("maxmemory") == "0"

        per = parse_info(r.cmd("INFO", "persistence"))
        res["aof_enabled"] = per.get("aof_enabled")
        res["rdb_last_bgsave_status"] = per.get("rdb_last_bgsave_status")
        res["loading"] = per.get("loading")

        cli = parse_info(r.cmd("INFO", "clients"))
        res["connected_clients"] = cli.get("connected_clients")

        rep = parse_info(r.cmd("INFO", "replication"))
        res["role"] = rep.get("role")

        ks = parse_info(r.cmd("INFO", "keyspace"))
        res["keyspace"] = ks

        try:
            res["dbsize"] = r.cmd("DBSIZE")
        except Exception as e:
            res["dbsize_error"] = str(e)

        # Stream 能力探测：3.2 及以下 COMMAND INFO xadd 返回空
        try:
            ci = r.cmd("COMMAND", "INFO", "xadd")
            stream_ok = bool(ci and ci[0])
        except Exception:
            stream_ok = False
        res["supports_stream"] = stream_ok
        for cmd in ("xreadgroup", "xack", "xdel", "unlink", "acl"):
            try:
                ci = r.cmd("COMMAND", "INFO", cmd)
                res["has_" + cmd] = bool(ci and ci[0])
            except Exception as e:
                res["has_" + cmd] = "ERR:%s" % e

        # CONFIG GET 只读项（requirepass 只报有无，不报值）
        for key in ("maxmemory", "maxmemory-policy", "appendonly", "appendfsync",
                    "save", "bind", "protected-mode", "timeout", "databases"):
            try:
                got = r.cmd("CONFIG", "GET", key)
                res["config_" + key] = got[1] if isinstance(got, list) and len(got) > 1 else got
            except Exception as e:
                res["config_" + key] = "ERR:%s" % e
        try:
            got = r.cmd("CONFIG", "GET", "requirepass")
            val = got[1] if isinstance(got, list) and len(got) > 1 else ""
            res["requirepass_set"] = bool(val)
        except Exception as e:
            res["requirepass_set"] = "ERR:%s" % e

        # SCAN 取样，看现有命名空间
        try:
            cur, keys, rounds = "0", [], 0
            while rounds < 20:
                cur, batch = r.cmd("SCAN", cur, "COUNT", "200")
                keys.extend(batch or [])
                rounds += 1
                if cur == "0" or len(keys) >= 500:
                    break
            res["scanned_keys"] = len(keys)
            prefixes = {}
            for k in keys:
                p = k.split(":")[0] if ":" in k else k
                prefixes[p] = prefixes.get(p, 0) + 1
            res["key_prefix_histogram"] = dict(
                sorted(prefixes.items(), key=lambda kv: -kv[1])[:25])
            res["key_samples"] = sorted(keys)[:25]
        except Exception as e:
            res["scan_error"] = str(e)

    except Exception as e:
        res["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        if r:
            r.close()
    return res


def probe_mysql() -> dict:
    res = {"reachable": False}
    if not SECRETS.exists():
        res["error"] = "找不到凭据文件: %s" % SECRETS
        return res
    cfg = json.loads(SECRETS.read_text(encoding="utf-8"))
    sec = cfg.get("sections", {}).get(SEC_NAME, {})
    if not sec:
        res["error"] = "凭据文件里没有 %s 段" % SEC_NAME
        return res
    try:
        import pymysql
    except ImportError as e:
        res["error"] = "当前 python 缺 pymysql：%s" % e
        return res

    conn = None
    try:
        conn = pymysql.connect(
            host=sec["host"], port=int(sec.get("port", 3306)),
            user=sec["user"], password=sec["password"],
            database=sec.get("database") or None,
            connect_timeout=4, read_timeout=6,
            cursorclass=pymysql.cursors.DictCursor)
        res["reachable"] = True
        res["server_identity"] = "%s@%s:%s" % (sec["user"], sec["host"], sec.get("port"))

        with conn.cursor() as cur:
            cur.execute("SELECT VERSION() AS v, @@character_set_server AS cs, "
                        "@@innodb_version AS innodb, @@max_connections AS mc")
            res["auth"] = cur.fetchone()
            cur.execute("SHOW DATABASES")
            res["databases"] = sorted(r["Database"] for r in cur.fetchall())
            cur.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys') "
                "ORDER BY table_schema, table_name")
            rows = cur.fetchall()
            res["all_tables"] = ["%s.%s" % (r["table_schema"], r["table_name"]) for r in rows]
            res["tables_by_schema"] = {}
            for r in rows:
                res["tables_by_schema"].setdefault(r["table_schema"], []).append(r["table_name"])
            target = "shared_vault_main"
            res["plan_tables_present"] = {
                t: t in res["tables_by_schema"].get("script_db_test", [])
                for t in ("shared_vault_main", "shared_vault_pending_review",
                          "shared_vault_audit_log")
            }
            res["plan_tables_present_note"] = "在 %s 库下检查" % target

            cur.execute(
                "SELECT table_schema, table_name, engine, table_rows "
                "FROM information_schema.tables "
                "WHERE table_schema = %s ORDER BY table_name",
                (sec.get("database"),))
            res["current_db_tables"] = cur.fetchall()
    except Exception as e:
        res["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    return res


def main() -> int:
    as_json = "--json" in sys.argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    report = {"redis": probe_redis(), "mysql": probe_mysql()}

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if not as_json:
        r, m = report["redis"], report["mysql"]
        log = []
        log.append("=== Redis @%s:%s ===" % (REDIS_HOST, REDIS_PORT))
        if r.get("reachable"):
            log.append("  version=%s os=%s uptime=%s天" % (
                r.get("version"), r.get("os"), r.get("uptime_days")))
            log.append("  maxmemory=%s policy=%s aof=%s clients=%s" % (
                r.get("maxmemory_human"), r.get("maxmemory_policy"),
                r.get("aof_enabled"), r.get("connected_clients")))
            log.append("  dbsize=%s scanned=%s" % (r.get("dbsize"), r.get("scanned_keys")))
            log.append("  supports_stream=%s" % r.get("supports_stream"))
            log.append("  requirepass_set=%s" % r.get("requirepass_set"))
            log.append("  keyspace=%s" % r.get("keyspace"))
            log.append("  top_prefixes=%s" % json.dumps(
                r.get("key_prefix_histogram"), ensure_ascii=False))
        else:
            log.append("  不可达：%s" % r.get("error"))
        log.append("")
        log.append("=== MySQL @%s ===" % SEC_NAME)
        if m.get("reachable"):
            log.append("  identity=%s" % m.get("server_identity"))
            log.append("  version=%s" % m.get("auth"))
            log.append("  databases=%s" % m.get("databases"))
            log.append("  plan_tables_present=%s" % m.get("plan_tables_present"))
        else:
            log.append("  不可达：%s" % m.get("error"))
        sys.stderr.write("\n".join(log) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
