#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_share_scope.py — 公共金库可见性 / 本机环境门禁。

原则：换人/换机后还成立的判断才可共享；绑死某使用者本机路径的一律 private。

供 vault_dump（落盘前强制降级）与 vault_store.approve（人审复检）共用。
"""
from __future__ import annotations

from typing import Any

from vault_redact import MACHINE_CATEGORIES, RedactResult, scan as redact_scan

SHARE_SCOPES = frozenset({"private", "team"})


def normalize_share_scope(value: str | None, *, candidate_shared: bool = False) -> str:
    v = (value or "").strip().lower()
    if v in SHARE_SCOPES:
        return v
    # 兼容旧调用：显式 candidate-shared 且未传 scope → 视为试图 team
    return "team" if candidate_shared else "private"


def scan_share_text(*parts: str) -> RedactResult:
    blob = "\n".join(p for p in parts if p)
    return redact_scan(blob)


def evaluate_share(
    *,
    text: str = "",
    share_scope: str | None = None,
    owner: str = "",
    is_candidate_shared: bool = False,
    strict: bool = False,
) -> dict[str, Any]:
    """判定能否作为共享候选。

    返回:
      ok: 字段层面是否允许继续（strict 下 team 缺字段会 false）
      is_candidate_shared: 校正后的共享标记（命中本机/敏感则 false）
      share_scope: 校正后的 scope
      owner: 原样或空
      forced_private: 是否被规则强制降级
      errors / warnings
      redact: RedactResult 摘要
    """
    errors: list[str] = []
    warnings: list[str] = []
    scope = normalize_share_scope(share_scope, candidate_shared=is_candidate_shared)
    want_share = bool(is_candidate_shared) or scope == "team"
    red = redact_scan(text or "")

    requested_share = bool(want_share)
    forced_private = False
    if red.hit:
        forced_private = True
        want_share = False
        scope = "private"
        if red.machine_bound:
            msg = (
                "本机环境绑定命中 %s：已强制 private，禁止 --candidate-shared / share_scope=team"
                % "、".join(c for c in red.categories if c in MACHINE_CATEGORIES)
            )
        else:
            msg = "敏感信息命中 %s：已强制 private" % "、".join(red.categories)
        if requested_share and strict:
            errors.append(
                "正文命中禁止共享类别 %s，不能以 share_scope=team / --candidate-shared 入库"
                % "、".join(red.categories)
            )
        else:
            warnings.append(msg)

    if want_share and scope != "team":
        # 显式要共享但 scope 不是 team
        if strict:
            errors.append(
                "共享候选要求 share_scope=team（当前=%s）；本机路径类请用 share_scope=private"
                % (share_scope or "private")
            )
        else:
            warnings.append(
                "is_candidate_shared=true 但 share_scope!=team，已降级为 private"
            )
            want_share = False
            scope = "private"
            forced_private = True

    if scope == "private" and want_share:
        want_share = False
        forced_private = True

    if scope == "private" and (owner or "").strip() == "" and red.machine_bound:
        warnings.append("本机环境条目建议填写 owner=<使用者>，避免他人误用为公共默认路径")

    return {
        "ok": not errors,
        "is_candidate_shared": bool(want_share),
        "share_scope": scope,
        "owner": (owner or "").strip(),
        "forced_private": forced_private,
        "errors": errors,
        "warnings": warnings,
        "redact": red,
        "categories": list(red.categories),
    }


def approve_blocked_reason(title: str = "", body: str = "", **meta: Any) -> str | None:
    """人审 approve 复检：命中则返回拒批原因，否则 None。"""
    parts = [title or "", body or ""]
    for key in ("segments", "tags", "source_ref", "claim_key"):
        val = meta.get(key)
        if isinstance(val, dict):
            parts.extend(str(v) for v in val.values())
        elif isinstance(val, (list, tuple)):
            parts.extend(str(v) for v in val)
        elif val:
            parts.append(str(val))
    scope = meta.get("share_scope")
    if scope:
        parts.append("share_scope: %s" % scope)
    owner = meta.get("owner")
    if owner:
        parts.append("owner: %s" % owner)
    red = redact_scan("\n".join(parts))
    if not red.hit:
        return None
    return (
        "share_gate_blocked: 命中 %s，禁止写入 shared_vault_main（本机环境/敏感信息）"
        % "、".join(red.categories)
    )
