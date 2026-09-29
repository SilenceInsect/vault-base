#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务前钩子：按 preflight YAML 的 recall_query 做 vault Redis 召回，写回清单。

用法：
  python hooks/pre_task_recall.py --skill-dir <业务skill根> --skill-id <id>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="前置 vault 召回")
    ap.add_argument("--skill-dir", required=True)
    ap.add_argument("--skill-id", required=True)
    ap.add_argument("--vault-base", default="")
    ap.add_argument("--query", default="", help="覆盖 preflight 中的 recall_query")
    args = ap.parse_args()

    skill_dir = Path(args.skill_dir).expanduser().resolve()
    checklist = (
        skill_dir / "references" / "vault-integration" / "preflight"
        / ("%s.preflight.yml" % args.skill_id)
    )
    if not checklist.is_file():
        print(json.dumps({
            "ok": False, "error": "missing checklist", "path": str(checklist),
        }, ensure_ascii=False))
        return 2

    text = checklist.read_text(encoding="utf-8")
    query = (args.query or "").strip()
    if not query:
        for line in text.splitlines():
            if line.strip().startswith("recall_query:"):
                query = line.split(":", 1)[1].strip().strip('"').strip("'")
                break
    if not query:
        query = args.skill_id

    vault_base = Path(args.vault_base).expanduser() if args.vault_base else (
        Path.home() / "common-skills-repo" / "vault-base"
    )
    scripts = vault_base.resolve() / "scripts"
    if not scripts.is_dir():
        print(json.dumps({
            "ok": False, "error": "vault-base scripts missing",
            "vault_base": str(vault_base),
        }, ensure_ascii=False))
        return 2

    sys.path.insert(0, str(scripts))
    from vault_paths import vault_root  # type: ignore
    from vault_prefix_cache import open_cache  # type: ignore

    cache = open_cache(vault_root())
    try:
        report = cache.ask(query, skill_id=args.skill_id, limit=8)
    finally:
        cache.close()

    uuids = [h.get("uuid") for h in (report.get("doc_hits") or []) if h.get("uuid")]
    titles = [h.get("title") for h in (report.get("doc_hits") or []) if h.get("title")]

    lines = text.splitlines()
    out_lines = []
    skip = False
    for line in lines:
        if line.strip().startswith("recalled_uuids:"):
            out_lines.append("  recalled_uuids: %s" % json.dumps(uuids, ensure_ascii=False))
            skip = True
            continue
        if line.strip().startswith("recalled_titles:"):
            out_lines.append("  recalled_titles: %s" % json.dumps(titles, ensure_ascii=False))
            skip = True
            continue
        if skip and (line.startswith("  - ") or line.strip() == "[]"):
            continue
        skip = False
        out_lines.append(line)
    checklist.write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    result = {
        "ok": True,
        "redis_available": bool(report.get("redis_available")),
        "query": query,
        "hit_count": len(uuids),
        "uuids": uuids,
        "titles": titles,
        "checklist": str(checklist),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
