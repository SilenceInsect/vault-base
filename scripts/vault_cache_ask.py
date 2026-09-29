#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_cache_ask.py — 传问题，在 Redis prefixCache 做索引命中与知识召回确认。

仅查 Redis（vault:c:），不回源 MySQL。用于确认缓存是否建好、能否命中。
完整叠读（cache→MySQL→本地）请用 vault_recall.py。

用法：
  python vault_cache_ask.py "install-gate-smoke"
  python vault_cache_ask.py -q "共享知识怎么沉淀" --skill-id vault-base
  python vault_cache_ask.py "..." --skill-id vault-base --json
  python vault_cache_ask.py --skill-id vault-base --list-index
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import vault_root                                                     # noqa: E402
from vault_prefix_cache import open_cache, _print_ask_human                            # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description="Redis prefixCache 问答查找（确认索引命中与知识召回）",
    )
    ap.add_argument("question", nargs="?", default="",
                    help="要查的问题 / 关键词")
    ap.add_argument("--question", "-q", dest="question_opt", default="",
                    help="问题文本（与位置参数二选一）")
    ap.add_argument("--skill-id", default="",
                    help="限定 skill；省略则 SCAN 全部 vault:c:idx:skill:*")
    ap.add_argument("--claim-key", default="", help="按 claim_key 索引查")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--vault")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON 诊断")
    ap.add_argument("--list-index", action="store_true",
                    help="只列出 Redis 里已有的 skill 索引，不查问题")
    ap.add_argument("--warmup", action="store_true",
                    help="未命中时用 vault_recall 回源 MySQL 预热后再查一次 Redis")
    args = ap.parse_args()

    # root 仅用于 secrets/config；目录可不存在（shared_full 无本地仓）
    root = vault_root(args.vault) if args.vault else vault_root()
    cache = open_cache(root)
    try:
        if args.list_index:
            skills = cache.scan_skill_ids()
            out = {"prefix": "vault:c:", "skills": []}
            for sid in skills:
                uuids = cache.list_skill_uuids(sid)
                out["skills"].append({
                    "skill_id": sid,
                    "uuid_count": len(uuids),
                    "uuids": uuids[:20],
                })
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if skills else 1

        q = (args.question_opt or args.question or "").strip()
        if not q and not args.claim_key and not args.skill_id:
            ap.error("请提供问题，例如: python vault_cache_ask.py \"你的问题\"")

        report = cache.ask(
            q, skill_id=args.skill_id, limit=args.limit,
            claim_key=args.claim_key,
        )

        if args.warmup and not report.get("ok") and (args.skill_id or q):
            try:
                import vault_recall
                warm = vault_recall.recall(
                    args.skill_id or "vault-base",
                    q or args.skill_id,
                    limit=args.limit,
                    include_local=False,
                    use_query_cache=True,
                )
                report["warmup"] = {
                    "via": warm.get("via"),
                    "layers": warm.get("layers"),
                    "hit_count": len(warm.get("hits") or []),
                }
                # 预热后再查 Redis
                report = cache.ask(
                    q, skill_id=args.skill_id or "vault-base",
                    limit=args.limit, claim_key=args.claim_key,
                )
                report["warmup_applied"] = True
            except Exception as e:
                report["warmup_error"] = "%s: %s" % (type(e).__name__, e)

        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            _print_ask_human(report)
            if report.get("warmup"):
                print("warmup :", json.dumps(report["warmup"], ensure_ascii=False))
        return 0 if report.get("ok") else 1
    finally:
        cache.close()


if __name__ == "__main__":
    raise SystemExit(main())
