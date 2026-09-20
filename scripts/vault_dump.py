#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_dump.py — 任务结束生成标准化 MD 写入本地 Vault。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid as uuidlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import ensure_dirs, local_ip, vault_root          # noqa: E402
from vault_redact import scan as redact_scan                        # noqa: E402

SCHEMA_VERSION = 1
TZ = timezone(timedelta(hours=8))
DOC_SUBDIR = {"answer": "answers", "decision": "decisions", "reference": "references"}
# 注：doc_type=brief 不走本脚本。清单是「活文档」，由 brief_bootstrap / brief_interview
# 生成后直接写 briefs/{skill_id}.md（语义化命名，见 3.6.3），模板见 3.6.2。

MD_TEMPLATE = """---
schema_version: {schema_version}
uuid: "{doc_uuid}"
skill_id: "{skill_id}"
doc_type: "{doc_type}"
title: "{title}"
author: "{author}"
author_ip: "{author_ip}"
created_at: "{created_at}"
updated_at: "{updated_at}"
is_candidate_shared: {is_candidate_shared}
share_rev: {share_rev}
tags: {tags}
source_task_id: "{source_task_id}"
source_ref: "{source_ref}"
content_hash: "{content_hash}"
---
# {title}

## 问题
{question}

## 答案&决策
{answer}

## 决策依据
{basis}

## 备注
{remark}
"""


def now_iso() -> str:
    return datetime.now(TZ).replace(microsecond=0).isoformat()


def content_hash(question: str, answer: str, basis: str, remark: str) -> str:
    """正文规范化后取 sha256 前 16 位，作为幂等键。"""
    norm = "\n".join(x.strip() for x in (question, answer, basis, remark) if x)
    norm = " ".join(norm.split())
    return "sha256:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def dump_md(
    skill_id: str,
    doc_type: str,
    author: str,
    title: str,
    question: str,
    answer: str,
    basis: str,
    remark: str = "",
    tags: list[str] | None = None,
    source_task_id: str = "",
    source_ref: str = "",
    is_candidate_shared: bool = False,
    root: Path | None = None,
) -> dict:
    """生成一份标准化 MD。返回 {path, uuid, content_hash, redacted}。"""
    if doc_type not in DOC_SUBDIR:
        raise ValueError("doc_type 只能是 %s" % sorted(DOC_SUBDIR))

    root = ensure_dirs(root or vault_root())
    doc_uuid = str(uuidlib.uuid4())
    target = root / DOC_SUBDIR[doc_type]
    target.mkdir(parents=True, exist_ok=True)
    ts = now_iso()

    # 敏感信息闸门：命中即强制私有，并在备注追加告警
    red = redact_scan("\n".join([title, question, answer, basis, remark]))
    note = remark
    if red.hit:
        is_candidate_shared = False
        note = (remark + "\n\n" + red.as_note()).strip()

    chash = content_hash(question, answer, basis, note)
    text = MD_TEMPLATE.format(
        schema_version=SCHEMA_VERSION,
        doc_uuid=doc_uuid,
        skill_id=skill_id,
        doc_type=doc_type,
        title=title.replace('"', "'"),
        author=author,
        author_ip=local_ip(),
        created_at=ts,
        updated_at=ts,
        is_candidate_shared="true" if is_candidate_shared else "false",
        share_rev=0,
        tags=json.dumps(tags or [], ensure_ascii=False),
        source_task_id=source_task_id,
        source_ref=source_ref,
        content_hash=chash,
        question=question,
        answer=answer,
        basis=basis,
        remark=note,
    )
    path = target / ("%s.md" % doc_uuid)
    path.write_text(text, encoding="utf-8")
    return {"path": str(path), "uuid": doc_uuid,
            "content_hash": chash, "redacted": red.hit}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault")
    ap.add_argument("--skill-id", required=True)
    ap.add_argument("--doc-type", default="answer", choices=sorted(DOC_SUBDIR))
    ap.add_argument("--author", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--question", default="")
    ap.add_argument("--answer", default="")
    ap.add_argument("--basis", default="")
    ap.add_argument("--remark", default="")
    ap.add_argument("--tags", default="")
    ap.add_argument("--task-id", default="")
    ap.add_argument("--source-ref", default="")
    ap.add_argument("--candidate-shared", action="store_true")
    args = ap.parse_args()

    res = dump_md(
        skill_id=args.skill_id, doc_type=args.doc_type, author=args.author,
        title=args.title, question=args.question, answer=args.answer,
        basis=args.basis, remark=args.remark,
        tags=[t for t in args.tags.split(",") if t],
        source_task_id=args.task_id, source_ref=args.source_ref,
        is_candidate_shared=args.candidate_shared, root=vault_root(args.vault),
    )
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
