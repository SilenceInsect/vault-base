#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""laya_export_dataset.py — 从 vault-base 已有知识导出 Laya 微调 JSONL。

数据源（按优先级合并去重）：
  1. references/laya/dataset.sample.jsonl（种子，可用 --no-seed 关掉）
  2. 本地 Vault MD：answers|decisions|references/*.md
  3. MySQL shared_vault_main（active = share 正样本）
  4. MySQL shared_vault_pending_review 历史（approved/rejected/pending）

标签规则：
  - main.active / pending.approved → share_decision=share, is_sensitive≈0.05
  - pending.rejected → private；备注含敏感词则 noul 抬高
  - 本地 MD is_candidate_shared=false → private
  - 本地 MD is_candidate_shared=true 且未进共享 → needs_human（弱标签；可 --local-as-share）

训练请用上游 RLCD notebook（见 references/laya.md §微调）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from laya_client import share_gate_questions                                   # noqa: E402
from vault_paths import vault_root                                            # noqa: E402
import vault_store                                                            # noqa: E402

FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
DOC_DIRS = ("answers", "decisions", "references")
SENS_KEYS = ("password", "secret", "api_key", "密码", "密钥", "credential", "token")


def parse_frontmatter(text: str) -> dict:
    m = FRONT_RE.match(text)
    if not m:
        return {}
    out: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            out[k] = [x.strip().strip('"').strip("'")
                      for x in v[1:-1].split(",") if x.strip()]
        elif v in ("true", "false"):
            out[k] = v == "true"
        elif v.isdigit():
            out[k] = int(v)
        else:
            out[k] = v.strip('"').strip("'")
    return out


def split_sections(text: str) -> dict:
    body = FRONT_RE.sub("", text, count=1)
    parts = re.split(r"^##\s+", body, flags=re.M)
    seg: dict[str, str] = {}
    for p in parts[1:]:
        head, _, rest = p.partition("\n")
        seg[head.strip()] = rest.strip()
    return {
        "question": seg.get("问题", "") or seg.get("Question", ""),
        "answer": seg.get("答案&决策", "") or seg.get("答案", "") or seg.get("决策", ""),
        "basis": seg.get("决策依据", "") or seg.get("依据", ""),
        "remark": seg.get("备注", "") or seg.get("Remark", ""),
    }


def _body_segments(body: str) -> dict:
    if not body:
        return {"question": "", "answer": "", "basis": "", "remark": ""}
    if "##" in body:
        return split_sections("---\n\n---\n" + body if not body.startswith("---") else body)
    # 无章节：整段当 answer
    return {"question": "", "answer": body.strip(), "basis": "", "remark": ""}


def _parse_segments_field(raw: Any) -> dict:
    if isinstance(raw, dict):
        return {
            "question": raw.get("question") or "",
            "answer": raw.get("answer") or "",
            "basis": raw.get("basis") or "",
            "remark": raw.get("remark") or "",
        }
    if isinstance(raw, str) and raw.strip():
        try:
            return _parse_segments_field(json.loads(raw))
        except json.JSONDecodeError:
            return _body_segments(raw)
    return {"question": "", "answer": "", "basis": "", "remark": ""}


def _sensitive_noul(text: str, default: float) -> float:
    blob = (text or "").lower()
    if any(k in blob for k in SENS_KEYS):
        return max(default, 0.9)
    return default


def build_example(
    *,
    uuid: str,
    title: str,
    skill_id: str,
    doc_type: str,
    tags: list,
    segments: dict,
    share_label: str,
    sens: float,
    source: str,
    status: str = "",
    reviewer: str = "",
) -> dict:
    questions = share_gate_questions()
    questions["share_decision"]["label"] = share_label
    questions["is_sensitive"]["label"] = float(sens)
    state = {
        "skill_id": skill_id or "",
        "doc_type": doc_type or "answer",
        "title": title or "",
        "question": segments.get("question") or "",
        "answer": segments.get("answer") or "",
        "basis": segments.get("basis") or "",
        "remark": segments.get("remark") or "",
        "tags": tags or [],
        "is_candidate_shared": share_label == "share",
        "source_uuid": uuid or "",
    }
    return {
        "state": state,
        "questions": questions,
        "meta": {
            "source": source,
            "status": status,
            "reviewer": reviewer,
            "uuid": uuid,
            "share_label": share_label,
        },
    }


def export_from_local_md(root: Path, *, local_as_share: bool) -> list[dict]:
    out: list[dict] = []
    for sub in DOC_DIRS:
        d = root / sub
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.md")):
            text = path.read_text(encoding="utf-8", errors="replace")
            meta = parse_frontmatter(text)
            segs = split_sections(text)
            # 空笔记跳过
            if not (segs.get("answer") or segs.get("question") or meta.get("title")):
                continue
            uuid = str(meta.get("uuid") or path.stem)
            candidate = bool(meta.get("is_candidate_shared"))
            blob = json.dumps({**meta, **segs}, ensure_ascii=False)
            if not candidate:
                label, sens = "private", _sensitive_noul(blob, 0.15)
            elif local_as_share:
                label, sens = "share", _sensitive_noul(blob, 0.05)
            else:
                # 未审共享候选：弱标签 needs_human（避免把未审当正样本）
                label, sens = "needs_human", _sensitive_noul(blob, 0.1)
            tags = meta.get("tags") or []
            if not isinstance(tags, list):
                tags = []
            out.append(build_example(
                uuid=uuid,
                title=str(meta.get("title") or path.stem),
                skill_id=str(meta.get("skill_id") or ""),
                doc_type=str(meta.get("doc_type") or sub.rstrip("s") or "answer"),
                tags=tags,
                segments=segs,
                share_label=label,
                sens=sens,
                source="local_md:%s" % sub,
                status="local",
            ))
    return out


def export_from_mysql_main(store, *, limit: int) -> list[dict]:
    if type(store).__name__ != "MysqlVaultStore":
        return []
    out: list[dict] = []
    with store._conn.cursor() as cur:
        cur.execute(
            "SELECT uuid, title, body, skill_id, doc_type, tags, segments, "
            "status, reviewer, review_note "
            "FROM shared_vault_main WHERE status='active' "
            "ORDER BY updated_at DESC LIMIT %s",
            (limit,),
        )
        rows = cur.fetchall() or []
    for r in rows:
        segs = _parse_segments_field(r.get("segments"))
        if not any(segs.values()):
            segs = _body_segments(r.get("body") or "")
        tags = r.get("tags") or []
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except json.JSONDecodeError:
                tags = [t for t in tags.split(",") if t.strip()]
        blob = (r.get("title") or "") + "\n" + (r.get("body") or "")
        out.append(build_example(
            uuid=r["uuid"],
            title=r.get("title") or "",
            skill_id=r.get("skill_id") or "",
            doc_type=r.get("doc_type") or "answer",
            tags=tags if isinstance(tags, list) else [],
            segments=segs,
            share_label="share",
            sens=_sensitive_noul(blob, 0.05),
            source="mysql_main",
            status=r.get("status") or "active",
            reviewer=r.get("reviewer") or "",
        ))
    return out


def export_from_mysql_pending(
    store, *, limit: int, include_pending: bool,
) -> list[dict]:
    if type(store).__name__ != "MysqlVaultStore":
        return []
    statuses = ["approved", "rejected"]
    if include_pending:
        statuses.append("pending")
    placeholders = ",".join(["%s"] * len(statuses))
    out: list[dict] = []
    with store._conn.cursor() as cur:
        cur.execute(
            "SELECT uuid, title, body, skill_id, doc_type, tags, segments, "
            "status, reviewer, review_note, llm_verdict, llm_confidence "
            "FROM shared_vault_pending_review WHERE status IN (%s) "
            "ORDER BY enqueued_at DESC LIMIT %s" % (placeholders, "%s"),
            (*statuses, limit),
        )
        rows = cur.fetchall() or []
    for r in rows:
        status = (r.get("status") or "").lower()
        note = (r.get("review_note") or "") + "\n" + (r.get("body") or "")
        if status == "approved":
            label, sens = "share", _sensitive_noul(note, 0.05)
        elif status == "rejected":
            label, sens = "private", _sensitive_noul(note, 0.15)
        else:
            lv = (r.get("llm_verdict") or "").lower()
            if lv in ("share", "private"):
                label, sens = lv, 0.1 if lv == "share" else 0.2
            else:
                label, sens = "needs_human", 0.1
        segs = _parse_segments_field(r.get("segments"))
        if not any(segs.values()):
            segs = _body_segments(r.get("body") or "")
        tags = r.get("tags") or []
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except json.JSONDecodeError:
                tags = []
        out.append(build_example(
            uuid=r["uuid"],
            title=r.get("title") or "",
            skill_id=r.get("skill_id") or "",
            doc_type=r.get("doc_type") or "answer",
            tags=tags if isinstance(tags, list) else [],
            segments=segs,
            share_label=label,
            sens=sens,
            source="mysql_pending",
            status=status,
            reviewer=r.get("reviewer") or "",
        ))
    return out


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        items.append(json.loads(line))
    return items


def dedupe(examples: Iterable[dict]) -> list[dict]:
    """同 uuid：mysql_main > mysql_pending > local_md > seed。"""
    rank = {
        "mysql_main": 40,
        "mysql_pending": 30,
        "local_md": 20,
        "seed": 10,
    }

    def _rank(ex: dict) -> int:
        src = (ex.get("meta") or {}).get("source") or ""
        for k, v in rank.items():
            if src.startswith(k):
                return v
        return 0

    best: dict[str, dict] = {}
    order: list[str] = []
    for ex in examples:
        st = ex.get("state") or {}
        key = st.get("source_uuid") or st.get("title") or json.dumps(st, ensure_ascii=False)[:80]
        if key not in best:
            best[key] = ex
            order.append(key)
            continue
        if _rank(ex) > _rank(best[key]):
            best[key] = ex
    return [best[k] for k in order]


def summarize(examples: list[dict]) -> dict:
    by_src: dict[str, int] = {}
    by_label: dict[str, int] = {}
    for ex in examples:
        src = ((ex.get("meta") or {}).get("source") or "unknown").split(":")[0]
        by_src[src] = by_src.get(src, 0) + 1
        lab = (ex.get("questions") or {}).get("share_decision", {}).get("label") or "?"
        by_label[lab] = by_label.get(lab, 0) + 1
    return {"by_source": by_src, "by_label": by_label}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="从 vault 已有决策导出 Laya 微调 JSONL")
    ap.add_argument("--vault")
    ap.add_argument("--out", default="", help="输出 .jsonl 路径")
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--include-pending", action="store_true",
                    help="包含未人审 pending（弱标签）")
    ap.add_argument("--local-as-share", action="store_true",
                    help="本地 is_candidate_shared=true 标为 share（默认 needs_human）")
    ap.add_argument("--no-seed", action="store_true", help="不合并种子样本")
    ap.add_argument("--seed-only", action="store_true", help="只写种子")
    ap.add_argument("--local-only", action="store_true", help="只扫本地 MD，不连 MySQL")
    args = ap.parse_args()

    root = vault_root(args.vault) if args.vault else vault_root()
    skill_refs = Path(__file__).resolve().parents[1] / "references" / "laya"
    seed_path = skill_refs / "dataset.sample.jsonl"
    out_path = Path(args.out) if args.out else (
        root / "_kb" / "laya_finetune" / "share_gate.jsonl"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    examples: list[dict] = []
    warnings: list[str] = []

    if not args.no_seed:
        for ex in load_jsonl(seed_path):
            meta = ex.setdefault("meta", {})
            meta.setdefault("source", "seed")
            examples.append(ex)

    if not args.seed_only:
        examples.extend(export_from_local_md(root, local_as_share=args.local_as_share))
        if not args.local_only:
            try:
                store = vault_store.open_default(root)
                examples.extend(export_from_mysql_main(store, limit=args.limit))
                examples.extend(export_from_mysql_pending(
                    store, limit=args.limit, include_pending=args.include_pending,
                ))
            except Exception as e:
                warnings.append("store export skipped: %s: %s" % (type(e).__name__, e))

    uniq = dedupe(examples)
    with out_path.open("w", encoding="utf-8") as f:
        for ex in uniq:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    report = {
        "out": str(out_path),
        "count": len(uniq),
        "vault": str(root),
        **summarize(uniq),
        "warnings": warnings,
        "hint": (
            "样本过少(<50)时仅够冒烟；请继续 vault_dump 决策/答案并人审后重跑。"
            if len(uniq) < 50 else "可送入上游 Laya RLCD notebook 微调。"
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
