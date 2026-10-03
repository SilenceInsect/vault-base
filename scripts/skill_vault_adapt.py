#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""skill_vault_adapt.py — 扫描本机 skill，单点注入 vault-base 路由节（零拷贝注入）。

v2 注入模型（中心化 / fast uninstall / 跨 IDE 知识单点汇总）：
  目标 skill 目录只被改一个文件 —— SKILL.md：
    - frontmatter 补 accepts_brief / vault_base（缺失才补）
    - 追加或替换 <!-- vault-base:begin ... vault-base:end --> 受管路由节
  hooks / taxonomy / rules **不再复制**进目标 skill；per-skill 数据统一落
  `<vault-base>/<skill_id>/`（顶层，单目录装全部数据；key = skill_id，与 IDE 无关——
  跨 IDE 使用同名 skill 时知识自动汇总到同一处）：
    preflight.yml   （任务前物料清单，全局一份）
    sediment/       （沉淀产物，卸载时保留）
    adapted.json    （适配账本，含 copies 多副本清单）

多副本规则：
  同名 skill 在多个 IDE 各有**实体**副本 → 每个副本的 SKILL.md 都注入路由节，
  共享同一份 vault 侧 preflight/sediment/账本；
  IDE 目录为**联接**（symlink/junction）→ resolve 到真相源，不重复注入。

升级路径：adapt 对已按 v1（文件铺进 skill 目录）适配过的 skill 自动迁移——
preflight/沉淀 移入 vault 侧；与模板一致的 hooks/taxonomy/rules 直接删除，
被改过的仅报告不删（--purge-legacy 同规则）。

用法：
  python skill_vault_adapt.py scan [--json]
  python skill_vault_adapt.py adapt --skill-id req-code-consistency
  python skill_vault_adapt.py adapt --all [--force]
  python skill_vault_adapt.py status --skill-id ...
  python skill_vault_adapt.py uninstall --skill-id ... [--purge-legacy] [--all]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import stat as _stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common_skills_repo import IDE_SKILLS, detect_ide_links  # noqa: E402
from vault_paths import skill_root as vb_skill_root  # noqa: E402

FRONT_RE = re.compile(r"^---[ \t]*\n(.*?)\n---[ \t]*\n", re.S)
MARK_BEGIN = "<!-- vault-base:begin (managed by skill_vault_adapt; do not edit) -->"
MARK_END = "<!-- vault-base:end -->"
MARK_RE = re.compile(
    re.escape(MARK_BEGIN) + r".*?" + re.escape(MARK_END) + r"\n?", re.S
)
LEGACY_SECTION_MARK = "## 知识金库接入（vault-base 适配）"
LEGACY_SECTION_RE = re.compile(
    r"^## 知识金库接入（vault-base 适配）.*?(?=^## |\Z)", re.S | re.M
)
SKIP_NAMES = {
    "vault-base", "skill-orchestrator", "create-rule", "create-skill",
    "update-cursor-settings", "create-hook",
}

# v1 注入到业务 skill 的文件 → 对应模板（内容一致才允许自动删除）
LEGACY_TEMPLATE_MAP = {
    "references/vault-integration/knowledge-taxonomy.yml": "knowledge-taxonomy.yml",
    "references/vault-integration/rules/vault-share-protocol.md": "rules/vault-share-protocol.md",
    "hooks/pre_task_recall.py": "hooks/pre_task_recall.py",
    "hooks/post_task_sediment.py": "hooks/post_task_sediment.py",
}


def adapt_assets() -> Path:
    return vb_skill_root() / "assets" / "skill-adapt"


def skill_data_dir(skill_id: str) -> Path:
    """per-skill 数据目录：<vault-base>/<skill_id>/（顶层，单目录装全部数据）。

    目录内固定三件：preflight.yml / sediment/ / adapted.json。
    """
    return vb_skill_root() / skill_id


def vault_preflight_path(skill_id: str) -> Path:
    return skill_data_dir(skill_id) / "preflight.yml"


def vault_record_path(skill_id: str) -> Path:
    return skill_data_dir(skill_id) / "adapted.json"


def vault_sediment_dir(skill_id: str) -> Path:
    return skill_data_dir(skill_id) / "sediment"


def _interim_integration_dir() -> Path:
    """昨日过渡布局（references/vault-integration/），仅兼容读/自动迁移。"""
    return vb_skill_root() / "references" / "vault-integration"


# 与 skill 包顶层目录冲突的保留名（不允许作为 skill_id 生成数据目录）
RESERVED_ROOT_NAMES = {
    "scripts", "assets", "references", "teams", "docs", "knowledge",
    "briefs", "hooks", "tests",
}


def _path_is_link(p: Path) -> bool:
    """symlink 或 Windows junction 都算联接（指向真相源，非独立副本）。"""
    try:
        st = os.lstat(p)
    except OSError:
        return False
    if os.path.islink(p):
        return True
    return getattr(st, "st_reparse_tag", 0) == _stat.IO_REPARSE_TAG_MOUNT_POINT


def discover_skills() -> list[dict]:
    """多副本模型：同名 skill 跨 IDE 的实体副本合并为一个条目。

    返回条目：
      {"skill_id": str, "copies": [{"path", "real", "is_link", "sources": [...]}]}
    - common repo 真相源优先排前
    - 联接副本 resolve 后与真相源同 key → 并入其 sources，不重复计
    - 实体副本各自一条（SKILL.md 需分别注入路由节）
    """
    entries: dict[str, dict] = {}

    def _add(skill_id: str, path: Path, source: str) -> None:
        is_link = _path_is_link(path)
        try:
            real = str(path.resolve())
        except OSError:
            real = str(path)
        key = real.lower()
        e = entries.setdefault(skill_id, {"copies": [], "_real": {}})
        if key in e["_real"]:
            cp = e["_real"][key]
            if source not in cp["sources"]:
                cp["sources"].append(source)
            return
        cp = {
            "path": str(path),
            "real": real,
            "is_link": is_link,
            "sources": [source],
        }
        e["_real"][key] = cp
        e["copies"].append(cp)

    common = Path.home() / "common-skills-repo"
    env = os.environ.get("COMMON_SKILLS_REPO")
    if env:
        common = Path(env).expanduser()
    if common.is_dir():
        for p in common.iterdir():
            if p.is_dir() and (p / "SKILL.md").is_file():
                _add(p.name, p, "common-skills-repo")

    for ide, on in detect_ide_links().items():
        if not on:
            continue
        root = IDE_SKILLS.get(ide)
        if root is None:
            continue
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
                _add(p.name, p, "ide:%s" % ide)
        except OSError:
            continue

    out = []
    for sid, e in entries.items():
        copies = sorted(
            e["copies"],
            key=lambda c: 0 if "common-skills-repo" in c["sources"] else 1,
        )
        out.append({"skill_id": sid, "copies": copies})
    return sorted(out, key=lambda x: x["skill_id"])


def _render_section(skill_id: str, vault_base: str) -> str:
    tpl = (adapt_assets() / "SKILL.vault-section.md").read_text(encoding="utf-8")
    return tpl.replace("{{VAULT_BASE}}", vault_base).replace("{{SKILL_ID}}", skill_id)


def _prune_empty_tree(root: Path) -> bool:
    """自底向上删除 root 下的空目录；root 全空则一并删除。返回 root 是否被删。"""
    if not root.is_dir():
        return False
    for d in sorted((p for p in root.rglob("*") if p.is_dir()),
                    key=lambda x: len(x.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    try:
        root.rmdir()
        return True
    except OSError:
        return False


def _file_matches_template(path: Path, rel_template: str) -> bool:
    tpl = adapt_assets() / rel_template
    if not tpl.is_file() or not path.is_file():
        return False
    try:
        return path.read_text(encoding="utf-8") == tpl.read_text(encoding="utf-8")
    except OSError:
        return False


def _read_md(p: Path) -> tuple[str, str]:
    """读 SKILL.md，返回 (归一化为 \\n 的文本, 原换行风格)。

    read_text/write_text 默认 newline 翻译会让 LF 文件在 Windows 写出变 CRLF，
    破坏字节级无损往返；这里按 bytes 读、归一化供正则处理，写回时恢复原风格。
    """
    b = p.read_bytes()
    text = b.decode("utf-8")
    nl = "\r\n" if b"\r\n" in b else "\n"
    return text.replace("\r\n", "\n"), nl


def _write_md(p: Path, text: str, nl: str) -> None:
    """按原换行风格写回（bytes 直写，不做 newline 翻译）。"""
    p.write_bytes(text.replace("\n", nl).encode("utf-8"))


def _ensure_frontmatter(skill_md: Path, vault_base_path: str) -> str:
    text, nl = _read_md(skill_md)
    m = FRONT_RE.match(text)
    if not m:
        fm = (
            "---\n"
            "name: %s\n"
            "description: \"\"\n"
            "accepts_brief: true\n"
            'vault_base: "%s"\n'
            "---\n\n"
        ) % (skill_md.parent.name, vault_base_path.replace("\\", "/"))
        _write_md(skill_md, fm + text, nl)
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
    _write_md(skill_md, "---\n%s\n---\n%s" % (raw.rstrip(), body), nl)
    return "frontmatter_updated"


def _remove_frontmatter_keys(skill_md: Path) -> str:
    text, nl = _read_md(skill_md)
    m = FRONT_RE.match(text)
    if not m:
        return "frontmatter_absent"
    raw = m.group(1)
    new = re.sub(r"(?m)^(accepts_brief|vault_base)\s*:.*\n?", "", raw).rstrip()
    if new == raw.rstrip():
        return "frontmatter_ok"
    body = text[m.end():]
    _write_md(skill_md, "---\n%s\n---\n%s" % (new, body), nl)
    return "frontmatter_cleaned"


def _upsert_section(skill_md: Path, frag: str) -> str:
    text, nl = _read_md(skill_md)
    if MARK_BEGIN in text and MARK_END in text:
        _write_md(skill_md, MARK_RE.sub(frag.strip() + "\n", text), nl)
        return "section_replaced"
    if LEGACY_SECTION_MARK in text:
        _write_md(skill_md, LEGACY_SECTION_RE.sub(frag.strip() + "\n", text), nl)
        return "section_upgraded_from_legacy"
    _write_md(skill_md, text.rstrip() + "\n\n" + frag.strip() + "\n", nl)
    return "section_appended"


def _strip_section(skill_md: Path) -> str:
    text, nl = _read_md(skill_md)
    changed = False
    if MARK_BEGIN in text and MARK_END in text:
        text = MARK_RE.sub("", text)
        changed = True
    elif LEGACY_SECTION_MARK in text:
        text = LEGACY_SECTION_RE.sub("", text)
        changed = True
    if not changed:
        return "section_absent"
    _write_md(skill_md, text.rstrip() + "\n", nl)
    return "section_removed"


def _migrate_legacy(skill_path: Path, skill_id: str, actions: list) -> dict:
    """把 v1 注入到业务 skill 内的文件迁到 vault 侧；与模板一致才允许删除。

    返回 legacy ADAPTED.json 的内容（若有），供账本合并。
    """
    legacy_integ = skill_path / "references" / "vault-integration"
    legacy_data: dict = {}

    # 1) legacy preflight → vault 侧（vault 侧已有则跳过）
    vp = vault_preflight_path(skill_id)
    lp = legacy_integ / "preflight" / ("%s.preflight.yml" % skill_id)
    if lp.is_file():
        if vp.is_file():
            lp.unlink()
            actions.append(("legacy_preflight", "removed_duplicate"))
        else:
            vp.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(lp), str(vp))
            actions.append(("legacy_preflight", "migrated"))

    # 2) legacy sediment → vault 侧 <skill_id>/sediment/（合并，同名跳过）
    lsd = legacy_integ / "sediment"
    if lsd.is_dir():
        vsd = vault_sediment_dir(skill_id)
        vsd.mkdir(parents=True, exist_ok=True)
        moved = 0
        for f in sorted(lsd.rglob("*")):
            if not f.is_file():
                continue
            dest = vsd / f.name
            if not dest.exists():
                shutil.move(str(f), str(dest))
                moved += 1
        shutil.rmtree(lsd, ignore_errors=True)
        actions.append(("legacy_sediment", "migrated_%d_files" % moved))

    # 3) hooks / taxonomy / rules：与模板一致 → 删；被改过 → 保留并报告
    for rel, tpl_rel in LEGACY_TEMPLATE_MAP.items():
        p = skill_path / rel
        if not p.is_file():
            continue
        if _file_matches_template(p, tpl_rel):
            p.unlink()
            actions.append(("legacy_" + rel.replace("/", "_"), "removed"))
        else:
            actions.append(("legacy_" + rel.replace("/", "_"), "kept_modified"))

    # 4) legacy ADAPTED.json → 读出内容供账本合并，删除旧文件
    lm = legacy_integ / "ADAPTED.json"
    if lm.is_file():
        try:
            legacy_data = json.loads(lm.read_text(encoding="utf-8"))
        except Exception:
            legacy_data = {}
        lm.unlink()
        actions.append(("legacy_ADAPTED.json", "migrated"))

    # 5) 清空壳目录（自底向上剪枝）
    if legacy_integ.is_dir():
        if _prune_empty_tree(legacy_integ):
            actions.append(("legacy_integ_dir", "removed_empty"))
        else:
            actions.append(("legacy_integ_dir", "kept_non_empty"))
    hooks_dir = skill_path / "hooks"
    if hooks_dir.is_dir() and not any(hooks_dir.iterdir()):
        hooks_dir.rmdir()
        actions.append(("legacy_hooks_dir", "removed_empty"))
    return legacy_data


def _migrate_interim_layout(skill_id: str, actions: list) -> None:
    """昨日过渡布局 references/vault-integration/{preflight,sediment,adapted}/<id>
    → <vault-base>/<skill_id>/（新根侧已有则不覆盖）。"""
    it = _interim_integration_dir()
    for src, dst in (
        (it / "preflight" / ("%s.preflight.yml" % skill_id),
         vault_preflight_path(skill_id)),
        (it / "adapted" / ("%s.json" % skill_id), vault_record_path(skill_id)),
    ):
        if not src.is_file():
            continue
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            actions.append(("interim_" + src.parent.name, "migrated"))
        else:
            src.unlink()
            actions.append(("interim_" + src.parent.name, "removed_duplicate"))
    lsd = it / "sediment" / skill_id
    if lsd.is_dir():
        vsd = vault_sediment_dir(skill_id)
        vsd.mkdir(parents=True, exist_ok=True)
        moved = 0
        for f in sorted(lsd.rglob("*")):
            if f.is_file():
                d = vsd / f.name
                if not d.exists():
                    shutil.move(str(f), str(d))
                    moved += 1
        shutil.rmtree(lsd, ignore_errors=True)
        actions.append(("interim_sediment", "migrated_%d_files" % moved))
    if it.is_dir() and _prune_empty_tree(it):
        actions.append(("interim_integ_dir", "removed_empty"))


def _load_record(skill_id: str) -> dict:
    p = vault_record_path(skill_id)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def adapt_skill(skill_id: str, copies: list[dict], *, force: bool = False) -> dict:
    """适配一个 skill：所有实体副本的 SKILL.md 注入路由节，vault 侧单点建数据。

    copies: [{"path", "real", "is_link", "sources"}]（discover_skills 产出，仅实体副本）
    """
    if skill_id in RESERVED_ROOT_NAMES:
        return {
            "ok": False,
            "skill_id": skill_id,
            "error": "skill_id 与 vault-base 包目录同名，禁止生成数据目录: %s" % skill_id,
        }
    vb = str(vb_skill_root().as_posix())
    sd = skill_data_dir(skill_id)
    sd.mkdir(parents=True, exist_ok=True)
    actions: list = []

    # 昨日过渡布局 → 新根（幂等）
    _migrate_interim_layout(skill_id, actions)

    # v1 → v2 迁移（逐副本；幂等）
    for c in copies:
        _migrate_legacy(Path(c["real"]), skill_id, actions)

    # preflight 清单（vault 侧，全局一份，与 IDE 副本无关）
    from preflight_materials import autofill, dump, load, template_path
    dest = vault_preflight_path(skill_id)
    if not dest.exists() or force:
        text = template_path().read_text(encoding="utf-8")
        text = text.replace('skill_id: ""', 'skill_id: "%s"' % skill_id, 1)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        try:
            import yaml  # noqa: F401
            data = load(dest)
            autofill(data, Path(copies[0]["real"]) if copies else dest, skill_id)
            dump(data, dest)
        except ImportError:
            pass
        actions.append(("preflight", "wrote"))
    else:
        actions.append(("preflight", "skip"))

    # 每个实体副本的 SKILL.md 注入
    per_copy = []
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for c in copies:
        sp = Path(c["real"])
        ca = []
        skill_md = sp / "SKILL.md"
        if skill_md.is_file():
            ca.append(("frontmatter", _ensure_frontmatter(skill_md, vb)))
            ca.append(("skill_section", _upsert_section(skill_md, _render_section(skill_id, vb))))
        else:
            ca.append(("skill_section", "skipped_no_skill_md"))
        per_copy.append({
            "path": str(sp),
            "sources": c.get("sources") or [],
            "actions": ca,
        })
        tag = ",".join(c.get("sources") or [sp.name])
        for k, v in ca:
            actions.append(("[%s] %s" % (tag, k), v))

    # 适配账本：copies 合并历史记录（同 real path 保留首次 adapted_at）
    old = _load_record(skill_id)
    rec_copies: dict[str, dict] = {}
    for x in (old.get("copies") or []):
        if isinstance(x, dict) and x.get("path"):
            rec_copies[str(x["path"]).lower()] = dict(x)
    if not rec_copies and old.get("skill_path"):
        rec_copies[str(old["skill_path"]).lower()] = {
            "path": old["skill_path"], "sources": ["legacy"],
            "adapted_at": old.get("adapted_at", ""),
        }
    for c in copies:
        key = c["real"].lower()
        ent = rec_copies.setdefault(key, {"path": c["real"], "sources": [],
                                          "adapted_at": now})
        ent["path"] = c["real"]
        for s in (c.get("sources") or []):
            if s not in ent["sources"]:
                ent["sources"].append(s)

    record = {
        "schema": 2,
        "skill_id": skill_id,
        "vault_base": vb,
        "copies": sorted(rec_copies.values(), key=lambda x: x["path"]),
        "actions": actions,
        "adapted_at": now,
    }
    vault_record_path(skill_id).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    first_real = copies[0]["real"] if copies else ""
    return {
        "ok": True,
        "skill_id": skill_id,
        "path": first_real,
        "copies": [x["path"] for x in per_copy],
        "actions": actions,
        "per_copy": per_copy,
        "integration": str(sd),
        "data_dir": str(sd),
        "touched_in_skill": ["SKILL.md"] * len(copies),
        "next": [
            'python "%s/assets/skill-adapt/hooks/pre_task_recall.py" --skill-dir "%s" --skill-id %s'
            % (vb, first_real, skill_id),
            'python "%s/scripts/preflight_materials.py" missing --skill-dir "%s" --skill-id %s --json'
            % (vb, first_real, skill_id),
        ],
    }


def uninstall_skill(skill_id: str, copies: list[dict], *,
                    purge_legacy: bool = False) -> dict:
    """摘除所有副本的路由节 + vault 侧清单/账本；沉淀是知识资产，保留。"""
    actions: list = []

    # 副本集合 = 当前发现的实体副本 ∪ 账本记录过的副本（仅取仍存在的）
    seen: dict[str, str] = {}
    for c in copies:
        seen[c["real"].lower()] = c["real"]
    rec = _load_record(skill_id)
    for rc in (rec.get("copies") or []):
        p = rc.get("path")
        if p:
            try:
                rp = str(Path(p).expanduser().resolve())
            except OSError:
                continue
            seen.setdefault(rp.lower(), rp)
    if not seen and rec.get("skill_path"):
        try:
            rp = str(Path(rec["skill_path"]).expanduser().resolve())
            seen[rp.lower()] = rp
        except OSError:
            pass

    per_copy = []
    for real in sorted(seen.values()):
        sp = Path(real)
        if not sp.is_dir():
            continue
        ca = []
        skill_md = sp / "SKILL.md"
        if skill_md.is_file():
            ca.append(("skill_section", _strip_section(skill_md)))
            ca.append(("frontmatter", _remove_frontmatter_keys(skill_md)))
        else:
            ca.append(("skill_section", "skipped_no_skill_md"))

        # legacy 清理（默认只报告，--purge-legacy 才动手）
        legacy_integ = sp / "references" / "vault-integration"
        leftovers = [rel for rel in LEGACY_TEMPLATE_MAP if (sp / rel).is_file()]
        if legacy_integ.is_dir():
            leftovers.append("references/vault-integration/")
        if leftovers:
            if purge_legacy:
                removed, kept = [], []
                for rel in leftovers:
                    p = sp / rel
                    if p.is_file() and _file_matches_template(
                            p, LEGACY_TEMPLATE_MAP.get(rel, rel)):
                        p.unlink()
                        removed.append(rel)
                    elif p.is_file():
                        kept.append(rel)
                if legacy_integ.is_dir():
                    _prune_empty_tree(legacy_integ)
                hooks_dir = sp / "hooks"
                if hooks_dir.is_dir() and not any(hooks_dir.iterdir()):
                    hooks_dir.rmdir()
                ca.append(("legacy_purge", {"removed": removed, "kept_modified": kept}))
            else:
                ca.append(("legacy_leftovers", leftovers))
        per_copy.append({"path": real, "actions": ca})

    # vault 侧：删清单与账本（新根 + 过渡布局都扫）；沉淀保留
    for vp in (
        vault_preflight_path(skill_id),
        _interim_integration_dir() / "preflight" / ("%s.preflight.yml" % skill_id),
    ):
        if vp.is_file():
            vp.unlink()
            actions.append(("vault_preflight", "removed:%s" % vp))
    for vr in (
        vault_record_path(skill_id),
        _interim_integration_dir() / "adapted" / ("%s.json" % skill_id),
    ):
        if vr.is_file():
            vr.unlink()
            actions.append(("vault_record", "removed:%s" % vr))
    sediment = vault_sediment_dir(skill_id)
    actions.append(("vault_sediment",
                    "kept:%s" % sediment if sediment.is_dir() else "none"))

    return {
        "ok": True,
        "skill_id": skill_id,
        "per_copy": per_copy,
        "actions": actions,
        "sediment_kept": str(sediment) if sediment.is_dir() else None,
    }


def _resolve_targets(args) -> list[dict]:
    """解析目标；--all 与 --skill-dir/--skill-id 互斥（防误批量），失败返回 []。"""
    if getattr(args, "all", False) and (getattr(args, "skill_dir", None)
                                       or getattr(args, "skill_id", None)):
        raise SystemExit(
            "--all 与 --skill-id/--skill-dir 互斥：批量请单用 --all；单点请给 --skill-id/--skill-dir"
        )
    skills = discover_skills()
    if getattr(args, "all", False):
        return [s for s in skills if s["skill_id"] not in SKIP_NAMES]
    if getattr(args, "skill_dir", None):
        p = Path(args.skill_dir).expanduser().resolve()
        return [{"skill_id": args.skill_id or p.name,
                 "copies": [{"path": str(p), "real": str(p),
                             "is_link": False, "sources": ["manual"]}]}]
    if getattr(args, "skill_id", None):
        hit = next((s for s in skills if s["skill_id"] == args.skill_id), None)
        return [hit] if hit else []
    return []


def cmd_scan(args) -> int:
    out = []
    for s in discover_skills():
        sid = s["skill_id"]
        rec_exists = vault_record_path(sid).is_file()
        per = []
        for c in s["copies"]:
            p = Path(c["path"])
            md = p / "SKILL.md"
            text = md.read_text(encoding="utf-8", errors="replace") if md.is_file() else ""
            per.append({
                "path": c["path"],
                "real": c["real"],
                "sources": c["sources"],
                "is_link": c["is_link"],
                "marker": MARK_BEGIN in text,
                "legacy_v1": (p / "references" / "vault-integration"
                              / "ADAPTED.json").is_file(),
            })
        real_copies = [x for x in per if not x["is_link"]]
        n_marker = sum(1 for x in real_copies if x["marker"])
        adapted = bool(real_copies) and n_marker == len(real_copies)
        out.append({
            "skill_id": sid,
            "path": per[0]["path"] if per else "",
            "copies": per,
            "copy_count": len(real_copies),
            "adapted": adapted,
            "partially_adapted": 0 < n_marker < len(real_copies),
            "skip_suggested": sid in SKIP_NAMES,
        })
    if args.json:
        print(json.dumps({"count": len(out), "skills": out}, ensure_ascii=False, indent=2))
    else:
        for s in out:
            if s["skip_suggested"]:
                flag = "SKIP"
            elif s["adapted"]:
                flag = "ADAPTED"
            elif s["partially_adapted"]:
                flag = "PARTIAL"
            else:
                flag = "plain"
            print("%-34s %-8s copies=%d  %s" % (
                s["skill_id"], flag, s["copy_count"], s["path"]))
            for x in s["copies"][1:]:
                print("    %s%s" % ("link→ " if x["is_link"] else "     ", x["path"]))
    return 0


def cmd_adapt(args) -> int:
    targets = _resolve_targets(args)
    if not targets:
        msg = ("未在本机发现 skill: %s（可用 --skill-dir 指定路径）" % args.skill_id
               if args.skill_id else "需要 --skill-id / --skill-dir / --all")
        print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False), file=sys.stderr)
        return 2
    results = []
    for t in targets:
        sid = t["skill_id"]
        copies = [c for c in t.get("copies", []) if not c.get("is_link")]
        if not copies:
            results.append({"ok": False, "skill_id": sid, "error": "no real copies"})
            continue
        if sid in SKIP_NAMES and not args.include_infra:
            results.append({"ok": False, "skill_id": sid, "skipped": True})
            continue
        try:
            results.append(adapt_skill(sid, copies, force=bool(args.force)))
        except Exception as e:
            results.append({
                "ok": False, "skill_id": sid,
                "error": "%s: %s" % (type(e).__name__, e),
            })
    print(json.dumps({"ok": True, "results": results}, ensure_ascii=False, indent=2))
    return 0


def cmd_uninstall(args) -> int:
    targets = _resolve_targets(args)
    if not targets:
        msg = ("未在本机发现 skill: %s（可用 --skill-dir 指定路径）" % args.skill_id
               if args.skill_id else "需要 --skill-id / --skill-dir / --all")
        print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False), file=sys.stderr)
        return 2
    results = []
    for t in targets:
        sid = t["skill_id"]
        copies = [c for c in t.get("copies", []) if not c.get("is_link")]
        if sid in SKIP_NAMES and not args.include_infra:
            results.append({"ok": False, "skill_id": sid, "skipped": True})
            continue
        try:
            results.append(uninstall_skill(
                sid, copies, purge_legacy=bool(args.purge_legacy)))
        except Exception as e:
            results.append({
                "ok": False, "skill_id": sid,
                "error": "%s: %s" % (type(e).__name__, e),
            })
    print(json.dumps({"ok": True, "results": results}, ensure_ascii=False, indent=2))
    return 0


def cmd_status(args) -> int:
    skills = discover_skills()
    sid = args.skill_id
    hit = next((s for s in skills if s["skill_id"] == sid), None)
    if not hit and args.skill_dir:
        p = Path(args.skill_dir).expanduser().resolve()
        hit = {"skill_id": sid, "copies": [{"path": str(p), "real": str(p),
                                            "is_link": False, "sources": ["manual"]}]}
    if not hit:
        print(json.dumps({"ok": False, "error": "not found"}, ensure_ascii=False))
        return 2
    vinteg = _interim_integration_dir()
    rec = _load_record(sid)
    per = []
    for c in hit["copies"]:
        sp = Path(c["path"])
        md = sp / "SKILL.md"
        text = md.read_text(encoding="utf-8", errors="replace") if md.is_file() else ""
        per.append({
            "path": c["path"],
            "real": c["real"],
            "sources": c["sources"],
            "is_link": c["is_link"],
            "marker_section": MARK_BEGIN in text,
            "legacy_section": LEGACY_SECTION_MARK in text and MARK_BEGIN not in text,
            "legacy_v1_leftovers": [rel for rel in LEGACY_TEMPLATE_MAP
                                    if (sp / rel).is_file()],
        })
    real = [x for x in per if not x["is_link"]]
    report = {
        "skill_id": sid,
        "copies": per,
        "copy_count": len(real),
        "adapted": all(x["marker_section"] for x in real) if real else bool(rec),
        "data_dir": str(skill_data_dir(sid)),
        "vault_preflight": str(vault_preflight_path(sid)),
        "vault_preflight_exists": vault_preflight_path(sid).is_file(),
        "vault_record": str(vault_record_path(sid)),
        "vault_record_exists": vault_record_path(sid).is_file(),
        "vault_sediment": str(vault_sediment_dir(sid)),
        "vault_sediment_exists": vault_sediment_dir(sid).is_dir(),
        "interim_layout_pending": any((vinteg / sub).exists()
                                      for sub in ("preflight", "adapted", "sediment"))
                                      if vinteg.is_dir() else False,
        "recorded_copies": [x.get("path") for x in (rec.get("copies") or [])],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["adapted"] else 1


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description="扫描并适配业务 skill → vault-base 标准链路（中心化注入，跨 IDE 知识单点汇总）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="列出本机 skill 及是否已适配（含多副本）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("adapt",
                       help="注入 SKILL.md 路由节 + vault 侧 preflight（幂等，自动迁移 v1，多副本全注入）")
    p.add_argument("--skill-id")
    p.add_argument("--skill-dir")
    p.add_argument("--all", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--include-infra", action="store_true",
                   help="连 vault-base 自身等基础设施 skill 也适配")
    p.set_defaults(func=cmd_adapt)

    p = sub.add_parser("uninstall", help="摘除所有副本路由节 + vault 侧清单/账本（沉淀保留）")
    p.add_argument("--skill-id")
    p.add_argument("--skill-dir")
    p.add_argument("--all", action="store_true")
    p.add_argument("--purge-legacy", action="store_true",
                   help="同时清理 v1 遗留注入文件（与模板一致才删，被改过的仅报告）")
    p.add_argument("--include-infra", action="store_true")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("status", help="查看单 skill 适配状态（含各副本）")
    p.add_argument("--skill-id", required=True)
    p.add_argument("--skill-dir")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
