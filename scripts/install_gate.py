#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install_gate.py — 安装流程门禁（I1–I8）。

在 env_ensure / readiness 之后、宣称「共享可写」之前执行。
专门捕获曾阻断本仓落地的问题：
  - MySQL 表结构与 MysqlVaultStore / ddl_mysql57.sql 不一致
  - vault_consume 导入错误
  - install-inventory Windows 路径 YAML 无法解析
  - vault_review list / store.search 冒烟失败
  - IDE 联接非 LINK
"""
from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import skill_root, vault_root                                      # noqa: E402
from vault_secrets import DEFAULT_MYSQL_SECTION, mysql_conf, redis_conn             # noqa: E402
import vault_store                                                                  # noqa: E402


def item(ok: bool, level: str, detail: str, fix: str = "") -> dict:
    return {"ok": ok, "level": level, "detail": detail, "fix": fix}


def check_i1_ide_links() -> dict:
    script = skill_root() / "scripts" / "common_skills_repo.py"
    if not script.exists():
        return item(False, "BLOCKER", "common_skills_repo.py missing",
                    "clone vault-base into common-skills-repo")
    res = subprocess.run(
        [sys.executable, str(script), "status", "--skill", "vault-base"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = (res.stdout or "") + (res.stderr or "")
    if "LINK" not in out:
        return item(False, "BLOCKER", "no IDE LINK found",
                    "python scripts/common_skills_repo.py link --skill vault-base "
                    "--ides auto --backup-existing")
    n = out.count("LINK")
    # 提示：只应对「已安装」的 IDE 联接；清单用 refresh-ide-links 对齐
    return item(True, "OK",
                "%d IDE LINK(s); prefer --ides auto + inventory ide_links from detect"
                % n)


def check_i2_inventory(team_id: str | None) -> dict:
    if not team_id:
        return item(True, "INFO", "no --id, inventory parse skipped")
    try:
        import install_inventory as inv
    except Exception as e:
        return item(False, "BLOCKER", "import install_inventory failed: %s" % e,
                    "pip install pyyaml")
    try:
        t, n = inv.parse_id(team_id)
        path = inv.inventory_path(t, n)
    except Exception as e:
        return item(False, "BLOCKER", "bad --id: %s" % e,
                    "use --id team-type:team-name")
    if not path.exists():
        return item(False, "DEGRADED", "inventory missing: %s" % path,
                    "python scripts/install_inventory.py init --id %s" % team_id)
    try:
        inv.load_yaml(path)
    except Exception as e:
        return item(
            False, "BLOCKER",
            "inventory YAML unreadable: %s" % e,
            "Windows 路径请用正斜杠或单引号，例如 path: 'C:/Users/.../common-skills-repo'；"
            "勿写 path: \"C:\\Users\\...\"（双引号 + 反斜杠会触发 \\U 转义）",
        )
    miss = inv.missing_fields(inv.load_yaml(path))
    # 主机/账号允许只写在 secrets.local.json（清单可留空或 <in_secrets>）
    conf = mysql_conf(vault_root(), DEFAULT_MYSQL_SECTION) or {}
    rconf = {}
    try:
        from vault_secrets import redis_conf
        rconf = redis_conf(vault_root()) or {}
    except Exception:
        pass
    covered = set()
    if conf.get("host"):
        covered |= {"shared_env.mysql.host", "shared_env.mysql.user"}
    if rconf.get("host"):
        covered.add("shared_env.redis.host")
    miss = [m for m in miss if m not in covered]
    if miss:
        return item(False, "DEGRADED", "inventory missing fields: %s" % ", ".join(miss),
                    "python scripts/install_inventory.py missing --id %s" % team_id)
    return item(True, "OK", "inventory parseable (secrets may cover shared hosts)")


def check_i3_mysql_schema(require_shared: bool) -> dict:
    conf = mysql_conf(vault_root(), DEFAULT_MYSQL_SECTION)
    if not conf or not conf.get("host"):
        return item(
            not require_shared, "DEGRADED" if not require_shared else "BLOCKER",
            "mysql credentials missing",
            "fill references/vault/_kb/secrets.local.json then re-run",
        )
    report = vault_store.MysqlVaultStore.check_schema(conf)
    if report.get("ok"):
        return item(True, "OK", report.get("detail") or "schema ok")
    detail = json.dumps(
        {k: report.get(k) for k in (
            "missing_tables", "missing_columns", "legacy_mismatch", "error", "detail"
        ) if report.get(k)},
        ensure_ascii=False,
    )
    return item(
        False, "BLOCKER",
        "mysql schema incompatible: %s" % detail,
        "apply assets/ddl_mysql57.sql; ensure MysqlVaultStore matches DDL "
        "(no meta_json; table vault_event not vault_events)",
    )


def check_i4_consume_import() -> dict:
    try:
        mod = importlib.import_module("vault_consume")
        # 强制验证曾出错的符号解析路径
        from vault_secrets import redis_conn as _rc  # noqa: F401
        from vault_paths import load_config as _lc  # noqa: F401
        if not hasattr(mod, "main") or not hasattr(mod, "process"):
            return item(False, "BLOCKER", "vault_consume missing main/process",
                        "restore scripts/vault_consume.py from repo")
        return item(True, "OK", "vault_consume importable")
    except Exception as e:
        return item(
            False, "BLOCKER",
            "vault_consume import failed: %s: %s" % (type(e).__name__, e),
            "load_secrets/redis_conn must come from vault_secrets, not vault_paths",
        )


def check_i5_review_list(require_shared: bool) -> dict:
    conf = mysql_conf(vault_root(), DEFAULT_MYSQL_SECTION)
    if not conf or not conf.get("host"):
        return item(not require_shared, "DEGRADED" if not require_shared else "BLOCKER",
                    "skip review list: no mysql conf",
                    "configure secrets.local.json")
    try:
        store = vault_store.open_default()
        if store.dialect != "mysql" and require_shared:
            return item(False, "BLOCKER",
                        "open_default fell back to %s" % store.dialect,
                        "fix mysql reachability / credentials")
        items = store.list_pending(limit=1)
        return item(True, "OK",
                    "vault_review list ok (pending=%d dialect=%s)"
                    % (len(items), store.dialect))
    except Exception as e:
        return item(
            False, "BLOCKER",
            "list_pending failed: %s: %s" % (type(e).__name__, e),
            "MysqlVaultStore must use DDL columns (segments/tags/enqueued_at), not meta_json",
        )


def check_i6_search(require_shared: bool) -> dict:
    conf = mysql_conf(vault_root(), DEFAULT_MYSQL_SECTION)
    if not conf or not conf.get("host"):
        return item(not require_shared, "DEGRADED" if not require_shared else "BLOCKER",
                    "skip search: no mysql conf", "")
    try:
        store = vault_store.open_default()
        store.search("__install_gate_probe__", limit=1)
        return item(True, "OK", "store.search callable")
    except Exception as e:
        return item(
            False, "BLOCKER",
            "search failed: %s: %s" % (type(e).__name__, e),
            "align search SQL with shared_vault_main DDL (status=active, no meta_json)",
        )


def check_i8_consume_timeout() -> dict:
    """空队列 BRPOP 不得抛 TimeoutError（历史阻断）。"""
    import vault_queue as vq
    r = redis_conn(vault_root(), timeout=30)
    if r is None:
        return item(True, "INFO", "redis unreachable; skip BRPOP timeout check")
    try:
        # 使用不存在的分片，确保队列为空
        got = vq.consume_one(r, "__install_gate_no_such_user__", timeout=1)
        if got is not None:
            return item(False, "DEGRADED",
                        "unexpected event on probe user",
                        "inspect vault:events:__install_gate_no_such_user__")
        return item(True, "OK", "BRPOP empty-queue returns None (no TimeoutError)")
    except Exception as e:
        return item(
            False, "BLOCKER",
            "consume_one raised %s: %s" % (type(e).__name__, e),
            "socket timeout must exceed BRPOP wait; see vault_queue.consume_one",
        )
    finally:
        try:
            r.close()
        except Exception:
            pass


def check_i9_prefix_cache(require_shared: bool) -> dict:
    """prefixCache vault:c: SET EX / GET / DEL。"""
    try:
        import vault_prefix_cache as vpc
        cache = vpc.open_cache()
        try:
            report = cache.probe()
        finally:
            cache.close()
    except Exception as e:
        return item(
            not require_shared, "DEGRADED" if not require_shared else "BLOCKER",
            "prefix cache probe error: %s" % e,
            "see references/prefix-cache.md; scripts/vault_prefix_cache.py probe",
        )
    if report.get("ok"):
        return item(True, "OK", report.get("detail") or "vault:c: ok")
    return item(
        not require_shared, "DEGRADED" if not require_shared else "BLOCKER",
        report.get("detail") or "prefix cache probe failed",
        "Redis must allow SET key val EX ttl on vault:c: prefix",
    )


def check_i7_redis(require_shared: bool) -> dict:
    r = redis_conn(vault_root(), timeout=3)
    if r is None:
        return item(
            not require_shared, "DEGRADED" if not require_shared else "BLOCKER",
            "redis unreachable",
            "check secrets redis host/port; Redis 3.2 使用 LIST 后端，不要求 Stream",
        )
    try:
        pong = r.cmd("PING")
        # 轻量 LIST 语义探测（不残留脏数据）
        key = "vault:install_gate:probe"
        r.cmd("DEL", key)
        r.cmd("LPUSH", key, "ok")
        val = r.cmd("RPOP", key)
        r.close()
        if pong != "PONG" or val != "ok":
            return item(False, "BLOCKER",
                        "redis LIST probe unexpected: ping=%s rpop=%s" % (pong, val),
                        "confirm Redis ≥3.2 LIST commands allowed")
        return item(True, "OK", "redis PING + LIST ok")
    except Exception as e:
        return item(False, "BLOCKER", "redis probe failed: %s" % e,
                    "fix network / requirepass / ACL")


def run_gate(args) -> dict:
    require_shared = bool(args.require_shared)
    gates = {
        "I1": check_i1_ide_links(),
        "I2": check_i2_inventory(args.id),
        "I3": check_i3_mysql_schema(require_shared),
        "I4": check_i4_consume_import(),
        "I5": check_i5_review_list(require_shared),
        "I6": check_i6_search(require_shared),
        "I7": check_i7_redis(require_shared),
        "I8": check_i8_consume_timeout(),
        "I9": check_i9_prefix_cache(require_shared),
    }
    blockers = [k for k, v in gates.items() if v["level"] == "BLOCKER" and not v["ok"]]
    degraded = [k for k, v in gates.items() if v["level"] == "DEGRADED" and not v["ok"]]
    return {
        "ready": len(blockers) == 0,
        "gate": gates,
        "blockers": blockers,
        "degraded": degraded,
        "require_shared": require_shared,
        "notes": [
            "I3/I5/I6 catch MysqlVaultStore↔DDL mismatch (historical blocker)",
            "I4 catches vault_consume wrong imports (historical blocker)",
            "I2 catches Windows path YAML escape (historical blocker)",
            "I8 catches BRPOPLPUSH socket TimeoutError on empty queue",
            "I9 catches vault:c: prefixCache SET EX/GET/DEL",
        ],
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="安装流程门禁 I1–I9")
    ap.add_argument("--id", help="物料清单逻辑 id，如 test-team:amrd-test")
    ap.add_argument("--require-shared", action="store_true",
                    help="共享侧检查失败视为 BLOCKER（安装阶段 5+ 必开）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = run_gate(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
