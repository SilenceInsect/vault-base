#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_recall.py — 共享召回门面：prefixCache → MySQL → 本地 Vault。

协议层 A+B（见 collaboration-protocol.md / prefix-cache.md）。
返回带 source 标签的命中列表，供业务 Skill 叠读 merge。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import local_vault_available, vault_root                              # noqa: E402
import vault_prefix_cache as vpc                                                       # noqa: E402
import vault_store                                                                     # noqa: E402

FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def _parse_fm(text: str) -> dict:
    m = FRONT_RE.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _local_hits(vault: Path, skill_id: str, query: str, limit: int) -> list[dict]:
    q = (query or "").lower().strip()
    hits = []
    for sub in ("answers", "decisions", "references", "briefs"):
        d = vault / sub
        if not d.is_dir():
            continue
        for p in d.rglob("*.md"):
            if p.name.startswith("_"):
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            meta = _parse_fm(text)
            sid = meta.get("skill_id") or ""
            if sub == "briefs":
                if skill_id and p.stem != skill_id and not p.stem.startswith(skill_id):
                    continue
            elif skill_id and sid and sid != skill_id:
                continue
            blob = (meta.get("title") or "") + "\n" + text[:2000]
            if q and q not in blob.lower():
                continue
            hits.append({
                "uuid": meta.get("uuid") or p.stem,
                "title": meta.get("title") or p.stem,
                "body": text[:1500],
                "skill_id": sid or skill_id,
                "doc_type": meta.get("doc_type") or sub.rstrip("s"),
                "path": str(p.relative_to(vault)).replace("\\", "/"),
                "source": "local_vault",
            })
            if len(hits) >= limit:
                return hits
    return hits


def _dedupe(hits: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for h in hits:
        key = h.get("uuid") or h.get("path") or h.get("title")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def recall(
    skill_id: str,
    query: str = "",
    *,
    limit: int = 10,
    root: Path | None = None,
    include_local: bool = True,
    use_query_cache: bool = True,
) -> dict:
    """执行叠读召回。

    返回：
      {
        "skill_id", "query", "hits": [...],
        "layers": {"cache": n, "mysql": n, "local": n},
        "cache_available": bool
      }
    """
    vault = Path(root) if root is not None else vault_root()
    local_ok = local_vault_available(vault)
    do_local = bool(include_local and local_ok)

    layers = {"cache": 0, "mysql": 0, "local": 0}
    hits: list[dict] = []
    cache = vpc.open_cache(vault)
    store = None

    try:
        # 1) query 短缓存
        if use_query_cache and skill_id and query and cache.available:
            cached = cache.get_query(skill_id, query)
            if cached:
                for h in cached[:limit]:
                    h = dict(h)
                    h["source"] = h.get("source") or "shared_cache"
                    hits.append(h)
                layers["cache"] = len(hits)
                if do_local:
                    local = _local_hits(vault, skill_id, query, limit)
                    layers["local"] = len(local)
                    hits.extend(local)
                hits = _dedupe(hits)[:limit]
                return {
                    "skill_id": skill_id,
                    "query": query,
                    "hits": hits,
                    "layers": layers,
                    "cache_available": True,
                    "local_vault": "ok" if local_ok else "skipped",
                    "via": "query_cache",
                }

        # 2) skill 索引缓存
        if skill_id and cache.available:
            docs = cache.get_docs_for_skill(skill_id, limit=limit)
            qlow = (query or "").lower()
            for d in docs:
                if qlow and qlow not in (
                    (d.get("title") or "") + "\n" + (d.get("body") or "")
                ).lower():
                    continue
                item = dict(d)
                item["source"] = "shared_cache"
                hits.append(item)
            layers["cache"] = len(hits)

        # 3) MySQL 回源
        need = limit - len(hits)
        if need > 0:
            try:
                store = vault_store.open_default(vault)
                if query:
                    rows = store.search(query, limit=max(need, limit))
                elif skill_id and hasattr(store, "list_by_skill"):
                    rows = store.list_by_skill(skill_id, limit=max(need, limit))
                else:
                    rows = store.search(skill_id or "", limit=max(need, limit))
                # 若指定 skill_id，过滤
                for r in rows:
                    if skill_id and r.get("skill_id") and r.get("skill_id") != skill_id:
                        # search 可能跨 skill；保留 skill 匹配或无 skill 字段的
                        meta = r.get("meta") or {}
                        if meta.get("skill_id") and meta.get("skill_id") != skill_id:
                            continue
                    item = {
                        "uuid": r.get("uuid"),
                        "title": r.get("title"),
                        "body": r.get("body"),
                        "skill_id": r.get("skill_id") or (r.get("meta") or {}).get("skill_id"),
                        "doc_type": r.get("doc_type") or (r.get("meta") or {}).get("doc_type"),
                        "claim_key": r.get("claim_key") or "",
                        "source": r.get("source") or "shared_mysql",
                    }
                    hits.append(item)
                    layers["mysql"] += 1
                    if cache.available:
                        cache.put_from_row(r, source="shared_mysql")
            except Exception as e:
                return {
                    "skill_id": skill_id,
                    "query": query,
                    "hits": _dedupe(hits)[:limit],
                    "layers": layers,
                    "cache_available": cache.available,
                    "error": "%s: %s" % (type(e).__name__, e),
                }

        hits = _dedupe(hits)[:limit]

        # 回填 query 缓存
        if use_query_cache and skill_id and query and cache.available and hits:
            shared_only = [h for h in hits if str(h.get("source", "")).startswith("shared")]
            if shared_only:
                cache.put_query(skill_id, query, shared_only)

        # 4) 本地 Vault（shared_full 未建仓时跳过）
        if do_local:
            local = _local_hits(vault, skill_id, query, limit)
            layers["local"] = len(local)
            hits = _dedupe(hits + local)[:limit]

        return {
            "skill_id": skill_id,
            "query": query,
            "hits": hits,
            "layers": layers,
            "cache_available": cache.available,
            "local_vault": "ok" if local_ok else "skipped",
            "via": "cache_then_mysql",
        }
    finally:
        cache.close()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="共享知识召回（prefixCache → MySQL → 本地）")
    ap.add_argument("--skill-id", required=True)
    ap.add_argument("--query", default="")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--vault")
    ap.add_argument("--no-local", action="store_true")
    ap.add_argument("--no-query-cache", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    root = vault_root(args.vault) if args.vault else vault_root()
    result = recall(
        args.skill_id,
        args.query,
        limit=args.limit,
        root=root,
        include_local=not args.no_local,
        use_query_cache=not args.no_query_cache,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
