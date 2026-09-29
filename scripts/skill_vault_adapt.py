#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""skill_vault_adapt.py — 扫描本机 skill，按标准结构注入 vault-base 共享链路。

写入（幂等，--force 覆盖 hooks/rules/taxonomy）：
  <skill>/references/vault-integration/
    knowledge-taxonomy.yml
    rules/vault-share-protocol.md
    preflight/<skill_id>.preflight.yml   （via preflight_materials init）
  <skill>/hooks/pre_task_recall.py
  <skill>/hooks/post_task_sediment.py
  <skill>/SKILL.md 追加「知识金库接入」节（若尚无）
  <skill>/SKILL.md frontmatter 补 vault_base / accepts_brief（谨慎合并）

用法：
  python skill_vault_adapt.py scan --json
  python skill_vault_adapt.py adapt --skill-id req-code-consistency
  python skill_vault_adapt.py adapt --all
  python skill_vault_adapt.py status --skill-id ...
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common_skills_repo import IDE_SKILLS, detect_ide_links  # noqa: E402
from vault_paths import skill_root as vb_skill_root  # noqa: E402

FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)
SECTION_MARK = "## 知识金库接入（vault-base 适配）"
SKIP_NAMES = {
    "vault-base", "skill-orchestrator", "create-rule", "create-skill",
    "update-cursor-settings", "create-hook",
}


def adapt_assets() -> Path:
    return vb_skill_root() / "assets" / "skill-adapt"


def discover_skills() -> list[dict]:
    """扫描本机已安装 IDE skills + common-skills-repo。"""
    found: dict[str, dict] = {}
    # common repo
    common = Path.home() / "common-skills-repo"
    env = os.environ.get("COMMON_SKILLS_REPO")
    if env:
        common = Path(env).expanduser()
    if common.is_dir():
        for p in common.iterdir():
            if p.is_dir() and (p / "SKILL.md").is_file():
                found[p.name] = {
                    "skill_id": p.name,
                    "path": str(p.resolve()),
                    "source": "common-skills-repo",
                }
    # IDE homes（不 resolve junction，保留展示路径）
    links = detect_ide_links()
    for ide, on in links.items():
        if not on:
            continue
        root = IDE_SKILLS.get(ide)
        if root is None:
            continue
        # workbuddy 兼容
        if ide == "workbuddy":
            for cand in (Path.home() / ".workbuddy" / "skills",
                         Path.home() / ".WorkBuddy" / "skills"):
                if cand.is_dir():
                    root = cand
                    break
        if not root.is_dir():
            continue
        try:
            for p in root.iterdir():
                if not p.is_dir() or not (p / "SKILL.md").is_file():
                    continue
                sid = p.name
                if sid in found:
                    found[sid].setdefault("ide_links", []).append(ide)
                else:
                    found[sid] = {
                        "skill_id": sid,
                        "path": str(p),
                        "source": "ide:%s" % ide,
                        "ide_links": [ide],
                    }
        except OSError:
            continue
    return sorted(found.values(), key=lambda x: x["skill_id"])


def _copy_file(src: Path, dest: Path, force: bool) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        return "skip"
    shutil.copy2(src, dest)
    return "wrote"


def _ensure_frontmatter(skill_md: Path, vault_base_path: str) -> str:
    text = skill_md.read_text(encoding="utf-8")
    m = FRONT_RE.match(text)
    if not m:
        fm = (
            "---\n"
            "name: %s\n"
            "description: \"\"\n"
            "accepts_brief: true\n"
            "vault_base: \"%s\"\n"
            "---\n\n"
        ) % (skill_md.parent.name, vault_base_path.replace("\\", "/"))
        skill_md.write_text(fm + text, encoding="utf-8")
        return "frontmatter_created"
    body = text[m.end():]
    raw = m.group(1)
    changed = False
    if re.search(r"(?m)^accepts_brief\s*:", raw) is None:
        raw = raw.rstrip() + "\naccepts_brief: true\n"
        changed = True
    if re.search(r"(?m)^vault_base\s*:", raw) is None:
        raw = raw.rstrip() + '\nvault_base: "%s"\n' % vault_base_path.replace("\\", "/")
        changed = True
    if not changed:
        return "frontmatter_ok"
    skill_md.write_text("---\n%s\n---\n%s" % (raw.rstrip() + "\n", body.lstrip("\n")),
                        encoding="utf-8")
    return "frontmatter_updated"


def _ensure_section(skill_md: Path) -> str:
    text = skill_md.read_text(encoding="utf-8")
    if SECTION_MARK in text:
        return "section_ok"
    frag = (adapt_assets() / "SKILL.vault-section.md").read_text(encoding="utf-8")
    skill_md.write_text(text.rstrip() + "\n\n" + frag.strip() + "\n", encoding="utf-8")
    return "section_appended"


def adapt_one(skill_path: Path, skill_id: str, *, force: bool = False) -> dict:
    assets = adapt_assets()
    vb = str(vb_skill_root().as_posix())
    actions = []
    integ = skill_path / "references" / "vault-integration"
    integ.mkdir(parents=True, exist_ok=True)

    actions.append(("taxonomy", _copy_file(
        assets / "knowledge-taxonomy.yml",
        integ / "knowledge-taxonomy.yml", force,
    )))
    actions.append(("rules", _copy_file(
        assets / "rules" / "vault-share-protocol.md",
        integ / "rules" / "vault-share-protocol.md", force,
    )))
    actions.append(("hook_pre", _copy_file(
        assets / "hooks" / "pre_task_recall.py",
        skill_path / "hooks" / "pre_task_recall.py", force,
    )))
    actions.append(("hook_post", _copy_file(
        assets / "hooks" / "post_task_sediment.py",
        skill_path / "hooks" / "post_task_sediment.py", force,
    )))

    # preflight init
    from preflight_materials import autofill, checklist_path, dump, load, template_path
    try:
        import yaml  # noqa: F401
        has_yaml = True
    except ImportError:
        has_yaml = False

    dest = checklist_path(skill_path, skill_id)
    if not dest.exists() or force:
        text = template_path().read_text(encoding="utf-8")
        text = text.replace('skill_id: ""', 'skill_id: "%s"' % skill_id, 1)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        if has_yaml:
            data = load(dest)
            autofill(data, skill_path, skill_id)
            dump(data, dest)
        actions.append(("preflight", "wrote"))
    else:
        actions.append(("preflight", "skip"))

    skill_md = skill_path / "SKILL.md"
    if skill_md.is_file():
        actions.append(("frontmatter", _ensure_frontmatter(skill_md, vb)))
        actions.append(("skill_section", _ensure_section(skill_md)))

    marker = integ / "ADAPTED.json"
    marker.write_text(json.dumps({
        "skill_id": skill_id,
        "vault_base": vb,
        "actions": actions,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "skill_id": skill_id,
        "path": str(skill_path),
        "actions": actions,
        "integration": str(integ),
        "next": [
            "python hooks/pre_task_recall.py --skill-dir \"%s\" --skill-id %s"
            % (skill_path, skill_id),
            "python \"%s/scripts/preflight_materials.py\" missing --skill-dir \"%s\" "
            "--skill-id %s --json" % (vb, skill_path, skill_id),
        ],
    }


def cmd_scan(args) -> int:
    skills = discover_skills()
    out = []
    for s in skills:
        p = Path(s["path"])
        adapted = (p / "references" / "vault-integration" / "ADAPTED.json").is_file()
        out.append({**s, "adapted": adapted, "skip_suggested": s["skill_id"] in SKIP_NAMES})
    if args.json:
        print(json.dumps({"count": len(out), "skills": out}, ensure_ascii=False, indent=2))
    else:
        for s in out:
            flag = "ADAPTED" if s["adapted"] else "plain"
            print("%-28s %s  %s" % (s["skill_id"], flag, s["path"]))
    return 0


def cmd_adapt(args) -> int:
    skills = discover_skills()
    by_id = {s["skill_id"]: s for s in skills}
    targets = []
    if args.all:
        targets = [s for s in skills if s["skill_id"] not in SKIP_NAMES]
    elif args.skill_id:
        if args.skill_id not in by_id:
            # 允许显式路径
            if args.skill_dir:
                targets = [{
                    "skill_id": args.skill_id,
                    "path": str(Path(args.skill_dir).expanduser().resolve()),
                }]
            else:
                print(json.dumps({"ok": False, "error": "skill not found",
                                  "skill_id": args.skill_id}, ensure_ascii=False))
                return 2
        else:
            targets = [by_id[args.skill_id]]
    elif args.skill_dir:
        p = Path(args.skill_dir).expanduser().resolve()
        targets = [{"skill_id": args.skill_id or p.name, "path": str(p)}]
    else:
        print("需要 --skill-id / --skill-dir / --all", file=sys.stderr)
        return 2

    results = []
    for t in targets:
        if t["skill_id"] in SKIP_NAMES and not args.include_infra:
            results.append({"ok": False, "skill_id": t["skill_id"], "skipped": True})
            continue
        try:
            results.append(adapt_one(
                Path(t["path"]), t["skill_id"], force=bool(args.force),
            ))
        except Exception as e:
            results.append({
                "ok": False, "skill_id": t["skill_id"],
                "error": "%s: %s" % (type(e).__name__, e),
            })
    print(json.dumps({"ok": True, "results": results}, ensure_ascii=False, indent=2))
    return 0


def cmd_status(args) -> int:
    skills = discover_skills()
    sid = args.skill_id
    hit = next((s for s in skills if s["skill_id"] == sid), None)
    if not hit and args.skill_dir:
        hit = {"skill_id": sid, "path": str(Path(args.skill_dir).expanduser().resolve())}
    if not hit:
        print(json.dumps({"ok": False, "error": "not found"}, ensure_ascii=False))
        return 2
    p = Path(hit["path"])
    integ = p / "references" / "vault-integration"
    report = {
        "skill_id": sid,
        "path": str(p),
        "adapted": (integ / "ADAPTED.json").is_file(),
        "has_taxonomy": (integ / "knowledge-taxonomy.yml").is_file(),
        "has_rules": (integ / "rules" / "vault-share-protocol.md").is_file(),
        "has_preflight": (
            integ / "preflight" / ("%s.preflight.yml" % sid)
        ).is_file(),
        "has_hooks": (p / "hooks" / "pre_task_recall.py").is_file()
        and (p / "hooks" / "post_task_sediment.py").is_file(),
        "skill_md_section": SECTION_MARK in (p / "SKILL.md").read_text(
            encoding="utf-8", errors="replace"
        ) if (p / "SKILL.md").is_file() else False,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["adapted"] else 1


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="扫描并适配业务 skill → vault-base 标准链路")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="列出本机 skill 及是否已适配")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("adapt", help="注入 hooks/rules/preflight/taxonomy")
    p.add_argument("--skill-id")
    p.add_argument("--skill-dir")
    p.add_argument("--all", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--include-infra", action="store_true",
                   help="连 vault-base 自身等基础设施 skill 也适配")
    p.set_defaults(func=cmd_adapt)

    p = sub.add_parser("status", help="查看单 skill 适配状态")
    p.add_argument("--skill-id", required=True)
    p.add_argument("--skill-dir")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
