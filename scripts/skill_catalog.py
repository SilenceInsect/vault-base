#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""skill_catalog.py — 扫描本机已安装 Skill，生成索引，并对照历史元数据与当前源。

索引回答四件事：谁的、什么技能、处理什么任务、任务元数据清单。
访问共享金库前先 lookup：用索引找出历史元数据与当前源，合并后再给出有目的的检索键。

用法：
  python skill_catalog.py scan --write
  python skill_catalog.py scan --write --workspace <项目根>
  python skill_catalog.py lookup --skill-id <id> --json
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from brief_bootstrap import collect_elements          # noqa: E402
from vault_paths import common_skills_repo, vault_root  # noqa: E402

TZ = timezone(timedelta(hours=8))
FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*", re.S)
CHECK_RE = re.compile(r"^[-*]\s+\[[ xX]\]\s+(.+)$")
HEAD_RE = re.compile(r"^##\s+(.+)$")
FENCE_RE = re.compile(r"^```")
KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:")
BTICK_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*)`")

NOISE_KEYS = {
    "json", "stdout", "help", "version", "verbose", "debug", "all", "top",
    "out", "vault", "write", "promote", "samples", "fix", "once", "mode",
    "user", "force", "dry_run", "quiet",
}
SKIP_COMMON = {"_publish", "_migrate_bak", "_extract_from_plan"}
CATALOG_JSON = "skill-catalog.json"
CATALOG_MD = "skill-catalog.md"


def now_iso() -> str:
    return datetime.now(TZ).replace(microsecond=0).isoformat()


def home_display(path: Path) -> str:
    """展示安装位置。不 resolve，避免联接全部显示成公共仓路径。"""
    try:
        rel = path.expanduser().absolute().relative_to(Path.home())
        return "~/" + rel.as_posix()
    except Exception:
        return str(path)


def load_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONT_RE.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    body = text[m.end():]
    try:
        import yaml
        data = yaml.safe_load(raw) or {}
        if isinstance(data, dict):
            return data, body
    except Exception:
        pass
    fm: dict = {}
    for line in raw.splitlines():
        if ":" not in line or line.startswith(" ") or line.startswith("-"):
            continue
        k, v = line.split(":", 1)
        fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, body


def as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    return ""


def split_task(description: str) -> tuple[str, list[str]]:
    """从 description 拆出「处理什么」和触发词。"""
    text = re.sub(r"\s+", " ", description).strip()
    if not text:
        return "", []
    m = re.search(
        r"(?:。\s*)?(?:在用户提到|当用户提到|当需要|当用户(?:粘贴|给出|提到|提出)|"
        r"触发词[:：]|Use only when|Use when|Use for|Trigger(?:ed)? when)\s*(.+)$",
        text,
        re.I,
    )
    triggers: list[str] = []
    handles = text
    if m:
        handles = text[:m.start()].strip(" 。；;.")
        chunk = re.sub(r"(时使用|时触发)。?$", "", m.group(1)).strip(" 。.")
        parts = re.split(r"[、,，/]| or | and ", chunk)
        triggers = [p.strip(" 。.；;") for p in parts if len(p.strip(" 。.；;")) > 1]
    if len(handles) > 180:
        handles = handles[:180].rstrip() + "…"
    return handles, triggers[:12]


def checklist_steps(body: str, limit: int = 8) -> list[str]:
    steps: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = CHECK_RE.match(line.strip())
        if not m:
            continue
        step = re.sub(r"\s+", " ", m.group(1)).strip()
        if step and step not in steps:
            steps.append(step)
        if len(steps) >= limit:
            break
    return steps


def purpose_line(body: str) -> str:
    """取「目的」小节的第一句，补 description 没写清的任务。"""
    lines = body.splitlines()
    capture = False
    buf: list[str] = []
    for line in lines:
        if HEAD_RE.match(line.strip()):
            title = HEAD_RE.match(line.strip()).group(1).strip()
            if capture:
                break
            capture = title in {"目的", "Purpose", "Role"}
            continue
        if capture:
            s = line.strip()
            if not s or s.startswith("#") or s.startswith("|") or s.startswith("```"):
                if buf:
                    break
                continue
            buf.append(s.lstrip("- ").strip())
            if len(" ".join(buf)) > 160:
                break
    text = re.sub(r"\s+", " ", " ".join(buf)).strip()
    return text[:180]


def brief_elements(text: str) -> list[dict]:
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("##") and "必要信息要素" in line:
            start = i + 1
            break
    if start is None:
        return []
    rows = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        if cells[0] in {"要素", "---"} or set(cells[0]) <= {"-", ":"}:
            continue
        if not cells[0] or cells[0] in {"（待填）", "(待填)"}:
            continue
        rows.append({
            "key": cells[0],
            "necessity": cells[1] or "可选",
            "acquire": cells[2],
            "impact": cells[3],
            "source": "brief",
        })
    return rows


def schema_elements(schema_path: Path) -> list[dict]:
    rows = []
    try:
        text = schema_path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        if not line or line[0] in " \t#":
            continue
        m = KEY_RE.match(line)
        if not m:
            continue
        key = m.group(1)
        comment = line.split("#", 1)[1].strip() if "#" in line else ""
        necessity = "必须" if "必填" in comment else "可选"
        rows.append({
            "key": key,
            "necessity": necessity,
            "acquire": "当前源：%s" % schema_path.name,
            "impact": comment[:80],
            "source": "schema",
        })
    return rows


def skill_text_required(body: str) -> list[dict]:
    rows = []
    seen = set()
    for line in body.splitlines():
        if "必填" not in line:
            continue
        for key in BTICK_RE.findall(line):
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "key": key,
                "necessity": "必须",
                "acquire": "当前源：SKILL.md 必填声明",
                "impact": "",
                "source": "skill_text",
            })
    return rows


def inferred_elements(skill_dir: Path) -> list[dict]:
    rows = []
    try:
        raw = collect_elements(skill_dir)
    except Exception:
        return rows
    for item in raw:
        key = str(item.get("name") or "")
        if not key or key in NOISE_KEYS:
            continue
        rows.append({
            "key": key,
            "necessity": item.get("necessity") or "可选",
            "acquire": item.get("acquire") or "",
            "impact": item.get("impact") or "",
            "source": "inferred",
        })
        if len(rows) >= 16:
            break
    return rows


def merge_elements(*groups: list[dict]) -> list[dict]:
    """按 source 优先级去重，保留先出现的更具体声明。"""
    order = {"skill_text": 0, "schema": 1, "brief": 2, "inferred": 3}
    merged: dict[str, dict] = {}
    for group in groups:
        for item in group:
            key = item["key"]
            if key in NOISE_KEYS:
                continue
            prev = merged.get(key)
            if prev is None or order.get(item["source"], 9) < order.get(prev["source"], 9):
                merged[key] = dict(item)
    return list(merged.values())


def current_metadata(skill_dir: Path, body: str, vault: Path, skill_id: str,
                     schemas: list[Path]) -> list[dict]:
    """当前源上的任务元数据。brief 里的历史键留给 lookup 合并，不灌进当前清单。"""
    declared = merge_elements(
        skill_text_required(body),
        *[schema_elements(p) for p in schemas],
    )
    if declared:
        return declared
    brief = [
        e for e in brief_elements_for(vault, skill_id)
        if e["key"] not in NOISE_KEYS and e.get("necessity") == "必须"
    ]
    if brief:
        return brief
    inferred = [
        e for e in inferred_elements(skill_dir)
        if e.get("necessity") == "必须" and e["key"] not in NOISE_KEYS
    ]
    if inferred:
        return inferred
    return [{
        "key": "任务目标",
        "necessity": "必须",
        "acquire": "问用户 / 读 SKILL.md description",
        "impact": "阻塞性缺失",
        "source": "inferred",
    }]


def find_schemas(skill_dir: Path) -> list[Path]:
    refs = skill_dir / "references"
    if not refs.is_dir():
        return []
    found = []
    for pat in ("*schema*.yml", "*schema*.yaml", "*input*.yml"):
        found.extend(refs.rglob(pat))
    uniq = []
    seen = set()
    for p in found:
        key = os.path.normcase(str(p))
        if key in seen or p.name.startswith("."):
            continue
        seen.add(key)
        uniq.append(p)
    return uniq[:6]


def iter_skill_dirs(root: Path):
    if not root.is_dir():
        return
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return
    for child in children:
        if not child.is_dir():
            continue
        if child.name.startswith(".") and child.name != ".system":
            continue
        if child.name in SKIP_COMMON:
            continue
        if child.name == ".system":
            yield from iter_skill_dirs(child)
            continue
        if (child / "SKILL.md").is_file():
            yield child


def install_roots(workspaces: list[Path]) -> list[tuple[str, Path, str]]:
    """(host, root, origin) origin: user | product | common | workspace。"""
    home = Path.home()
    roots = [
        ("cursor", home / ".cursor" / "skills", "user"),
        ("claude", home / ".claude" / "skills", "user"),
        ("codex", home / ".codex" / "skills", "user"),
        ("common", common_skills_repo(), "common"),
    ]
    for ws in workspaces:
        roots.append(("workspace:%s" % ws.name, ws / ".cursor" / "skills", "workspace"))
    return roots


def is_link(path: Path) -> bool:
    try:
        return os.path.normcase(str(path.resolve())) != os.path.normcase(str(path.absolute()))
    except OSError:
        return False


def understand_skill(skill_dir: Path, installs: list[dict], origin: str,
                     vault: Path) -> dict:
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    fm, body = load_frontmatter(text)
    skill_id = as_text(fm.get("name")) or skill_dir.name
    description = as_text(fm.get("description"))
    handles, triggers = split_task(description)
    purpose = purpose_line(body)
    if purpose and purpose not in handles:
        task_text = purpose if len(purpose) > len(handles) else handles
    else:
        task_text = handles or purpose or skill_id
    steps = checklist_steps(body)

    schemas = find_schemas(skill_dir)
    elements = current_metadata(skill_dir, body, vault, skill_id, schemas)
    hosts = []
    for inst in installs:
        if inst["host"] not in hosts:
            hosts.append(inst["host"])
    return {
        "owner": getpass.getuser(),
        "hosts": hosts,
        "origin": origin,
        "skill_id": skill_id,
        "canonical": str(skill_dir),
        "canonical_display": home_display(skill_dir),
        "installs": installs,
        "accepts_brief": bool(fm.get("accepts_brief")),
        "vault_base": as_text(fm.get("vault_base")),
        "task": task_text,
        "triggers": triggers,
        "steps": steps,
        "metadata": elements,
        "current_sources": current_sources(skill_dir, vault, skill_id, schemas),
    }


def brief_elements_for(vault: Path, skill_id: str) -> list[dict]:
    path = vault / "briefs" / ("%s.md" % skill_id)
    if not path.is_file():
        return []
    try:
        return brief_elements(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []


def current_sources(skill_dir: Path, vault: Path, skill_id: str,
                    schemas: list[Path]) -> list[dict]:
    sources = [{
        "kind": "skill",
        "ref": home_display(skill_dir / "SKILL.md"),
        "path": str(skill_dir / "SKILL.md"),
    }]
    brief = vault / "briefs" / ("%s.md" % skill_id)
    if brief.is_file():
        sources.append({
            "kind": "brief",
            "ref": "briefs/%s.md" % skill_id,
            "path": str(brief),
        })
    for schema in schemas:
        sources.append({
            "kind": "schema",
            "ref": home_display(schema),
            "path": str(schema),
        })
    domain = skill_dir / "references" / "vault"
    if domain.is_dir():
        sources.append({
            "kind": "domain_vault",
            "ref": home_display(domain),
            "path": str(domain),
        })
    return sources


def peek_skill_id(skill_dir: Path) -> str:
    try:
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return skill_dir.name
    fm, _body = load_frontmatter(text)
    return as_text(fm.get("name")) or skill_dir.name


def install_rank(item: dict) -> tuple:
    """同一 skill_id 多份副本时，用公共仓或用户安装做「当前源」。"""
    origin_rank = {"common": 0, "user": 1, "workspace": 2, "product": 3}.get(item["origin"], 9)
    host_rank = {"common": 0, "cursor": 1, "claude": 2, "codex": 3}.get(item["host"], 4)
    return (origin_rank, host_rank, item["path"])


def scan(workspaces: list[Path], vault: Path) -> dict:
    found: list[dict] = []
    seen_path: set[str] = set()
    for host, root, origin in install_roots(workspaces):
        for skill_dir in iter_skill_dirs(root):
            try:
                canonical = skill_dir.resolve()
            except OSError:
                canonical = skill_dir
            path_key = os.path.normcase(str(canonical)) + "|" + host
            if path_key in seen_path:
                continue
            seen_path.add(path_key)
            this_origin = "product" if ".system" in skill_dir.parts else origin
            found.append({
                "skill_id": peek_skill_id(skill_dir),
                "dir": canonical,
                "origin": this_origin,
                "host": host,
                "path": str(skill_dir),
                "display": home_display(skill_dir),
                "linked": is_link(skill_dir),
            })

    buckets: dict[str, list[dict]] = {}
    for item in found:
        buckets.setdefault(item["skill_id"], []).append(item)

    origin_order = {"user": 0, "workspace": 1, "common": 2, "product": 3}
    skills = []
    for items in buckets.values():
        items.sort(key=install_rank)
        primary = items[0]
        origins = {it["origin"] for it in items}
        if "user" in origins or "common" in origins:
            origin = "user" if "user" in origins else "common"
        elif "workspace" in origins:
            origin = "workspace"
        else:
            origin = "product"
        installs = [{
            "host": it["host"],
            "path": it["path"],
            "display": it["display"],
            "linked": it["linked"],
        } for it in items]
        # 同一路径被多个 host 联接到一起时，host 还是要都留下，路径展示去重交给表格。
        skill = understand_skill(primary["dir"], installs, origin, vault)
        digests = set()
        for it in items:
            try:
                digests.add((it["dir"] / "SKILL.md").read_bytes())
            except OSError:
                digests.add(b"")
        if len(digests) > 1:
            skill["variant_note"] = "同名技能有 %d 份内容不完全相同的副本，当前源取 %s" % (
                len(digests), primary["display"],
            )
        skills.append(skill)
    skills.sort(key=lambda s: (origin_order.get(s["origin"], 9), s["skill_id"].lower()))
    return {
        "generated_at": now_iso(),
        "owner": getpass.getuser(),
        "vault": str(vault),
        "skills": skills,
    }


def catalog_paths(vault: Path) -> tuple[Path, Path]:
    kb = vault / "_kb"
    return kb / CATALOG_JSON, kb / CATALOG_MD


def meta_brief(items: list[dict], limit: int = 10) -> str:
    if not items:
        return "（未声明）"
    parts = []
    for item in items[:limit]:
        mark = "*" if item.get("necessity") == "必须" else ""
        parts.append(mark + item["key"])
    if len(items) > limit:
        parts.append("…+%d" % (len(items) - limit))
    return "、".join(parts)


def render_md(catalog: dict) -> str:
    lines = [
        "# 本机 Skill 索引",
        "",
        "生成时间：%s" % catalog["generated_at"],
        "账号：%s" % catalog["owner"],
        "",
        "本文件只描述这台机器上的安装，`is_candidate_shared` 不适用，不要入共享候选。",
        "带 `*` 的元数据键为当前源标明的必须项。",
        "",
        "| 谁 | 技能 | 处理的任务 | 任务元数据清单 | 安装位置 |",
        "|---|---|---|---|---|",
    ]
    for skill in catalog["skills"]:
        hosts = "、".join(skill["hosts"])
        who = "%s · %s" % (skill["owner"], hosts)
        places = "<br>".join(
            "%s%s" % (inst["display"], "（联接）" if inst["linked"] else "")
            for inst in skill["installs"]
        )
        task = skill["task"].replace("|", "/")
        lines.append("| %s | `%s` | %s | %s | %s |" % (
            who, skill["skill_id"], task, meta_brief(skill["metadata"]), places,
        ))
    lines.append("")
    lines.append("## 明细")
    lines.append("")
    for skill in catalog["skills"]:
        lines.append("### %s" % skill["skill_id"])
        lines.append("")
        lines.append("- 谁：%s（%s）" % (skill["owner"], "、".join(skill["hosts"])))
        lines.append("- 来源：%s" % skill["origin"])
        lines.append("- 任务：%s" % skill["task"])
        if skill["triggers"]:
            lines.append("- 触发：%s" % "、".join(skill["triggers"]))
        if skill["steps"]:
            lines.append("- 步骤：")
            for step in skill["steps"]:
                lines.append("  - %s" % step)
        lines.append("- 元数据清单：")
        if not skill["metadata"]:
            lines.append("  - （未声明）")
        for item in skill["metadata"]:
            lines.append("  - %s｜%s｜%s" % (
                item["key"], item.get("necessity") or "可选", item.get("source") or "",
            ))
        lines.append("- 当前源：")
        for src in skill["current_sources"]:
            lines.append("  - %s：%s" % (src["kind"], src["ref"]))
        lines.append("")
    return "\n".join(lines) + "\n"


def write_catalog(catalog: dict, vault: Path) -> tuple[Path, Path]:
    js, md = catalog_paths(vault)
    js.parent.mkdir(parents=True, exist_ok=True)
    js.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    md.write_text(render_md(catalog), encoding="utf-8")
    return js, md


def load_catalog(vault: Path) -> dict | None:
    js, _md = catalog_paths(vault)
    if not js.is_file():
        return None
    try:
        return json.loads(js.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def historical_local(vault: Path, skill_id: str) -> list[dict]:
    """本地 Vault 里已有、且归属该 skill 的笔记（历史元数据载体）。"""
    hits = []
    if not vault.is_dir():
        return hits
    for path in vault.rglob("*.md"):
        if "_kb" in path.parts or ".obsidian" in path.parts or "_samples" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, _body = load_frontmatter(text)
        owner = as_text(fm.get("skill_id"))
        stem = path.stem
        sample = stem.startswith(skill_id + ".sample")
        if owner != skill_id and stem != skill_id and not sample:
            continue
        rows = brief_elements(text)
        keys = [row["key"] for row in rows]
        hits.append({
            "layer": "local_vault",
            "ref": str(path.relative_to(vault)).replace("\\", "/"),
            "doc_type": as_text(fm.get("doc_type")),
            "title": as_text(fm.get("title")) or path.stem,
            "metadata_keys": keys,
            "metadata": [
                {"key": row["key"], "necessity": row.get("necessity") or "可选"}
                for row in rows
            ],
            "updated_at": as_text(fm.get("updated_at")),
        })
    return hits


def _historical_key(key: str) -> bool:
    """历史 brief 里的短英文参数名不拿去检索；中文任务字段和带点号的路径要留。"""
    if not key or key in NOISE_KEYS:
        return False
    if any(ord(ch) > 127 for ch in key):
        return True
    return "." in key or "_" in key


def historical_shared(skill_id: str, vault: Path, limit: int = 8) -> tuple[list[dict], str]:
    """按 skill_id 读共享层：优先 prefixCache，回源 MySQL（vault_recall）。"""
    try:
        import vault_recall
        result = vault_recall.recall(
            skill_id, query=skill_id, limit=limit, root=vault,
            include_local=False, use_query_cache=True,
        )
    except Exception as exc:
        return [], "shared_unavailable:%s" % type(exc).__name__
    if result.get("error"):
        return [], "shared_search_failed:%s" % result["error"][:160]
    hits = []
    for row in result.get("hits") or []:
        claim = as_text(row.get("claim_key"))
        keys = [claim] if claim else []
        src = as_text(row.get("source")) or "shared"
        hits.append({
            "layer": "shared_cache" if "cache" in src else "shared",
            "ref": row.get("uuid") or "",
            "doc_type": as_text(row.get("doc_type")),
            "title": row.get("title") or "",
            "metadata_keys": keys,
            "metadata": [{"key": claim, "necessity": "必须"}] if claim else [],
            "skill_id": as_text(row.get("skill_id")) or skill_id,
            "source": src,
        })
    return hits, "ok"


def merge_history(current: list[dict], historical: list[dict]) -> list[dict]:
    """当前清单为主键；历史里多出来的键标成 historical_only。"""
    by_key: dict[str, dict] = {}
    for item in current:
        by_key[item["key"]] = {
            "key": item["key"],
            "necessity": item.get("necessity") or "可选",
            "current_source": item.get("source") or "",
            "acquire": item.get("acquire") or "",
            "in_current": True,
            "in_historical": False,
            "historical_refs": [],
        }
    for hit in historical:
        for row in hit.get("metadata") or [
            {"key": key, "necessity": "历史有、当前源未声明"}
            for key in (hit.get("metadata_keys") or [])
        ]:
            key = row["key"]
            if key in NOISE_KEYS:
                continue
            ref = "%s:%s" % (hit.get("layer"), hit.get("ref"))
            slot = by_key.get(key)
            if slot is None:
                by_key[key] = {
                    "key": key,
                    "necessity": row.get("necessity") or "历史有、当前源未声明",
                    "current_source": "",
                    "acquire": "",
                    "in_current": False,
                    "in_historical": True,
                    "historical_refs": [ref],
                }
            else:
                slot["in_historical"] = True
                if ref not in slot["historical_refs"]:
                    slot["historical_refs"].append(ref)
    merged = list(by_key.values())

    def rank(item: dict) -> tuple:
        if item["in_current"] and item["in_historical"]:
            group = 0
        elif item["in_historical"]:
            group = 1
        else:
            group = 2
        must = 0 if item.get("necessity") == "必须" else 1
        return (group, must, item["key"])

    merged.sort(key=rank)
    return merged


def lookup_plan(skill: dict, merged: list[dict]) -> list[dict]:
    """合并后才生成检索键：先技能身份，再必须元数据，再仅历史仍在用的键。"""
    skill_id = skill["skill_id"]
    plan = [{
        "layer": "shared",
        "query": skill_id,
        "why": "同技能历史条目",
    }, {
        "layer": "local_vault",
        "query": "skill_id=%s" % skill_id,
        "why": "底座本地笔记与 brief",
    }]
    for src in skill.get("current_sources") or []:
        if src["kind"] in {"schema", "domain_vault", "brief"}:
            plan.append({
                "layer": "current_source",
                "query": src["ref"],
                "why": "当前源：%s" % src["kind"],
            })
    keys: list[str] = []

    def take(pred) -> None:
        for item in merged:
            key = item["key"]
            if key in NOISE_KEYS or key in keys:
                continue
            if item.get("necessity") != "必须":
                continue
            if pred(item):
                keys.append(key)

    take(lambda item: item.get("in_current"))
    take(lambda item: item.get("in_historical") and _historical_key(item["key"]))
    for key in keys[:8]:
        plan.append({
            "layer": "shared",
            "query": "%s %s" % (skill_id, key),
            "why": "按合并后的元数据键回查",
        })
    return plan


def lookup(catalog: dict, skill_id: str, vault: Path) -> dict:
    skill = None
    for item in catalog.get("skills") or []:
        if item.get("skill_id") == skill_id:
            skill = item
            break
    if skill is None:
        known = [s.get("skill_id") for s in catalog.get("skills") or []]
        return {
            "ok": False,
            "skill_id": skill_id,
            "error": "索引中没有该技能",
            "known": known,
        }
    local_hits = historical_local(vault, skill_id)
    shared_hits, shared_status = historical_shared(skill_id, vault)
    historical = local_hits + shared_hits
    merged = merge_history(skill.get("metadata") or [], historical)
    return {
        "ok": True,
        "skill_id": skill_id,
        "owner": skill.get("owner"),
        "hosts": skill.get("hosts"),
        "task": skill.get("task"),
        "generated_at": catalog.get("generated_at"),
        "historical": historical,
        "shared_status": shared_status,
        "current_sources": skill.get("current_sources") or [],
        "merged_metadata": merged,
        "lookup_plan": lookup_plan(skill, merged),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="扫描本机 Skill 并生成索引 / 对照检索计划")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scan", help="扫描本机安装并理解 Skill")
    sp.add_argument("--workspace", action="append", default=[],
                    help="额外扫描 <项目>/.cursor/skills，可重复")
    sp.add_argument("--vault")
    sp.add_argument("--write", action="store_true", help="写入 _kb/skill-catalog.json 与 .md")
    sp.add_argument("--json", action="store_true")

    lp = sub.add_parser("lookup", help="按索引合并历史元数据与当前源，再给出检索键")
    lp.add_argument("--skill-id", required=True)
    lp.add_argument("--workspace", action="append", default=[])
    lp.add_argument("--vault")
    lp.add_argument("--refresh", action="store_true", help="忽略已有索引，重新扫描")
    lp.add_argument("--json", action="store_true")

    args = ap.parse_args()
    vault = vault_root(getattr(args, "vault", None))
    workspaces = [Path(p).expanduser().resolve() for p in (args.workspace or [])]

    if args.cmd == "scan":
        catalog = scan(workspaces, vault)
        payload = {
            "ok": True,
            "count": len(catalog["skills"]),
            "generated_at": catalog["generated_at"],
            "skills": [
                {
                    "owner": s["owner"],
                    "hosts": s["hosts"],
                    "skill_id": s["skill_id"],
                    "task": s["task"],
                    "metadata": [m["key"] for m in s["metadata"]],
                }
                for s in catalog["skills"]
            ],
        }
        if args.write:
            js, md = write_catalog(catalog, vault)
            payload["json"] = str(js)
            payload["markdown"] = str(md)
        if args.json or args.write:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(render_md(catalog))
        return 0

    catalog = None if args.refresh else load_catalog(vault)
    if catalog is None:
        catalog = scan(workspaces, vault)
    result = lookup(catalog, args.skill_id, vault)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
