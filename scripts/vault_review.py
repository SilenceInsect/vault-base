#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_review.py — 共享知识审核旁路。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import vault_root                                                          # noqa: E402
import vault_store                                                                           # noqa: E402


def cmd_list(args, store) -> int:
    items = store.list_pending(limit=args.limit, only_needs_llm=args.needs_llm)
    if not items:
        print(json.dumps({"pending": [], "message": "empty list"},
                         ensure_ascii=False, indent=2))
        return 0
    print(json.dumps({"pending": items}, ensure_ascii=False, indent=2))
    return 0


def cmd_show(args, store) -> int:
    items = store.list_pending(limit=500)
    match = [x for x in items if x["uuid"] == args.uuid]
    if not match:
        print(json.dumps({"error": "not found", "uuid": args.uuid}, ensure_ascii=False))
        return 1
    print(json.dumps(match[0], ensure_ascii=False, indent=2))
    return 0


def cmd_approve(args, store) -> int:
    n = store.approve(args.uuid, args.reviewer, note=args.note or "")
    print(json.dumps({"uuid": args.uuid, "approved": n > 0}, ensure_ascii=False))
    return 0 if n else 1


def cmd_reject(args, store) -> int:
    if not args.note:
        print(json.dumps({"error": "--note required for reject"}, ensure_ascii=False))
        return 1
    n = store.reject(args.uuid, args.reviewer, note=args.note)
    print(json.dumps({"uuid": args.uuid, "rejected": n > 0}, ensure_ascii=False))
    return 0 if n else 1


def cmd_batch_approve(args, store) -> int:
    items = store.list_pending(limit=args.limit)
    approved = []
    for item in items:
        meta = item.get("meta") or {}
        if args.author and meta.get("author") != args.author:
            continue
        if store.approve(item["uuid"], args.reviewer, note="batch"):
            approved.append(item["uuid"])
    print(json.dumps({"approved": approved, "count": len(approved)},
                     ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="共享知识审核")
    ap.add_argument("--vault", help="Vault 根目录")
    sub = ap.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出待审")
    p_list.add_argument("--limit", type=int, default=30)
    p_list.add_argument("--needs-llm", action="store_true")

    p_show = sub.add_parser("show", help="查看详情")
    p_show.add_argument("uuid")

    p_app = sub.add_parser("approve", help="批准")
    p_app.add_argument("uuid")
    p_app.add_argument("--reviewer", required=True)
    p_app.add_argument("--note", default="")

    p_rej = sub.add_parser("reject", help="拒绝")
    p_rej.add_argument("uuid")
    p_rej.add_argument("--reviewer", required=True)
    p_rej.add_argument("--note", required=True)

    p_batch = sub.add_parser("batch-approve", help="批量批准")
    p_batch.add_argument("--reviewer", required=True)
    p_batch.add_argument("--author", default="")
    p_batch.add_argument("--limit", type=int, default=20)

    args = ap.parse_args()
    store = vault_store.open_default(vault_root(args.vault))

    handlers = {
        "list": cmd_list,
        "show": cmd_show,
        "approve": cmd_approve,
        "reject": cmd_reject,
        "batch-approve": cmd_batch_approve,
    }
    return handlers[args.command](args, store)


if __name__ == "__main__":
    sys.exit(main())
