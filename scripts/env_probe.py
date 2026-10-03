#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""env_probe.py — 只读环境探测，stdout 输出纯 JSON。

用法：
  env_probe.py            # 完整探测（约 3 秒）
  env_probe.py --quick    # 钩子用：仅 TCP 探活 + import 检查，2 秒硬超时
  env_probe.py --json     # 强制纯 JSON（默认已是）

退出码：0 = 无 blocker；1 = 有 blocker；2 = 探测本身失败
纪律：--json 时 stdout 必须是纯 JSON，人读旁白一律走 stderr。
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import load_config, vault_root                                    # noqa: E402
from vault_secrets import (                                                          # noqa: E402
    load_secrets, mysql_conf, mysql_section_name, redis_conf, redis_conn,
)

TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(TZ).replace(microsecond=0).isoformat()


def parse_info(text: str) -> dict:
    out = {}
    for line in (text or "").splitlines():
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def tcp_reachable(host: str, port: int, timeout: float) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "ok"
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def git_version() -> str | None:
    try:
        return subprocess.run(
            ["git", "--version"], capture_output=True, text=True, check=True,
        ).stdout.strip().split()[-1]
    except Exception:
        return None


def svn_version() -> str | None:
    try:
        return subprocess.run(
            ["svn", "--version", "--quiet"], capture_output=True, text=True, check=True,
        ).stdout.strip().splitlines()[0]
    except Exception:
        return None


def hips_tray_running() -> bool | None:
    try:
        res = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq HipsTray.exe"],
            capture_output=True, text=True, check=False,
        )
        return "HipsTray.exe" in res.stdout
    except Exception:
        return None


def mysql_identity(conf: dict | None) -> str:
    if not conf:
        return ""
    return "%s@%s:%s/%s" % (
        conf.get("user", "?"), conf.get("host", "?"),
        conf.get("port", 3306), conf.get("database") or "?",
    )


def probe_redis_quick(root: Path, timeout: float) -> dict:
    conf = redis_conf(root)
    host, port = conf["host"], conf["port"]
    ok, detail = tcp_reachable(host, port, timeout)
    return {
        "host": host, "port": port, "reachable": ok,
        "detail": detail if not ok else "tcp ok",
    }


def probe_redis_full(root: Path, timeout: float) -> dict:
    conf = redis_conf(root)
    res = {"host": conf["host"], "port": conf["port"], "reachable": False}
    r = None
    try:
        r = redis_conn(root, timeout=timeout)
        if r is None:
            res["error"] = "connect failed"
            return res
        res["reachable"] = True
        res["ping"] = r.cmd("PING")

        srv = parse_info(r.cmd("INFO", "server"))
        res["version"] = srv.get("redis_version")
        res["os"] = srv.get("os")

        try:
            ci = r.cmd("COMMAND", "INFO", "xadd")
            res["supports_stream"] = bool(ci and ci[0])
        except Exception:
            res["supports_stream"] = False

        try:
            got = r.cmd("CONFIG", "GET", "requirepass")
            val = got[1] if isinstance(got, list) and len(got) > 1 else ""
            res["requirepass_set"] = bool(val)
        except Exception as e:
            res["requirepass_set"] = "ERR:%s" % e
    except Exception as e:
        res["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        if r:
            r.close()
    return res


def probe_mysql_quick(root: Path, timeout: float) -> dict:
    conf = mysql_conf(root)
    if not conf or not conf.get("host"):
        return {"reachable": False, "error": "no mysql credentials in secrets"}
    host = conf["host"]
    port = int(conf.get("port") or 3306)
    ok, detail = tcp_reachable(host, port, timeout)
    return {
        "identity": mysql_identity(conf),
        "reachable": ok,
        "detail": detail if not ok else "tcp ok",
    }


def probe_mysql_full(root: Path, timeout: float) -> dict:
    conf = mysql_conf(root)
    res = {"reachable": False}
    if not conf:
        res["error"] = "no mysql credentials in secrets"
        return res
    res["identity"] = mysql_identity(conf)
    try:
        import pymysql
    except ImportError as e:
        res["error"] = "pymysql not installed: %s" % e
        return res

    conn = None
    try:
        conn = pymysql.connect(
            host=conf["host"], port=int(conf.get("port") or 3306),
            user=conf["user"], password=conf.get("password"),
            database=conf.get("database") or None,
            connect_timeout=max(1, int(timeout)),
            read_timeout=max(2, int(timeout * 2)),
            cursorclass=pymysql.cursors.DictCursor,
        )
        res["reachable"] = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            res["select1"] = cur.fetchone()
            cur.execute("SELECT VERSION() AS v, @@character_set_server AS cs")
            row = cur.fetchone()
            res["version"] = row.get("v") if row else None
            res["charset"] = row.get("cs") if row else None
    except Exception as e:
        res["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    return res


def build_report(root: Path, quick: bool) -> dict:
    cfg = load_config(root)
    timeout = cfg["degrade"]["probe_timeout_ms"] / 1000.0
    if quick:
        timeout = min(timeout, 2.0)

    blockers: list[dict] = []
    degraded: list[dict] = []
    info: list[dict] = []

    # pymysql import check
    try:
        import pymysql  # noqa: F401
        pymysql_ok = True
    except ImportError:
        pymysql_ok = False
        degraded.append({
            "id": "E2", "name": "pymysql",
            "fix": "pip install pymysql",
            "detail": "MySQL 探测降级；本地 sqlite 仍可用",
        })

    if quick:
        redis_r = probe_redis_quick(root, timeout)
        mysql_r = probe_mysql_quick(root, timeout)
    else:
        redis_r = probe_redis_full(root, timeout)
        mysql_r = probe_mysql_full(root, timeout)

    if not redis_r.get("reachable"):
        degraded.append({
            "id": "E6", "name": "redis",
            "fix": "检查网络或联系运维",
            "detail": redis_r.get("error") or redis_r.get("detail", "unreachable"),
        })
    elif not quick:
        info.append({
            "id": "E7", "name": "redis_version",
            "detail": redis_r.get("version", ""),
        })
        info.append({
            "id": "E8", "name": "redis_stream",
            "detail": "supports_stream=%s" % redis_r.get("supports_stream"),
        })

    if not mysql_r.get("reachable"):
        if not pymysql_ok and not mysql_conf(root):
            pass  # no creds + no pymysql = local only, not blocker
        else:
            degraded.append({
                "id": "E3", "name": "mysql",
                "fix": "检查凭据或网络",
                "detail": mysql_r.get("error") or mysql_r.get("detail", "unreachable"),
            })
    elif not quick and mysql_r.get("charset"):
        info.append({
            "id": "E9", "name": "mysql_charset",
            "detail": mysql_r.get("charset"),
        })

    import vault_store
    store = vault_store.open_default(root)
    if store.dialect == "sqlite":
        degraded.append({
            "id": "E4", "name": "store_backend",
            "fix": "配置 _kb/secrets.local.json 并确保 MySQL 可达",
            "detail": "回读身份为 %s" % store.server,
        })

    env = {
        "python": platform.python_version(),
        "python_path": sys.executable,
        "git": git_version(),
        "svn": svn_version(),
        "redis_version": redis_r.get("version"),
        "redis_supports_stream": redis_r.get("supports_stream"),
        "mysql": mysql_r.get("identity") or mysql_identity(mysql_conf(root)),
        "store_server": store.server,
        "hips_tray_running": hips_tray_running(),
        "secrets_loaded": bool(load_secrets(root)),
        "default_redis_host": redis_conf(root).get("host") or "",
        "default_mysql_section": mysql_section_name(root),
    }

    ok = len(blockers) == 0
    return {
        "ok": ok,
        "checked_at": now_iso(),
        "mode": "quick" if quick else "full",
        "blockers": blockers,
        "degraded": degraded,
        "info": info,
        "env": env,
        "redis": redis_r,
        "mysql": mysql_r,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Vault 环境只读探测")
    ap.add_argument("--vault", help="Vault 根目录")
    ap.add_argument("--quick", action="store_true", help="快速 TCP + import 探测")
    ap.add_argument("--json", action="store_true", help="纯 JSON 输出（默认已是）")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        root = vault_root(args.vault)
        report = build_report(root, quick=args.quick)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    except Exception as e:
        err = {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
               "checked_at": now_iso()}
        print(json.dumps(err, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
