#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务中/结案钩子：按分类沉淀结构化知识（本地 YAML + vault_dump + 可选入队）。

用法：
  python hooks/post_task_sediment.py --skill-dir <dir> --skill-id <id> \\
    --kind reusable_decision --title "..." --question "..." --answer "..." \\
    [--scenario X] [--share-scope private|shared] [--confidence 0.8] \\
    [--min-chain "a>b"] [--full-chain "a>b>c"] [--tools "t1,t2"] \\
    [--html-path path] [--enqueue]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(TZ).replace(microsecond=0).isoformat()


def main() -> int:
    ap = argparse.ArgumentParser(description="过程/结案知识沉淀")
    ap.add_argument("--skill-dir", required=True)
    ap.add_argument("--skill-id", required=True)
    ap.add_argument("--vault-base", default="")
    ap.add_argument("--kind", required=True, help="见 knowledge-taxonomy.yml kinds.id")
    ap.add_argument("--title", required=True)
    ap.add_argument("--question", default="")
    ap.add_argument("--answer", required=True)
    ap.add_argument("--scenario", default="")
    ap.add_argument("--share-scope", choices=["private", "shared"], default="private")
    ap.add_argument("--confidence", type=float, default=0.0)
    ap.add_argument("--min-chain", default="")
    ap.add_argument("--full-chain", default="")
    ap.add_argument("--tools", default="", help="逗号分隔")
    ap.add_argument("--html-path", default="")
    ap.add_argument("--review-status", default="pending_human",
                    choices=["pending_human", "human_reviewed", "private_only"])
    ap.add_argument("--enqueue", action="store_true", help="shared 时 scan --enqueue")
    ap.add_argument("--author", default="")
    args = ap.parse_args()

    skill_dir = Path(args.skill_dir).expanduser().resolve()
    vault_base = Path(args.vault_base).expanduser() if args.vault_base else (
        Path.home() / "common-skills-repo" / "vault-base"
    )
    vault_base = vault_base.resolve()
    integ = skill_dir / "references" / "vault-integration"
    out_dir = integ / "sediment"
    out_dir.mkdir(parents=True, exist_ok=True)

    doc_uuid = str(uuid.uuid4())
    tools = [t.strip() for t in (args.tools or "").split(",") if t.strip()]
    shared = args.share_scope == "shared"
    payload = {
        "schema_version": 1,
        "uuid": doc_uuid,
        "skill_id": args.skill_id,
        "kind": args.kind,
        "doc_type": "decision" if "decision" in args.kind else "reference",
        "title": args.title,
        "scenario": args.scenario,
        "share_scope": args.share_scope,
        "is_candidate_shared": shared,
        "review_status": args.review_status,
        "confidence": args.confidence,
        "min_exec_chain": [x.strip() for x in args.min_chain.split(">") if x.strip()],
        "full_exec_chain": [x.strip() for x in args.full_chain.split(">") if x.strip()],
        "tools_used": tools,
        "html_report_path": args.html_path,
        "question": args.question,
        "answer": args.answer,
        "created_at": now_iso(),
    }
    yml = out_dir / ("%s.yml" % doc_uuid)
    # JSON 旁路 + 简 yml
    yml.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    yml.write_text(
        "\n".join([
            "schema_version: 1",
            'uuid: "%s"' % doc_uuid,
            'skill_id: "%s"' % args.skill_id,
            'kind: "%s"' % args.kind,
            'title: "%s"' % args.title.replace('"', "'"),
            'scenario: "%s"' % args.scenario,
            'share_scope: "%s"' % args.share_scope,
            "is_candidate_shared: %s" % ("true" if shared else "false"),
            'review_status: "%s"' % args.review_status,
            "confidence: %s" % args.confidence,
            "question: |",
            "  " + (args.question or "").replace("\n", "\n  "),
            "answer: |",
            "  " + (args.answer or "").replace("\n", "\n  "),
            'html_report_path: "%s"' % args.html_path,
            "created_at: \"%s\"" % payload["created_at"],
        ]) + "\n",
        encoding="utf-8",
    )

    dump_rc = None
    dump_out = ""
    scripts = vault_base / "scripts"
    dump_py = scripts / "vault_dump.py"
    if dump_py.is_file():
        author = args.author or "skill-hook"
        cmd = [
            sys.executable, str(dump_py),
            "--skill-id", args.skill_id,
            "--doc-type", "decision" if shared or "decision" in args.kind else "reference",
            "--author", author,
            "--title", args.title,
            "--question", args.question or args.title,
            "--answer", args.answer,
            "--vault", str(vault_base / "references" / "vault"),
        ]
        if shared:
            cmd.append("--candidate-shared")
        cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        dump_rc = cp.returncode
        dump_out = (cp.stdout or "") + (cp.stderr or "")

    enqueue_rc = None
    if args.enqueue and shared and (scripts / "vault_scan.py").is_file():
        cp2 = subprocess.run(
            [sys.executable, str(scripts / "vault_scan.py"),
             "--user", args.author or "web", "--skill-id", args.skill_id, "--enqueue"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(scripts),
        )
        enqueue_rc = cp2.returncode

    print(json.dumps({
        "ok": True,
        "uuid": doc_uuid,
        "yaml": str(yml),
        "json": str(yml.with_suffix(".json")),
        "vault_dump_rc": dump_rc,
        "enqueue_rc": enqueue_rc,
        "dump_log": dump_out[-1500:],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
