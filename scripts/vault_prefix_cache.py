#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_prefix_cache.py — Redis prefixCache（vault:c:）。

契约见 references/prefix-cache.md。
与事件队列键（vault:events: 等）分离；可丢、可重建；真相在 MySQL。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import load_config, vault_root                                    # noqa: E402
from vault_secrets import redis_conn                                              # noqa: E402

CACHE_PREFIX = "vault:c:"
BODY_MAX = 4000
QUERY_TTL_DEFAULT = 3600
PROBE_KEY = CACHE_PREFIX + "probe:install_gate"


def _ttl(root: Path | None = None) -> int:
    cfg = load_config(root)
    try:
        return max(60, int(cfg.get("cache_ttl_seconds") or 172800))
    except (TypeError, ValueError):
        return 172800


def _query_ttl(root: Path | None = None) -> int:
    return min(QUERY_TTL_DEFAULT, _ttl(root))


def doc_key(uuid: str) -> str:
    return CACHE_PREFIX + "doc:" + uuid


def skill_idx_key(skill_id: str) -> str:
    return CACHE_PREFIX + "idx:skill:" + skill_id


def claim_idx_key(claim_key: str) -> str:
    return CACHE_PREFIX + "idx:claim:" + claim_key


def query_key(skill_id: str, query: str) -> str:
    h = hashlib.sha1((query or "").encode("utf-8")).hexdigest()[:16]
    return CACHE_PREFIX + "q:%s:%s" % (skill_id, h)


def qidx_key(skill_id: str) -> str:
    return CACHE_PREFIX + "qidx:" + skill_id


def meta_key(skill_id: str) -> str:
    return CACHE_PREFIX + "meta:" + skill_id


def pack_doc(row: dict, *, source: str = "shared") -> dict:
    """从 store 行或 pending 行压成可缓存摘要。"""
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    body = row.get("body") or ""
    if len(body) > BODY_MAX:
        body = body[:BODY_MAX] + "\n…[truncated]"
    tags = row.get("tags")
    if tags is None:
        tags = meta.get("tags") or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except Exception:
            tags = []
    return {
        "uuid": row.get("uuid") or meta.get("uuid") or "",
        "skill_id": row.get("skill_id") or meta.get("skill_id") or "",
        "doc_type": row.get("doc_type") or meta.get("doc_type") or "answer",
        "title": row.get("title") or meta.get("title") or "",
        "body": body,
        "claim_key": row.get("claim_key") or meta.get("claim_key") or "",
        "content_hash": row.get("content_hash") or meta.get("content_hash") or "",
        "tags": tags if isinstance(tags, list) else [],
        "author": row.get("author") or meta.get("author") or "",
        "source": source,
    }


class PrefixCache:
    """薄封装；redis 不可达时所有写操作 no-op、读返回 miss。"""

    def __init__(self, root: Path | None = None, r=None):
        self.root = root or vault_root()
        self._owned = r is None
        self.r = r if r is not None else redis_conn(self.root, timeout=3)
        self.ttl = _ttl(self.root)
        self.query_ttl = _query_ttl(self.root)

    @property
    def available(self) -> bool:
        return self.r is not None

    def close(self) -> None:
        if self._owned and self.r is not None:
            try:
                self.r.close()
            except Exception:
                pass

    def _setex(self, key: str, value: str, ttl: int) -> bool:
        if not self.r:
            return False
        try:
            self.r.cmd("SET", key, value, "EX", int(ttl))
            return True
        except Exception:
            return False

    def get_doc(self, uuid: str) -> dict | None:
        if not self.r or not uuid:
            return None
        try:
            raw = self.r.cmd("GET", doc_key(uuid))
            if not raw:
                return None
            data = json.loads(raw)
            data["source"] = "shared_cache"
            return data
        except Exception:
            return None

    def put_doc(self, doc: dict) -> bool:
        uuid = doc.get("uuid") or ""
        if not uuid:
            return False
        skill_id = doc.get("skill_id") or ""
        claim = (doc.get("claim_key") or "").strip()
        ok = self._setex(doc_key(uuid), json.dumps(doc, ensure_ascii=False), self.ttl)
        if not ok:
            return False
        if skill_id and self.r:
            try:
                self.r.cmd("SADD", skill_idx_key(skill_id), uuid)
            except Exception:
                pass
        if claim and self.r:
            try:
                self.r.cmd("SADD", claim_idx_key(claim), uuid)
            except Exception:
                pass
        return True

    def put_from_row(self, row: dict, *, source: str = "shared_mysql") -> bool:
        return self.put_doc(pack_doc(row, source=source))

    def list_skill_uuids(self, skill_id: str) -> list[str]:
        if not self.r or not skill_id:
            return []
        try:
            members = self.r.cmd("SMEMBERS", skill_idx_key(skill_id)) or []
            return [m for m in members if isinstance(m, str)]
        except Exception:
            return []

    def get_docs_for_skill(self, skill_id: str, limit: int = 20) -> list[dict]:
        out = []
        for uid in self.list_skill_uuids(skill_id)[: max(1, limit)]:
            doc = self.get_doc(uid)
            if doc:
                out.append(doc)
        return out

    def get_query(self, skill_id: str, query: str) -> list[dict] | None:
        if not self.r:
            return None
        try:
            raw = self.r.cmd("GET", query_key(skill_id, query))
            if not raw:
                return None
            data = json.loads(raw)
            return data if isinstance(data, list) else None
        except Exception:
            return None

    def put_query(self, skill_id: str, query: str, hits: list[dict]) -> bool:
        if not skill_id:
            return False
        key = query_key(skill_id, query)
        slim = []
        for h in hits:
            slim.append({
                "uuid": h.get("uuid"),
                "title": h.get("title"),
                "skill_id": h.get("skill_id") or skill_id,
                "doc_type": h.get("doc_type"),
                "body": (h.get("body") or "")[:800],
                "source": h.get("source") or "shared_cache",
            })
        if not self._setex(key, json.dumps(slim, ensure_ascii=False), self.query_ttl):
            return False
        if self.r:
            try:
                self.r.cmd("SADD", qidx_key(skill_id), key)
            except Exception:
                pass
        return True

    def get_meta(self, skill_id: str) -> Any | None:
        if not self.r:
            return None
        try:
            raw = self.r.cmd("GET", meta_key(skill_id))
            return json.loads(raw) if raw else None
        except Exception:
            return None

    def put_meta(self, skill_id: str, payload: Any) -> bool:
        return self._setex(
            meta_key(skill_id),
            json.dumps(payload, ensure_ascii=False),
            self.ttl,
        )

    def invalidate_skill_queries(self, skill_id: str) -> int:
        """清该 skill 的 query 短缓存与 meta。"""
        if not self.r or not skill_id:
            return 0
        n = 0
        try:
            keys = self.r.cmd("SMEMBERS", qidx_key(skill_id)) or []
            for k in keys:
                try:
                    self.r.cmd("DEL", k)
                    n += 1
                except Exception:
                    pass
            self.r.cmd("DEL", qidx_key(skill_id))
            self.r.cmd("DEL", meta_key(skill_id))
            n += 1
        except Exception:
            pass
        return n

    def invalidate_doc(self, uuid: str, skill_id: str = "", claim_key: str = "") -> None:
        if not self.r or not uuid:
            return
        try:
            self.r.cmd("DEL", doc_key(uuid))
            if skill_id:
                self.r.cmd("SREM", skill_idx_key(skill_id), uuid)
                self.invalidate_skill_queries(skill_id)
            if claim_key:
                self.r.cmd("SREM", claim_idx_key(claim_key), uuid)
        except Exception:
            pass

    def probe(self) -> dict:
        """安装门禁：SET EX / GET / DEL。"""
        if not self.r:
            return {"ok": False, "detail": "redis unavailable"}
        try:
            self.r.cmd("SET", PROBE_KEY, "1", "EX", 30)
            got = self.r.cmd("GET", PROBE_KEY)
            ttl = self.r.cmd("TTL", PROBE_KEY)
            self.r.cmd("DEL", PROBE_KEY)
            ok = got == "1" and isinstance(ttl, int) and 0 < ttl <= 30
            return {
                "ok": ok,
                "detail": "SET EX/GET/TTL/DEL ok" if ok else "unexpected probe result",
                "ttl": ttl,
                "prefix": CACHE_PREFIX,
            }
        except Exception as e:
            return {"ok": False, "detail": "%s: %s" % (type(e).__name__, e)}

    def scan_skill_ids(self, count: int = 200) -> list[str]:
        """SCAN vault:c:idx:skill:*（不用 KEYS）。"""
        if not self.r:
            return []
        prefix = CACHE_PREFIX + "idx:skill:"
        found: list[str] = []
        cursor = "0"
        try:
            while True:
                res = self.r.cmd("SCAN", cursor, "MATCH", prefix + "*", "COUNT", count)
                if not isinstance(res, list) or len(res) < 2:
                    break
                cursor = str(res[0])
                for k in res[1] or []:
                    if isinstance(k, str) and k.startswith(prefix):
                        found.append(k[len(prefix):])
                if cursor == "0":
                    break
        except Exception:
            pass
        return sorted(set(found))

    def ask(
        self,
        question: str,
        *,
        skill_id: str = "",
        limit: int = 10,
        claim_key: str = "",
    ) -> dict:
        """仅查 Redis prefixCache：索引命中 + 正文召回（不回源 MySQL）。

        诊断字段：
          query_cache_hit / skill_index / claim_index / doc_hits / misses
        """
        q = (question or "").strip()
        report: dict[str, Any] = {
            "ok": False,
            "redis_available": self.available,
            "prefix": CACHE_PREFIX,
            "question": q,
            "skill_id": skill_id or None,
            "claim_key": claim_key or None,
            "query_cache_hit": False,
            "query_cache_key": None,
            "skill_index": {},
            "claim_index": {},
            "doc_hits": [],
            "misses": [],
            "via": "redis_only",
        }
        if not self.r:
            report["misses"].append({"stage": "redis", "reason": "unavailable"})
            return report
        if not q and not skill_id and not claim_key:
            report["misses"].append({"stage": "input", "reason": "empty question"})
            return report

        qlow = q.lower()
        hits: list[dict] = []

        # 1) 短查询缓存（需 skill_id）
        if skill_id and q:
            qk = query_key(skill_id, q)
            report["query_cache_key"] = qk
            cached = self.get_query(skill_id, q)
            if cached:
                report["query_cache_hit"] = True
                for h in cached[:limit]:
                    item = dict(h)
                    item["match"] = "query_cache"
                    item["source"] = "shared_cache"
                    hits.append(item)

        # 2) skill 索引 → 拉 doc，按问题子串过滤
        skill_ids = [skill_id] if skill_id else self.scan_skill_ids()
        if skill_id and skill_id not in skill_ids:
            # 指定了 skill 但索引键可能尚无；仍尝试读
            skill_ids = [skill_id]

        for sid in skill_ids:
            uuids = self.list_skill_uuids(sid)
            report["skill_index"][sid] = {
                "key": skill_idx_key(sid),
                "uuid_count": len(uuids),
                "uuids": uuids[:50],
            }
            if not uuids:
                report["misses"].append({
                    "stage": "skill_index",
                    "skill_id": sid,
                    "reason": "empty or missing SET",
                })
                continue
            for uid in uuids:
                doc = self.get_doc(uid)
                if not doc:
                    report["misses"].append({
                        "stage": "doc",
                        "uuid": uid,
                        "skill_id": sid,
                        "reason": "idx member but doc key missing/expired",
                    })
                    continue
                blob = ((doc.get("title") or "") + "\n" + (doc.get("body") or "")).lower()
                if qlow and qlow not in blob:
                    continue
                item = dict(doc)
                item["match"] = "skill_index+doc"
                item["source"] = "shared_cache"
                hits.append(item)

        # 3) claim 索引（可选）
        if claim_key:
            try:
                members = self.r.cmd("SMEMBERS", claim_idx_key(claim_key)) or []
            except Exception:
                members = []
            report["claim_index"] = {
                "key": claim_idx_key(claim_key),
                "uuid_count": len(members),
                "uuids": [m for m in members if isinstance(m, str)][:50],
            }
            for uid in members:
                if not isinstance(uid, str):
                    continue
                doc = self.get_doc(uid)
                if not doc:
                    report["misses"].append({
                        "stage": "doc",
                        "uuid": uid,
                        "reason": "claim idx hit but doc missing",
                    })
                    continue
                item = dict(doc)
                item["match"] = "claim_index+doc"
                item["source"] = "shared_cache"
                hits.append(item)

        # 去重保序
        seen = set()
        uniq = []
        for h in hits:
            uid = h.get("uuid") or ""
            if uid in seen:
                continue
            seen.add(uid)
            uniq.append(h)
        report["doc_hits"] = uniq[:limit]
        report["ok"] = len(report["doc_hits"]) > 0
        report["hit_count"] = len(report["doc_hits"])
        report["index_skills_scanned"] = len(skill_ids)
        return report


def open_cache(root: Path | None = None) -> PrefixCache:
    """打开 prefixCache。

    ``root`` 用于解析 secrets/config；目录不必已存在（shared_full 无本地仓时
    可凭 ``VAULT_SECRETS`` 或默认 redis 主机连接）。
    """
    return PrefixCache(root)


def main() -> int:
    import argparse
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="vault prefixCache 工具")
    ap.add_argument("--vault")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    p_get = sub.add_parser("get-doc")
    p_get.add_argument("uuid")
    p_skill = sub.add_parser("list-skill")
    p_skill.add_argument("skill_id")
    p_skill.add_argument("--limit", type=int, default=20)
    p_ask = sub.add_parser("ask", help="传问题，仅在 Redis prefixCache 查找")
    p_ask.add_argument("question", nargs="?", default="",
                       help="问题文本（也可用 --question）")
    p_ask.add_argument("--question", "-q", dest="question_opt", default="")
    p_ask.add_argument("--skill-id", default="", help="限定技能；空则 SCAN 全部 skill 索引")
    p_ask.add_argument("--claim-key", default="", help="按 claim_key 索引查")
    p_ask.add_argument("--limit", type=int, default=10)
    p_ask.add_argument("--json", action="store_true")
    args = ap.parse_args()
    cache = open_cache(vault_root(args.vault) if args.vault else None)
    try:
        if args.cmd == "probe":
            report = cache.probe()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report.get("ok") else 1
        if args.cmd == "get-doc":
            print(json.dumps(cache.get_doc(args.uuid), ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "list-skill":
            docs = cache.get_docs_for_skill(args.skill_id, limit=args.limit)
            print(json.dumps({"skill_id": args.skill_id, "docs": docs},
                             ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "ask":
            q = (args.question_opt or args.question or "").strip()
            report = cache.ask(
                q, skill_id=args.skill_id, limit=args.limit,
                claim_key=args.claim_key,
            )
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                _print_ask_human(report)
            return 0 if report.get("ok") else 1
    finally:
        cache.close()
    return 2


def _print_ask_human(report: dict) -> None:
    """人读输出：索引命中 vs 正文召回。"""
    print("prefixCache 查询（仅 Redis，不回源 MySQL）")
    print("prefix :", report.get("prefix"))
    print("redis  :", "ok" if report.get("redis_available") else "unavailable")
    print("question:", report.get("question") or "(空)")
    if report.get("skill_id"):
        print("skill  :", report["skill_id"])
    print()
    print("## 索引")
    print("query_cache_hit:", report.get("query_cache_hit"))
    if report.get("query_cache_key"):
        print("query_cache_key:", report["query_cache_key"])
    for sid, info in (report.get("skill_index") or {}).items():
        print("skill_index[%s]: %s uuids=%d key=%s"
              % (sid, "HIT" if info.get("uuid_count") else "MISS",
                 info.get("uuid_count") or 0, info.get("key")))
    ci = report.get("claim_index") or {}
    if ci:
        print("claim_index: %s uuids=%d key=%s"
              % ("HIT" if ci.get("uuid_count") else "MISS",
                 ci.get("uuid_count") or 0, ci.get("key")))
    print()
    print("## 知识召回 doc_hits=%d" % report.get("hit_count", 0))
    for i, h in enumerate(report.get("doc_hits") or [], 1):
        title = h.get("title") or "(no title)"
        print("%d. [%s] %s" % (i, h.get("match") or "?", title))
        print("   uuid   :", h.get("uuid"))
        print("   skill  :", h.get("skill_id"))
        print("   source :", h.get("source"))
        body = (h.get("body") or "").replace("\n", " ")
        if len(body) > 160:
            body = body[:160] + "…"
        if body:
            print("   body   :", body)
    misses = report.get("misses") or []
    if misses:
        print()
        print("## misses=%d" % len(misses))
        for m in misses[:20]:
            print("-", json.dumps(m, ensure_ascii=False))
    if not report.get("ok"):
        print()
        print("结论: 未命中。可先 vault_recall 回源 MySQL 预热，或检查 skill_id / 问题关键词。")
    else:
        print()
        print("结论: 命中 %d 条（均来自 Redis prefixCache）。" % report.get("hit_count", 0))


if __name__ == "__main__":
    raise SystemExit(main())
