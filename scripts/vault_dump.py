#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_dump.py — 任务结束生成标准化 MD 写入本地 Vault。

准入分三道闸（见 references/quality-gate.md）：字段闸、内容闸、语义闸。
本脚本负责前两道：`--strict` 下不达标直接拒写，非 strict 只给 warning。
字段闸不通过时**宁可不写** —— 补一条垃圾的代价远高于少写一条。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid as uuidlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import ensure_dirs, local_ip, vault_root          # noqa: E402
from vault_redact import scan as redact_scan                        # noqa: E402
from vault_share_scope import evaluate_share                         # noqa: E402
from vault_archive import is_archived_path, iter_live_docs          # noqa: E402

SCHEMA_VERSION = 1
TZ = timezone(timedelta(hours=8))
DOC_SUBDIR = {"answer": "answers", "decision": "decisions", "reference": "references"}
# 注：doc_type=brief 不走本脚本。清单是「活文档」，由 brief_bootstrap / brief_interview
# 生成后直接写 briefs/{skill_id}.md（语义化命名，见 3.6.3），模板见 3.6.2。

KEY_MAX = 64
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")
MIN_ANSWER = 80          # 低于此长度基本是「做了啥」而非「为什么」
MAX_ANSWER = 2000        # 超过多半混了过程日志，应拆条
MIN_TITLE = 8
# 时间漂移词：写进金库半年后就是错的表述
DRIFT_WORDS = ("今天", "目前", "最新", "暂时", "先这样", "这次", "刚刚", "昨晚", "现在")

MD_TEMPLATE = """---
schema_version: {schema_version}
uuid: "{doc_uuid}"
skill_id: "{skill_id}"
doc_type: "{doc_type}"
title: "{title}"
author: "{author}"
owner: "{owner}"
share_scope: "{share_scope}"
author_ip: "{author_ip}"
created_at: "{created_at}"
updated_at: "{updated_at}"
is_candidate_shared: {is_candidate_shared}
share_rev: {share_rev}
tags: {tags}
source_task_id: "{source_task_id}"
source_ref: "{source_ref}"
claim_key: "{claim_key}"
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


def _read_frontmatter(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    fm: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm


def scan_keys(root: Path, limit: int = 800) -> dict[str, list[str]]:
    """已占用的 claim_key → [uuid]。用于写入前撞车检测。忽略 _archive。"""
    out: dict[str, list[str]] = {}
    n = 0
    for p in iter_live_docs(root, ("answers", "decisions")):
        n += 1
        if n > limit:
            return out
        if is_archived_path(p):
            continue
        fm = _read_frontmatter(p)
        key = fm.get("claim_key")
        if key:
            out.setdefault(key, []).append(fm.get("uuid") or p.stem)
    return out


def scan_hashes(root: Path, limit: int = 800) -> dict[str, list[dict]]:
    """content_hash → [{uuid, path, claim_key}]。精确正文幂等检测。"""
    out: dict[str, list[dict]] = {}
    n = 0
    for p in iter_live_docs(root, ("answers", "decisions")):
        n += 1
        if n > limit:
            return out
        fm = _read_frontmatter(p)
        h = fm.get("content_hash") or ""
        if not h:
            continue
        out.setdefault(h, []).append({
            "uuid": fm.get("uuid") or p.stem,
            "path": str(p),
            "claim_key": fm.get("claim_key") or "",
            "title": fm.get("title") or "",
        })
    return out


def gate_check(
    *,
    title: str,
    question: str,
    answer: str,
    basis: str,
    claim_key: str,
    source_ref: str,
    doc_type: str = "answer",
    strict: bool = False,
    root: Path | None = None,
    share_scope: str = "private",
    owner: str = "",
    is_candidate_shared: bool = False,
    remark: str = "",
) -> dict:
    """字段闸 + 内容闸的机器可判部分。

    返回 {"ok", "errors", "warnings"}：errors 在 strict 下拒写；warnings 只提示。
    语义闸（Laya）不在这里 —— 它决定 share/private，不决定写不写。
    本机环境绑定与 --candidate-shared 互斥：strict 下试图共享盘符路径会进 errors。
    """
    errors: list[str] = []
    warnings: list[str] = []

    # —— 字段闸 ——
    key = (claim_key or "").strip()
    if not key:
        errors.append("缺 claim_key（去重锚点，格式 {skill_id}/{kebab}，见 quality-gate.md）")
    elif len(key) > KEY_MAX:
        errors.append("claim_key 超长 %d > %d" % (len(key), KEY_MAX))
    elif not KEY_RE.match(key):
        errors.append("claim_key 字符集不合法（需小写 a-z0-9 . _ / -）: %s" % key)
    elif not key.split("/", 1)[0]:
        errors.append("claim_key 缺少 skill 前缀: %s" % key)

    if not (source_ref or "").strip():
        errors.append("缺 source_ref（依据必须可追溯：文件路径 / 对话出处 / 报错原文）")

    if doc_type not in DOC_SUBDIR:
        errors.append("doc_type 只能是 %s" % sorted(DOC_SUBDIR))

    scope = (share_scope or "private").strip().lower()
    if scope and scope not in ("private", "team"):
        errors.append("share_scope 只能是 private|team，当前=%s" % share_scope)

    # —— 内容闸（可机器判的部分）——
    t = (title or "").strip()
    if len(t) < MIN_TITLE:
        errors.append("标题过短（%d < %d），应为一句可反驳的陈述" % (len(t), MIN_TITLE))
    if t.endswith(("?", "？")):
        warnings.append("标题是问句；建议改成结论式陈述，便于直接当判据引用")

    ans = (answer or "").strip()
    if len(ans) < MIN_ANSWER:
        errors.append("答案过短（%d < %d 字符），多半只写了「做了什么」没写「为什么」"
                      % (len(ans), MIN_ANSWER))
    if len(ans) > MAX_ANSWER:
        warnings.append("答案 %d 字符 > %d，可能混入过程日志，考虑拆条" % (len(ans), MAX_ANSWER))

    if not (question or "").strip():
        errors.append("缺问题段：入库条目必须能被复述成一个问题")
    if not (basis or "").strip():
        errors.append("缺决策依据段：没有依据的判断不是知识，是意见")

    hit = [w for w in DRIFT_WORDS if w in (ans + basis)]
    if hit:
        warnings.append("含时间漂移词 %s，建议改成绝对日期（否则半年后这条会变成错的）"
                        % "、".join(hit))

    # 本机环境 / 敏感 ↔ 共享互斥
    share_eval = evaluate_share(
        text="\n".join([title, question, answer, basis, remark, owner and ("owner: " + owner)]),
        share_scope=scope or "private",
        owner=owner,
        is_candidate_shared=is_candidate_shared,
        strict=strict,
    )
    warnings.extend(share_eval.get("warnings") or [])
    errors.extend(share_eval.get("errors") or [])

    # claim_key 撞车：无论是否 strict，都进 errors（去重不变量）
    # dump_md 会将其视为硬拦截；缺 key 本身仍只在 strict 拒写
    if root is not None and key and KEY_RE.match(key) and len(key) <= KEY_MAX:
        used = scan_keys(root).get(key) or []
        if used:
            msg = ("claim_key 已被占用（%s）：同一判断请走 supersede/merge 更新原条目，"
                   "而不是新增重复条" % ",".join(u[:8] for u in used))
            errors.append(msg)

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "share_eval": share_eval,
    }


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
    claim_key: str = "",
    strict: bool = False,
    root: Path | None = None,
    share_scope: str = "private",
    owner: str = "",
) -> dict:
    """生成一份标准化 MD。返回 {path, uuid, content_hash, redacted}。

    strict=True 时前置跑 gate_check，有 error 直接返回 {"error": "quality_gate_failed"}
    且**不落盘**；非 strict 只把 warnings 带在返回值里，照常写入。
    本机盘符路径等命中后强制 is_candidate_shared=false，即使传了 --candidate-shared。
    """
    root = ensure_dirs(root or vault_root())
    share_scope = (share_scope or "private").strip().lower() or "private"
    owner = (owner or "").strip()

    gate = gate_check(
        title=title, question=question, answer=answer, basis=basis,
        claim_key=claim_key, source_ref=source_ref, doc_type=doc_type,
        strict=strict, root=root,
        share_scope=share_scope, owner=owner,
        is_candidate_shared=is_candidate_shared, remark=remark,
    )
    share_eval = gate.get("share_eval") or {}
    # 去重硬拦截：claim_key 撞车 —— 即使非 strict 也拒写
    dedupe_errs = [e for e in gate["errors"] if "claim_key 已被占用" in e]
    other_errs = [e for e in gate["errors"] if "claim_key 已被占用" not in e]
    if dedupe_errs:
        return {"error": "dedupe_blocked", "ok": False,
                "errors": dedupe_errs, "warnings": gate["warnings"],
                "hint": "同一 claim_key 已有条目：更新原条或把重复条标 merge:<key> 后 "
                        "vault_dedupe.py --apply 归档"}
    if strict and other_errs:
        return {"error": "quality_gate_failed", "ok": False,
                "errors": other_errs, "warnings": gate["warnings"],
                "hint": "补齐后重试；或去掉 --strict 落盘（不推荐，垃圾条目的清理成本更高）"}
    # 非 strict：其余字段问题降为 warning，照写；但去重硬拦已在上面处理
    if other_errs and not strict:
        gate = {
            "ok": True,
            "errors": [],
            "warnings": list(gate["warnings"]) + ["[non-strict] " + e for e in other_errs],
            "share_eval": share_eval,
        }

    doc_uuid = str(uuidlib.uuid4())
    if doc_type not in DOC_SUBDIR:
        raise ValueError("doc_type 只能是 %s" % sorted(DOC_SUBDIR))
    target = root / DOC_SUBDIR[doc_type]
    target.mkdir(parents=True, exist_ok=True)
    ts = now_iso()

    # 敏感 / 本机环境闸门：命中即强制私有，并在备注追加告警
    red = share_eval.get("redact") or redact_scan(
        "\n".join([title, question, answer, basis, remark])
    )
    note = remark
    is_candidate_shared = bool(share_eval.get("is_candidate_shared", False))
    share_scope = share_eval.get("share_scope") or "private"
    if red.hit:
        is_candidate_shared = False
        share_scope = "private"
        note = (remark + "\n\n" + red.as_note()).strip()
    elif share_eval.get("forced_private"):
        is_candidate_shared = False
        share_scope = "private"

    chash = content_hash(question, answer, basis, note)
    # 正文完全一致 → 幂等返回已有条，不落第二份
    existed = (scan_hashes(root).get(chash) or [])
    if existed:
        hit = existed[0]
        return {
            "ok": True, "deduped": True, "reason": "content_hash_match",
            "uuid": hit["uuid"], "path": hit["path"],
            "content_hash": chash,
            "claim_key": hit.get("claim_key") or (claim_key or "").strip(),
            "title": hit.get("title") or title,
            "gate": {"ok": gate["ok"], "warnings": gate["warnings"]},
            "hint": "正文 content_hash 已存在，未新建文件；若结论有更新请 supersede 原条",
        }
    text = MD_TEMPLATE.format(
        schema_version=SCHEMA_VERSION,
        doc_uuid=doc_uuid,
        skill_id=skill_id,
        doc_type=doc_type,
        title=title.replace('"', "'"),
        author=author,
        owner=owner,
        share_scope=share_scope,
        author_ip=local_ip(),
        created_at=ts,
        updated_at=ts,
        is_candidate_shared="true" if is_candidate_shared else "false",
        share_rev=0,
        tags=json.dumps(tags or [], ensure_ascii=False),
        source_task_id=source_task_id,
        source_ref=source_ref,
        claim_key=(claim_key or "").strip(),
        content_hash=chash,
        question=question,
        answer=answer,
        basis=basis,
        remark=note,
    )
    path = target / ("%s.md" % doc_uuid)
    path.write_text(text, encoding="utf-8")
    out = {"path": str(path), "uuid": doc_uuid,
           "content_hash": chash, "redacted": bool(getattr(red, "hit", False)),
           "skill_id": skill_id, "doc_type": doc_type,
           "title": title, "is_candidate_shared": is_candidate_shared,
           "share_scope": share_scope, "owner": owner,
           "claim_key": (claim_key or "").strip(),
           "gate": {"ok": gate["ok"], "warnings": gate["warnings"]}}
    if share_eval.get("forced_private"):
        out["forced_private"] = True
        out["share_block_categories"] = list(share_eval.get("categories") or [])
    if gate["warnings"]:
        out["gate_hint"] = "条目已写入，但存在质量告警（见 gate.warnings）"
    try:
        from vault_event_log import log_write
        ev = log_write(
            action="vault_dump",
            skill_id=skill_id,
            doc_uuid=doc_uuid,
            actor=author,
            title=title,
            shared=bool(is_candidate_shared),
            detail={"path": str(path), "doc_type": doc_type,
                    "redacted": bool(getattr(red, "hit", False)),
                    "share_scope": share_scope},
            root=root,
            print_notice=True,
        )
        out["chat_notice"] = ev.get("chat_notice")
        out["event_id"] = ev.get("event_id")
        out["event_mysql_ok"] = ev.get("mysql_ok")
    except Exception as e:
        out["event_log_error"] = "%s: %s" % (type(e).__name__, e)
    return out


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
    ap.add_argument("--claim-key", default="",
                    help="去重锚点，格式 {skill_id}/{kebab-slug}，≤64 字符")
    ap.add_argument("--strict", action="store_true",
                    help="准入闸不通过则拒写（缺 claim_key/source_ref、正文过短等）")
    ap.add_argument("--candidate-shared", action="store_true")
    ap.add_argument("--share-scope", default="private",
                    choices=["private", "team"],
                    help="可见性：private=本机/个人；team=团队可共享候选")
    ap.add_argument("--owner", default="",
                    help="本机私有条使用者（如 quanguanming）；盘符路径类建议必填")
    args = ap.parse_args()

    res = dump_md(
        skill_id=args.skill_id, doc_type=args.doc_type, author=args.author,
        title=args.title, question=args.question, answer=args.answer,
        basis=args.basis, remark=args.remark,
        tags=[t for t in args.tags.split(",") if t],
        source_task_id=args.task_id, source_ref=args.source_ref,
        is_candidate_shared=args.candidate_shared,
        claim_key=args.claim_key, strict=args.strict,
        root=vault_root(args.vault),
        share_scope=args.share_scope,
        owner=args.owner,
    )
    print(json.dumps(res, ensure_ascii=False))
    return 0 if not res.get("error") else 2


if __name__ == "__main__":
    sys.exit(main())
