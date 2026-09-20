#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""brief_bootstrap.py — 读 skill 内容，推断任务所需的信息要素，生成清单样例。"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import uuid as uuidlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import ensure_dirs, skill_root, vault_root                                    # noqa: E402

TZ = timezone(timedelta(hours=8))
FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*", re.S)


def now_iso() -> str:
    return datetime.now(TZ).replace(microsecond=0).isoformat()


def today() -> str:
    return datetime.now(TZ).date().isoformat()


def parse_skill_md(skill_dir: Path) -> dict:
    p = skill_dir / "SKILL.md"
    if not p.exists():
        return {"name": skill_dir.name, "description": "", "body": ""}
    text = p.read_text(encoding="utf-8")
    fm = {}
    m = FRONT_RE.match(text)
    body = text
    if m:
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"').strip("'")
        body = text[m.end():]
    return {
        "name": fm.get("name", skill_dir.name),
        "description": fm.get("description", ""),
        "body": body.strip(),
    }


def extract_argparse_fields(script_path: Path) -> list[dict]:
    """从 scripts/*.py 的 argparse 调用中提取参数要素。"""
    try:
        tree = ast.parse(script_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    fields = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_add = (
            isinstance(func, ast.Attribute) and func.attr == "add_argument"
        ) or (isinstance(func, ast.Name) and func.id == "add_argument")
        if not is_add or not node.args:
            continue
        arg_name = None
        if isinstance(node.args[0], ast.Constant):
            arg_name = str(node.args[0].value).lstrip("-").replace("-", "_")
        if not arg_name:
            continue
        required = False
        default = None
        help_text = ""
        for kw in node.keywords:
            if kw.arg == "required" and isinstance(kw.value, ast.Constant):
                required = bool(kw.value.value)
            elif kw.arg == "default" and isinstance(kw.value, ast.Constant):
                default = kw.value.value
            elif kw.arg == "help" and isinstance(kw.value, ast.Constant):
                help_text = str(kw.value.value)
        necessity = "必须" if required else "可选"
        acquire = "问用户"
        if default is not None:
            acquire = "默认值：%s" % default
            necessity = "可选"
        blocking = any(k in help_text for k in ("版本", "分支", "口径")) and required
        fields.append({
            "name": arg_name,
            "necessity": necessity,
            "acquire": acquire,
            "impact": "阻塞性缺失" if blocking else "影响输出质量",
            "blocking": blocking,
        })
    return fields


def collect_elements(skill_dir: Path) -> list[dict]:
    skill = parse_skill_md(skill_dir)
    elements = [{
        "name": "任务目标",
        "necessity": "必须",
        "acquire": "问用户 / 读 SKILL.md description",
        "impact": "阻塞性缺失",
        "blocking": True,
    }]
    if skill["description"]:
        elements.append({
            "name": "skill 描述上下文",
            "necessity": "可选",
            "acquire": "自动（SKILL.md）",
            "impact": "帮助对齐范围",
            "blocking": False,
        })

    scripts = skill_dir / "scripts"
    seen = set()
    if scripts.is_dir():
        for py in sorted(scripts.glob("*.py")):
            for f in extract_argparse_fields(py):
                if f["name"] in seen:
                    continue
                seen.add(f["name"])
                elements.append(f)

    refs = skill_dir / "references"
    if refs.is_dir() and any(refs.glob("*.md")):
        elements.append({
            "name": "references 规范文档",
            "necessity": "可选",
            "acquire": "自动读取 references/*.md",
            "impact": "补充领域约束",
            "blocking": False,
        })

    blocking_count = sum(1 for e in elements if e.get("blocking"))
    if blocking_count < 2:
        elements.append({
            "name": "验收标准",
            "necessity": "必须",
            "acquire": "问用户",
            "impact": "阻塞性缺失",
            "blocking": True,
        })
    return elements


def load_brief_template() -> str:
    tpl_path = skill_root() / "assets" / "brief-template.md"
    if tpl_path.exists():
        return tpl_path.read_text(encoding="utf-8")
    return ""


def render_brief(skill_id: str, title: str, elements: list[dict],
                 sample_idx: int = 1) -> str:
    tpl = load_brief_template()
    ts = now_iso()
    doc_uuid = str(uuidlib.uuid4())
    rows = "\n".join(
        "| %s | %s | %s | %s |" % (
            e["name"], e["necessity"], e["acquire"], e["impact"],
        ) for e in elements
    )
    if tpl:
        text = tpl.format(
            uuid=doc_uuid, skill_id=skill_id, title=title,
            author="bootstrap", created_at=ts, updated_at=ts, date=today(),
        )
        text = text.replace("| （待填） | 必须 | 问用户 | （待填） |", rows, 1)
        return text

    return """---
schema_version: 1
doc_type: brief
uuid: "%s"
skill_id: "%s"
title: "%s"
brief_rev: 1
source: "bootstrap"
author: "bootstrap"
created_at: "%s"
updated_at: "%s"
is_candidate_shared: false
tags: []
content_hash: ""
---
# %s

## 目标与产出
- 目标：%s
- 交付物：（待填）
- 验收标准：（待填）

## 必要信息要素
| 要素 | 必要性 | 获取方式 | 缺失影响 |
|---|---|---|---|
%s

## 迭代记录
| rev | 日期 | 变更 |
|---|---|---|
| 1 | %s | 初始版本（brief_bootstrap sample-%02d） |
""" % (doc_uuid, skill_id, title, ts, ts, title, title, rows, today(), sample_idx)


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 skill 任务清单样例")
    ap.add_argument("--skill-dir", required=True, help="业务 skill 目录")
    ap.add_argument("--skill-id", help="skill id（默认从 SKILL.md name 字段）")
    ap.add_argument("--samples", type=int, default=1, help="生成样例数量")
    ap.add_argument("--write", action="store_true", help="写入 briefs/_samples/")
    ap.add_argument("--promote", action="store_true",
                    help="同时写入 briefs/{skill_id}.md")
    ap.add_argument("--vault", help="Vault 根目录")
    args = ap.parse_args()

    skill_dir = Path(args.skill_dir).expanduser().resolve()
    skill = parse_skill_md(skill_dir)
    skill_id = args.skill_id or skill["name"]
    title = "%s 任务清单" % skill_id
    elements = collect_elements(skill_dir)
    root = ensure_dirs(vault_root(args.vault))

    samples = []
    for i in range(1, max(1, args.samples) + 1):
        content = render_brief(skill_id, title, elements, sample_idx=i)
        out_path = root / "briefs" / "_samples" / (
            "%s.sample-%02d.md" % (skill_id, i))
        item = {"path": str(out_path), "skill_id": skill_id, "index": i}
        if args.write or args.promote:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content, encoding="utf-8")
            item["written"] = True
        else:
            item["written"] = False
            item["preview"] = content[:500]
        samples.append(item)

    if args.promote:
        promote_path = root / "briefs" / ("%s.md" % skill_id)
        promote_path.write_text(render_brief(skill_id, title, elements), encoding="utf-8")
        samples.append({"path": str(promote_path), "promoted": True})

    blocking = sum(1 for e in elements if e.get("blocking"))
    report = {
        "skill_id": skill_id,
        "elements": len(elements),
        "blocking_elements": blocking,
        "quality_ok": blocking >= 2,
        "samples": samples,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
