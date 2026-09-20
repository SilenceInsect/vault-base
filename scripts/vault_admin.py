#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_admin.py — 运维与自愈。"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import vault_root                                                          # noqa: E402
from vault_secrets import redis_conn                                                         # noqa: E402
import vault_store                                                                           # noqa: E402

FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*", re.S)
DOC_DIRS = ("answers", "decisions", "references", "briefs")


def parse_frontmatter(text: str) -> dict:
    m = FRONT_RE.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def cmd_stats(args) -> int:
    root = vault_root(args.vault)
    counts = {}
    for d in DOC_DIRS:
        p = root / d
        counts[d] = len(list(p.rglob("*.md"))) if p.exists() else 0
    counts["total_md"] = sum(counts.values())

    redis_ok = False
    r = redis_conn(root, timeout=2)
    if r is not None:
        try:
            redis_ok = r.cmd("PING") == "PONG"
        except Exception:
            pass
        finally:
            r.close()

    store = vault_store.open_default(root)
    report = {
        "vault": str(root),
        "md_counts": counts,
        "redis_ping": redis_ok,
        "store_server": store.server,
        "store_dialect": store.dialect,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_check(args) -> int:
    root = vault_root(args.vault)
    issues = []
    for d in DOC_DIRS:
        p = root / d
        if not p.exists():
            continue
        for md in p.rglob("*.md"):
            if md.name.startswith("_"):
                continue
            text = md.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            rel = md.relative_to(root).as_posix()
            if "schema_version" not in fm:
                issues.append({"file": rel, "issue": "missing schema_version"})
            if "uuid" not in fm:
                issues.append({"file": rel, "issue": "missing uuid"})
    report = {"vault": str(root), "issues": issues, "ok": len(issues) == 0}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def cmd_doctor(args) -> int:
    root = vault_root(args.vault)
    import readiness_gate
    gate_args = argparse.Namespace(
        vault=args.vault, skill_dir=None, skill_id=None,
        fix=False, quick=False, require_shared=False, json=True,
    )
    readiness = readiness_gate.run_gate(gate_args)

    git_status = ""
    if (root / ".git").exists():
        res = subprocess.run(
            ["git", "status", "--short"], cwd=str(root),
            capture_output=True, text=True,
        )
        git_status = res.stdout.strip() or "(clean)"

    report = {
        "vault": str(root),
        "readiness": readiness,
        "git_status": git_status,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if readiness["ready"] else 1


def cmd_replay(args) -> int:
    print(json.dumps({
        "message": "replay is not implemented yet",
        "limit": args.limit,
    }, ensure_ascii=False))
    return 0


def cmd_migrate(args) -> int:
    print(json.dumps({
        "message": "migrate not implemented",
        "target_schema": args.to,
    }, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Vault 运维工具")
    ap.add_argument("--vault", help="Vault 根目录")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("stats", help="统计本地 MD 与连接状态")
    sub.add_parser("check", help="校验 frontmatter 契约")
    sub.add_parser("doctor", help="就绪门禁 + git 状态")
    p_replay = sub.add_parser("replay", help="DLQ 重放（暂未实现）")
    p_replay.add_argument("--limit", type=int, default=10)
    p_migrate = sub.add_parser("migrate", help="schema 迁移（暂未实现）")
    p_migrate.add_argument("--to", type=int, required=True)

    args = ap.parse_args()
    handlers = {
        "stats": cmd_stats,
        "check": cmd_check,
        "doctor": cmd_doctor,
        "replay": cmd_replay,
        "migrate": cmd_migrate,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
