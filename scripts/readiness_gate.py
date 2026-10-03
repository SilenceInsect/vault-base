#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""readiness_gate.py — 检查就绪门禁 R1-R8，输出 JSON 结果契约。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import ensure_dirs, skill_root, vault_root, write_default_config      # noqa: E402
from vault_secrets import (                                                            # noqa: E402
    load_secrets, mysql_conf, redis_conn, redis_conf,
)
from vault_scan import init_vault_git, run_git                                         # noqa: E402
import vault_store                                                                     # noqa: E402

FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*", re.S)


def gate(level: str, ok: bool, detail: str, fix: str = "") -> dict:
    return {"ok": ok, "level": level, "detail": detail, "fix": fix}


def count_md(root: Path) -> int:
    return len(list(root.rglob("*.md")))


def check_r1(root: Path, fix: bool) -> dict:
    n = count_md(root)
    if n >= 1:
        return gate("OK", True, "%d markdown files found" % n)
    if fix:
        ensure_dirs(root)
        ph = root / "answers" / "_README.md"
        if not ph.exists():
            ph.parent.mkdir(parents=True, exist_ok=True)
            ph.write_text(
                "---\nschema_version: 1\nuuid: \"00000000-0000-4000-8000-000000000001\"\n"
                "doc_type: reference\ntitle: Vault 占位说明\n---\n# Vault 占位说明\n",
                encoding="utf-8",
            )
        n = count_md(root)
    if n >= 1:
        return gate("OK", True, "%d markdown files (after fix)" % n)
    return gate("DEGRADED", False, "no markdown files in vault",
                "run vault_dump or add notes manually")


def check_r2(root: Path, timeout: float) -> dict:
    r_ok, m_ok = False, False
    r_detail, m_detail = "", ""

    conn = redis_conn(root, timeout=timeout)
    if conn is not None:
        try:
            r_ok = conn.cmd("PING") == "PONG"
            r_detail = "redis PING ok"
        except Exception as e:
            r_detail = "redis: %s" % e
        finally:
            conn.close()
    else:
        r_detail = "redis unreachable"

    conf = mysql_conf(root)
    if conf and conf.get("host"):
        try:
            import pymysql
            c = pymysql.connect(
                host=conf["host"], port=int(conf.get("port") or 3306),
                user=conf["user"], password=conf.get("password"),
                database=conf.get("database"),
                connect_timeout=max(1, int(timeout)),
            )
            with c.cursor() as cur:
                cur.execute("SELECT 1")
            c.close()
            m_ok = True
            m_detail = "mysql SELECT 1 ok"
        except ImportError:
            m_detail = "pymysql missing"
        except Exception as e:
            m_detail = "mysql: %s" % e
    else:
        m_detail = "no mysql credentials"

    if r_ok and m_ok:
        return gate("OK", True, "%s; %s" % (r_detail, m_detail))
    return gate("DEGRADED", False, "%s; %s" % (r_detail, m_detail),
                "check network and _kb/secrets.local.json")


def check_r3(root: Path, fix: bool) -> dict:
    git_dir = root / ".git"
    if fix and not git_dir.exists():
        init_vault_git(root)
        try:
            run_git(["rev-parse", "HEAD"], root)
        except RuntimeError:
            run_git(["add", "-A"], root)
            run_git(["commit", "-m", "vault initial snapshot"], root, check=False)
    if not git_dir.exists():
        return gate("BLOCKER", False, "no .git in vault",
                    "run readiness_gate --fix or env_ensure")
    try:
        run_git(["rev-parse", "HEAD"], root)
        return gate("OK", True, "git HEAD present")
    except RuntimeError as e:
        return gate("BLOCKER", False, "no git HEAD: %s" % e,
                    "git add -A && git commit in vault")


def check_r4(root: Path) -> tuple[dict, bool]:
    store = vault_store.open_default(root)
    # 期望主机统一从 vault_secrets 解析（环境变量 → 本机 secrets 覆盖）；
    # 本仓不写死任何内网地址，未配置时无法比对，仅告警不硬失败。
    expected_host = redis_conf(root).get("host") or ""
    # 无凭据/SQLite：本地可继续（can_write_shared=false）；--require-shared 时升为 BLOCKER
    if store.dialect == "sqlite":
        return gate("DEGRADED", False,
                    "store is sqlite (%s), shared write disabled" % store.server,
                    "configure mysql secrets and ensure DB reachable"), False
    if not expected_host:
        return gate("DEGRADED", False,
                    "no expected host configured (set VAULT_REDIS_HOST or secrets redis.host)",
                    "configure secrets / env, then re-run"), False
    if not store.server.split("@")[-1].startswith(expected_host):
        return gate("DEGRADED", False,
                    "store.server=%s does not match expected host %s" % (
                        store.server, expected_host),
                    "fix secrets mysql section host / use --require-shared to hard-fail"), False
    return gate("OK", True, "shared store: %s" % store.server), True


def parse_skill_frontmatter(skill_dir: Path) -> dict:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return {}
    m = FRONT_RE.match(skill_md.read_text(encoding="utf-8"))
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def check_r5(skill_dir: Path | None) -> dict:
    if skill_dir is None:
        return gate("INFO", True, "no --skill-dir, R5 skipped")
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return gate("BLOCKER", False, "SKILL.md missing in %s" % skill_dir,
                    "provide valid --skill-dir")
    fm = parse_skill_frontmatter(skill_dir)
    accepts = fm.get("accepts_brief", "").lower() == "true"
    bootstrap = skill_root() / "scripts" / "brief_bootstrap.py"
    if not accepts:
        return gate("BLOCKER", False,
                    "SKILL.md lacks accepts_brief: true",
                    "add accepts_brief: true to skill frontmatter")
    if not bootstrap.exists():
        return gate("BLOCKER", False, "brief_bootstrap.py missing in vault-base",
                    "reinstall vault-base skill")
    return gate("OK", True, "accepts_brief and brief_bootstrap present")


def check_r6(root: Path, skill_id: str | None) -> dict:
    if not skill_id:
        return gate("INFO", True, "no --skill-id, R6 skipped")
    p = root / "briefs" / ("%s.md" % skill_id)
    if p.exists():
        return gate("OK", True, "brief exists: %s" % p.name)
    return gate("DEGRADED", False, "missing briefs/%s.md" % skill_id,
                "run brief_bootstrap.py --write or brief_interview.py")


def check_r7(root: Path) -> dict:
    samples = root / "briefs" / "_samples"
    if samples.exists() and any(samples.glob("*.md")):
        n = len(list(samples.glob("*.md")))
        return gate("OK", True, "%d sample briefs" % n)
    return gate("DEGRADED", False, "briefs/_samples empty or missing",
                "run brief_bootstrap.py --samples 1 --write")


def check_r8(root: Path, skill_id: str | None) -> dict:
    if not skill_id:
        return gate("INFO", True, "no --skill-id, R8 skipped")
    p = root / "briefs" / ("%s.md" % skill_id)
    if not p.exists():
        return gate("INFO", True, "no brief to inspect")
    text = p.read_text(encoding="utf-8")
    fm = parse_skill_frontmatter(p) if text.startswith("---") else {}
    missing = []
    if "brief_rev" not in fm:
        missing.append("brief_rev")
    if "updated_at" not in fm:
        missing.append("updated_at")
    if "## 迭代记录" not in text:
        missing.append("迭代记录 section")
    if missing:
        return gate("INFO", False, "brief missing: %s" % ", ".join(missing),
                    "update brief frontmatter and 迭代记录")
    return gate("OK", True, "brief has brief_rev, updated_at, 迭代记录")


def run_gate(args) -> dict:
    root = ensure_dirs(vault_root(args.vault))
    if args.fix:
        write_default_config(root)

    timeout = 2.0 if args.quick else 3.0
    gates: dict[str, dict] = {}

    gates["R1"] = check_r1(root, fix=args.fix)
    gates["R2"] = check_r2(root, timeout)
    gates["R3"] = check_r3(root, fix=args.fix)
    gates["R4"], can_shared = check_r4(root)

    if not args.quick:
        gates["R5"] = check_r5(Path(args.skill_dir) if args.skill_dir else None)
        gates["R6"] = check_r6(root, args.skill_id)
        gates["R7"] = check_r7(root)
        gates["R8"] = check_r8(root, args.skill_id)

    blockers = [k for k, v in gates.items() if v["level"] == "BLOCKER" and not v["ok"]]
    if args.require_shared and (not gates["R2"]["ok"] or not gates["R4"]["ok"]):
        if "R2" not in blockers and not gates["R2"]["ok"]:
            gates["R2"]["level"] = "BLOCKER"
        if "R4" not in blockers and not gates["R4"]["ok"]:
            gates["R4"]["level"] = "BLOCKER"
        blockers = [k for k, v in gates.items() if v["level"] == "BLOCKER" and not v["ok"]]

    ready = len(blockers) == 0
    can_write_local = gates["R1"]["ok"] or gates["R1"]["level"] == "DEGRADED"
    can_scan_git = gates["R3"]["ok"]

    return {
        "ready": ready,
        "gate": gates,
        "capability": {
            "can_write_shared": can_shared and gates["R2"]["ok"],
            "can_write_local": can_write_local,
            "can_scan_git": can_scan_git,
        },
        "vault": str(root),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Vault 就绪门禁 R1-R8")
    ap.add_argument("--vault", help="Vault 根目录")
    ap.add_argument("--skill-dir", help="业务 skill 目录（R5）")
    ap.add_argument("--skill-id", help="业务 skill id（R6/R8）")
    ap.add_argument("--fix", action="store_true", help="自动修复可修复项")
    ap.add_argument("--quick", action="store_true", help="仅 R1-R4")
    ap.add_argument("--require-shared", action="store_true",
                    help="R2/R4 失败视为 BLOCKER")
    ap.add_argument("--json", action="store_true", help="JSON 输出（默认已是）")
    args = ap.parse_args()

    try:
        report = run_gate(args)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        blockers = [k for k, v in report["gate"].items()
                    if v["level"] == "BLOCKER" and not v["ok"]]
        return 0 if not blockers else 1
    except Exception as e:
        print(json.dumps({"ready": False, "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
