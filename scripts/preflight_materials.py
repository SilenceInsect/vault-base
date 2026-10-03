#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""preflight_materials.py — 任务前置物料清单（YAML）init / missing / write-field。

Agent（Matt/grill 式）按 missing.questions 一次一问，write-field 落盘。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None

REQUIRED = [
    "skill_id",
    "task_goal",
    "scenario",
    "materials.acceptance_criteria",
    "known.author",
]

QUESTIONS = {
    "skill_id": "业务 skill_id？",
    "task_goal": "本会话任务目标（一句话）？",
    "scenario": "业务场景标签 scenario？",
    "context.recall_query": "用于金库召回的关键词？",
    "materials.acceptance_criteria": "验收标准是什么？",
    "materials.risk_notes": "主要风险与禁止项？",
    "known.author": "你的域账号或姓名？",
    "known.workspace": "工作区/仓库路径？",
    "sediment.default_share_scope": "默认沉淀范围？private 或 shared",
}


def _get(data: dict, dotted: str):
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set(data: dict, dotted: str, value):
    cur = data
    parts = dotted.split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _empty(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    if isinstance(v, (list, dict)) and len(v) == 0:
        return True
    return False


def skill_data_dir(skill_id: str, vault_base: str | Path = "") -> Path:
    """per-skill 数据目录：<vault-base>/<skill_id>/（顶层，单目录装全部数据）。

    preflight 清单、沉淀产物、适配账本统一落这里，业务 skill 目录保持零污染，
    卸载只需摘 SKILL.md 路由节；跨 IDE 同名 skill 共享同一份。
    """
    if vault_base:
        return Path(vault_base).expanduser() / skill_id
    try:
        from vault_paths import skill_root  # type: ignore
        return skill_root() / skill_id
    except Exception:
        return Path(__file__).resolve().parents[1] / skill_id


def vault_checklist_path(skill_id: str, vault_base: str | Path = "") -> Path:
    return skill_data_dir(skill_id, vault_base) / "preflight.yml"


def _vb_root(vault_base: str | Path = "") -> Path:
    if vault_base:
        return Path(vault_base).expanduser()
    try:
        from vault_paths import skill_root  # type: ignore
        return skill_root()
    except Exception:
        return Path(__file__).resolve().parents[1]


def interim_checklist_path(skill_id: str, vault_base: str | Path = "") -> Path:
    """过渡布局（references/vault-integration/preflight/），仅兼容读。"""
    return (_vb_root(vault_base) / "references" / "vault-integration"
            / "preflight" / ("%s.preflight.yml" % skill_id))


def legacy_checklist_path(skill_dir: Path, skill_id: str) -> Path:
    """v1 落点（注入到业务 skill 内部），仅兼容读。"""
    return (
        skill_dir / "references" / "vault-integration" / "preflight"
        / ("%s.preflight.yml" % skill_id)
    )


def checklist_path(skill_dir: Path, skill_id: str, vault_base: str | Path = "") -> Path:
    """解析清单路径：新根 → 过渡布局 → v1 legacy → 新根（新写落新根）。"""
    vp = vault_checklist_path(skill_id, vault_base)
    if vp.is_file():
        return vp
    ip = interim_checklist_path(skill_id, vault_base)
    if ip.is_file():
        return ip
    lp = legacy_checklist_path(skill_dir, skill_id)
    if lp.is_file():
        return lp
    return vp


def checklist_location(skill_dir: Path, skill_id: str, path: Path,
                       vault_base: str | Path = "") -> str:
    """给输出用的落点标签：vault / interim / legacy。"""
    if path == legacy_checklist_path(skill_dir, skill_id):
        return "legacy"
    if path == interim_checklist_path(skill_id, vault_base):
        return "interim"
    return "vault"


def template_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "skill-adapt" / (
        "preflight-checklist.template.yml"
    )


def load(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(text) or {}
    # 极简
    data: dict = {}
    stack: list = [(-1, data)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if ":" not in raw:
            continue
        key, val = raw.strip().split(":", 1)
        key, val = key.strip(), val.strip().strip('"').strip("'")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if val in ("", "|", "[]"):
            child: dict = {}
            parent[key] = [] if val == "[]" else child
            if val != "[]":
                stack.append((indent, child))
        elif val in ("true", "false"):
            parent[key] = val == "true"
        else:
            try:
                parent[key] = int(val)
            except ValueError:
                parent[key] = val
    return data


def dump(data: dict, path: Path) -> None:
    if yaml is None:
        raise RuntimeError("需要 PyYAML：pip install pyyaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def missing_fields(data: dict) -> list[str]:
    return [k for k in REQUIRED if _empty(_get(data, k))]


def autofill(data: dict, skill_dir: Path, skill_id: str) -> list[str]:
    filled = []
    if _empty(_get(data, "skill_id")):
        _set(data, "skill_id", skill_id)
        filled.append("skill_id")
    if _empty(_get(data, "context.vault_base_root")):
        _set(
            data,
            "context.vault_base_root",
            str((Path.home() / "common-skills-repo" / "vault-base").as_posix()),
        )
        filled.append("context.vault_base_root")
    if _empty(_get(data, "known.author")):
        _set(data, "known.author", os.environ.get("USERNAME") or os.environ.get("USER") or "")
        if not _empty(_get(data, "known.author")):
            filled.append("known.author")
    if _empty(_get(data, "known.workspace")):
        _set(data, "known.workspace", str(skill_dir.parent))
        filled.append("known.workspace")
    brief = (
        Path.home() / "common-skills-repo" / "vault-base" / "references" / "vault"
        / "briefs" / ("%s.md" % skill_id)
    )
    if brief.is_file() and _empty(_get(data, "known.local_brief_path")):
        _set(data, "known.local_brief_path", str(brief.as_posix()))
        filled.append("known.local_brief_path")
    if _empty(_get(data, "context.recall_query")):
        _set(data, "context.recall_query", skill_id)
        filled.append("context.recall_query")
    today = datetime.date.today().isoformat()
    if _empty(_get(data, "created_at")):
        _set(data, "created_at", today)
        filled.append("created_at")
    _set(data, "updated_at", today)
    return filled


def cmd_init(args) -> int:
    skill_dir = Path(args.skill_dir).expanduser().resolve()
    skill_id = args.skill_id
    dest = checklist_path(skill_dir, skill_id)
    loc = checklist_location(skill_dir, skill_id, dest)
    if dest.exists() and not args.force:
        print("exists:", dest)
        return 0
    tpl = template_path()
    text = tpl.read_text(encoding="utf-8")
    text = text.replace('skill_id: ""', 'skill_id: "%s"' % skill_id, 1)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    data = load(dest)
    filled = autofill(data, skill_dir, skill_id)
    if yaml is not None:
        miss = missing_fields(data)
        _set(data, "interview.missing_fields", miss)
        _set(data, "interview.status", "in_progress" if miss else "filled")
        dump(data, dest)
    print(json.dumps({
        "ok": True, "path": str(dest), "location": loc, "autofilled": filled,
        "missing": missing_fields(data) if yaml else [],
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_missing(args) -> int:
    skill_dir = Path(args.skill_dir).expanduser().resolve()
    path = checklist_path(skill_dir, args.skill_id)
    if not path.exists():
        print(json.dumps({"ok": False, "error": "run init first", "path": str(path)},
                         ensure_ascii=False))
        return 2
    data = load(path)
    autofill(data, skill_dir, args.skill_id)
    miss = missing_fields(data)
    out = {
        "ok": True,
        "path": str(path),
        "location": checklist_location(skill_dir, args.skill_id, path),
        "missing": miss,
        "questions": [
            {"field": f, "ask": QUESTIONS.get(f, "请提供 %s" % f)} for f in miss
        ],
        "interview_hint": (
            "Agent 必须一次只问 questions 中的一项（Matt/grill 式），"
            "用户回答后 preflight_materials.py write-field"
        ),
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("清单:", path)
        if not miss:
            print("必填已齐。可执行 pre_task_recall / 开始任务。")
        else:
            for i, q in enumerate(out["questions"], 1):
                print("%d. [%s] %s" % (i, q["field"], q["ask"]))
    return 0 if not miss else 1


def cmd_write_field(args) -> int:
    if yaml is None:
        print("需要 PyYAML", file=sys.stderr)
        return 2
    skill_dir = Path(args.skill_dir).expanduser().resolve()
    path = checklist_path(skill_dir, args.skill_id)
    data = load(path)
    val = args.value
    if val.lower() in ("true", "false"):
        val = val.lower() == "true"
    elif re.fullmatch(r"\d+", val):
        val = int(val)
    _set(data, args.field, val)
    miss = missing_fields(data)
    _set(data, "interview.missing_fields", miss)
    _set(data, "interview.status", "filled" if not miss else "in_progress")
    _set(data, "interview.updated_at", datetime.date.today().isoformat())
    _set(data, "updated_at", datetime.date.today().isoformat())
    dump(data, path)
    print(json.dumps({
        "ok": True, "field": args.field, "value": val,
        "missing_count": len(miss), "path": str(path),
        "location": checklist_location(skill_dir, args.skill_id, path),
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="前置物料清单")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("--skill-dir", required=True)
        p.add_argument("--skill-id", required=True)

    p = sub.add_parser("init")
    add_common(p)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("missing")
    add_common(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_missing)

    p = sub.add_parser("write-field")
    add_common(p)
    p.add_argument("--field", required=True)
    p.add_argument("--value", required=True)
    p.set_defaults(func=cmd_write_field)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
