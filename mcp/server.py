#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault-base 最小 MCP Server（验证版）。

Tools:
  - vault_recall     共享召回（cache → MySQL → 本地）
  - vault_dump       沉淀标准化 MD 到本地 Vault
  - vault_readiness  就绪门禁 R1–R8
  - vault_preflight  前置物料清单 init / missing / write-field

配置见 mcp/README.md 与 .cursor/mcp.json。
"""
from __future__ import annotations

import json
import os
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

# 保证可 import 同仓 scripts/
_SKILL_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _SKILL_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

try:
    from mcp.server.mcpserver import MCPServer as _Server  # mcp>=2
except ImportError:  # pragma: no cover — mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore

mcp = _Server(
    "vault-base",
    version="0.1.0",
    instructions=(
        "vault-base 知识金库最小工具面。"
        "任务开始前先 vault_readiness；召回用 vault_recall；"
        "前置物料用 vault_preflight；结案沉淀用 vault_dump。"
        "协议层（五步强制 / skill 适配）仍由 vault-base Skill 负责。"
    ),
)


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _default_author() -> str:
    return os.environ.get("VAULT_AUTHOR") or os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


def _default_skill_dir(skill_id: str = "") -> Path:
    """MCP 无业务 skill 目录时，落到 vault-base 根（清单仍按 skill_id 写 per-skill 目录）。"""
    return _SKILL_ROOT


@mcp.tool()
def vault_recall(
    skill_id: str,
    query: str = "",
    limit: int = 10,
    include_local: bool = True,
    use_query_cache: bool = True,
) -> str:
    """从共享金库叠读召回知识（prefixCache → MySQL → 本地 Vault）。

    Args:
        skill_id: 业务 skill id（必填）
        query: 检索关键词；空则按 skill 索引拉文档
        limit: 最大命中条数
        include_local: 是否叠读本地 Vault
        use_query_cache: 是否使用 query 短缓存
    """
    from vault_recall import recall  # type: ignore

    result = recall(
        skill_id,
        query,
        limit=max(1, min(int(limit), 50)),
        include_local=include_local,
        use_query_cache=use_query_cache,
    )
    return _json(result)


@mcp.tool()
def vault_dump(
    skill_id: str,
    title: str,
    question: str = "",
    answer: str = "",
    basis: str = "",
    remark: str = "",
    doc_type: str = "answer",
    author: str = "",
    tags: str = "",
    candidate_shared: bool = False,
    task_id: str = "",
    source_ref: str = "",
) -> str:
    """将问答/决策沉淀为本地 Vault 标准化 MD（敏感信息命中则强制私有）。

    Args:
        skill_id: 业务 skill id
        title: 文档标题
        question: 问题
        answer: 答案或决策
        basis: 决策依据
        remark: 备注
        doc_type: answer | decision | reference
        author: 作者（默认本机用户名 / VAULT_AUTHOR）
        tags: 逗号分隔标签
        candidate_shared: 是否候选共享（命中脱敏则强制 false）
        task_id: 来源任务 id
        source_ref: 来源引用
    """
    from vault_dump import dump_md  # type: ignore

    if doc_type not in ("answer", "decision", "reference"):
        return _json({"ok": False, "error": "doc_type must be answer|decision|reference"})

    res = dump_md(
        skill_id=skill_id,
        doc_type=doc_type,
        author=author or _default_author(),
        title=title,
        question=question,
        answer=answer,
        basis=basis,
        remark=remark,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        source_task_id=task_id,
        source_ref=source_ref,
        is_candidate_shared=bool(candidate_shared),
    )
    res["ok"] = True
    return _json(res)


@mcp.tool()
def vault_readiness(
    skill_id: str = "",
    skill_dir: str = "",
    fix: bool = False,
    quick: bool = False,
    require_shared: bool = False,
) -> str:
    """运行就绪门禁 R1–R8。有 BLOCKER 时禁止共享写入；看 capability.can_write_shared。

    Args:
        skill_id: 业务 skill id（R6/R8）；可空
        skill_dir: 业务 skill 目录（R5）；可空
        fix: 自动修复可修复项（占位 MD、git init 等）
        quick: 仅跑 R1–R4
        require_shared: R2/R4 失败升为 BLOCKER
    """
    from readiness_gate import run_gate  # type: ignore

    args = Namespace(
        vault=None,
        skill_dir=skill_dir or None,
        skill_id=skill_id or None,
        fix=fix,
        quick=quick,
        require_shared=require_shared,
    )
    try:
        report = run_gate(args)
        report["ok"] = bool(report.get("ready"))
        return _json(report)
    except Exception as e:
        return _json({"ok": False, "ready": False, "error": "%s: %s" % (type(e).__name__, e)})


@mcp.tool()
def vault_preflight(
    action: str,
    skill_id: str,
    skill_dir: str = "",
    field: str = "",
    value: str = "",
    force: bool = False,
) -> str:
    """前置物料清单：init 创建、missing 列出缺项、write-field 写入单项。

    Agent 应按 missing.questions 一次只问一项，再 write-field。

    Args:
        action: init | missing | write-field
        skill_id: 业务 skill id
        skill_dir: 业务 skill 目录；空则用 vault-base 根
        field: write-field 时的点分字段名（如 task_goal）
        value: write-field 时的值
        force: init 时覆盖已有清单
    """
    import preflight_materials as pm  # type: ignore

    action = (action or "").strip().lower().replace("_", "-")
    sdir = Path(skill_dir).expanduser().resolve() if skill_dir else _default_skill_dir(skill_id)

    if action == "init":
        args = Namespace(skill_dir=str(sdir), skill_id=skill_id, force=force)
        # cmd_init prints JSON; capture via rewrite
        dest = pm.checklist_path(sdir, skill_id)
        loc = pm.checklist_location(sdir, skill_id, dest)
        if dest.exists() and not force:
            data = pm.load(dest) if dest.is_file() else {}
            return _json({
                "ok": True,
                "path": str(dest),
                "location": loc,
                "exists": True,
                "missing": pm.missing_fields(data) if data else [],
            })
        tpl = pm.template_path()
        text = tpl.read_text(encoding="utf-8")
        text = text.replace('skill_id: ""', 'skill_id: "%s"' % skill_id, 1)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        data = pm.load(dest)
        filled = pm.autofill(data, sdir, skill_id)
        if pm.yaml is not None:
            miss = pm.missing_fields(data)
            pm._set(data, "interview.missing_fields", miss)
            pm._set(data, "interview.status", "in_progress" if miss else "filled")
            pm.dump(data, dest)
        return _json({
            "ok": True,
            "path": str(dest),
            "location": loc,
            "autofilled": filled,
            "missing": pm.missing_fields(data) if pm.yaml else [],
        })

    if action == "missing":
        path = pm.checklist_path(sdir, skill_id)
        if not path.exists():
            return _json({"ok": False, "error": "run init first", "path": str(path)})
        data = pm.load(path)
        pm.autofill(data, sdir, skill_id)
        miss = pm.missing_fields(data)
        return _json({
            "ok": True,
            "path": str(path),
            "location": pm.checklist_location(sdir, skill_id, path),
            "missing": miss,
            "questions": [
                {"field": f, "ask": pm.QUESTIONS.get(f, "请提供 %s" % f)} for f in miss
            ],
            "interview_hint": (
                "一次只问 questions 中的一项；用户回答后 vault_preflight "
                "action=write-field field=... value=..."
            ),
        })

    if action in ("write-field", "writefield"):
        if not field:
            return _json({"ok": False, "error": "field required for write-field"})
        if pm.yaml is None:
            return _json({"ok": False, "error": "需要 PyYAML：pip install pyyaml"})
        path = pm.checklist_path(sdir, skill_id)
        if not path.exists():
            return _json({"ok": False, "error": "run init first", "path": str(path)})
        data = pm.load(path)
        val: Any = value
        if isinstance(val, str) and val.lower() in ("true", "false"):
            val = val.lower() == "true"
        elif isinstance(val, str) and val.isdigit():
            val = int(val)
        pm._set(data, field, val)
        miss = pm.missing_fields(data)
        import datetime
        today = datetime.date.today().isoformat()
        pm._set(data, "interview.missing_fields", miss)
        pm._set(data, "interview.status", "filled" if not miss else "in_progress")
        pm._set(data, "interview.updated_at", today)
        pm._set(data, "updated_at", today)
        pm.dump(data, path)
        return _json({
            "ok": True,
            "field": field,
            "value": val,
            "missing_count": len(miss),
            "missing": miss,
            "path": str(path),
            "location": pm.checklist_location(sdir, skill_id, path),
        })

    return _json({
        "ok": False,
        "error": "action must be init|missing|write-field",
        "got": action,
    })


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
