# -*- coding: utf-8 -*-
"""Audit local vault MD against vault-base naming + frontmatter rules."""
from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED = [
    "schema_version", "uuid", "skill_id", "doc_type", "title", "author",
    "author_ip", "created_at", "updated_at", "is_candidate_shared",
    "share_rev", "tags", "content_hash",
]
# plan 3.3 also lists source_task_id as required
REQUIRED_SOFT = ["source_task_id"]
DOC_TYPES = {"answer", "decision", "reference", "brief"}
UUID_NAME = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.md$", re.I
)
BRIEF_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*\.md$", re.I)
SAMPLE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*\.sample-\d+\.md$", re.I)
CR_NAME = re.compile(r"^(DEC-CR|LESSON-CR)-\d{4}-\d{4}-.+\.md$")
BODY_AD = ["## 问题", "## 答案&决策", "## 决策依据", "## 备注"]


def parse_fm(text: str):
    if not text.startswith("---"):
        return None, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text
    fm = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, parts[2]


def check_vault(root: Path, label: str):
    print("=" * 64)
    print("金库:", label)
    print("路径:", root)
    mds = [p for p in root.rglob("*.md") if ".git" not in p.parts]
    fail_files = 0
    issue_n = 0
    if not mds:
        print("(无 md 文件)")
        return
    for p in sorted(mds):
        rel = p.relative_to(root).as_posix()
        # 归档目录不计入合规验收
        if any(part.startswith("_archive") for part in p.parts):
            continue
        text = p.read_text(encoding="utf-8")
        fm, body = parse_fm(text)
        problems = []

        if rel.startswith("briefs/_samples/"):
            if not (SAMPLE_NAME.match(p.name) or BRIEF_NAME.match(p.name)):
                problems.append("样本文件名不合规: %s" % p.name)
        elif rel.startswith("briefs/"):
            if not BRIEF_NAME.match(p.name):
                problems.append("brief 应为 briefs/{skill_id}.md，实际: %s" % p.name)
            # brief should not be uuid-named
            if UUID_NAME.match(p.name):
                problems.append("brief 禁止 uuid 文件名")
        elif any(rel.startswith(d + "/") for d in ("answers", "decisions", "references")):
            if not UUID_NAME.match(p.name):
                problems.append(
                    "标准沉淀须 {uuid}.md 且落在 answers|decisions|references/，实际: %s" % rel
                )
        else:
            if CR_NAME.match(p.name):
                problems.append(
                    "遗留 CR 命名/平铺根目录：规范要求 decisions|answers|references/{uuid}.md"
                )
            elif p.name.startswith("_"):
                pass  # allow _README etc under typed dirs only - root placeholder?
            else:
                problems.append("未知目录或命名: %s（应在 answers/decisions/references/briefs）" % rel)

        if fm is None:
            problems.append("缺少 YAML frontmatter")
        else:
            missing = [k for k in REQUIRED if k not in fm]
            if missing:
                problems.append("缺 vault-base 必填字段: " + ",".join(missing))
            soft_m = [k for k in REQUIRED_SOFT if k not in fm]
            if soft_m and not missing:
                problems.append("缺推荐/方案必填 source_task_id 等: " + ",".join(soft_m))
            dt = fm.get("doc_type", "")
            if dt and dt not in DOC_TYPES:
                problems.append("doc_type 非法: %s" % dt)
            if UUID_NAME.match(p.name) and fm.get("uuid"):
                if (fm["uuid"].lower() + ".md") != p.name.lower():
                    problems.append(
                        "文件名与 frontmatter.uuid 不一致: %s" % fm.get("uuid")
                    )
            if dt in ("answer", "decision"):
                for h in BODY_AD:
                    if h not in body:
                        problems.append("缺正文区块: %s" % h)
            if rel.startswith("briefs/") and not rel.startswith("briefs/_"):
                for h in ("## 目标与产出", "## 必要信息要素", "## 迭代记录"):
                    if h not in body:
                        problems.append("brief 缺区块: %s" % h)

        status = "FAIL" if problems else "OK  "
        print("[%s] %s" % (status, rel))
        if fm:
            print(
                "       schema=%s doc_type=%s title=%s"
                % (fm.get("schema_version", "?"), fm.get("doc_type", fm.get("kind", "?")), (fm.get("title") or "")[:48])
            )
        for pr in problems:
            print("       - %s" % pr)
            issue_n += 1
        if problems:
            fail_files += 1

    print("\n汇总 %s: 文件=%d 不合规文件=%d 问题条数=%d" % (label, len(mds), fail_files, issue_n))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    check_vault(
        Path(r"C:\Users\<user>\.cursor\skills\req-code-consistency\references\vault"),
        "req-code-consistency 本地金库",
    )
    check_vault(
        Path(r"C:\Users\<user>\.cursor\skills\vault-base\references\vault"),
        "vault-base 底座金库",
    )
    print("\n规范摘要 (vault-base 3.1/3.2/3.6):")
    print("  - 普通知识: answers|decisions|references/{uuid4}.md")
    print("  - brief 例外: briefs/{skill_id}.md（语义化）")
    print("  - 样本: briefs/_samples/{skill_id}.sample-N.md")
    print("  - frontmatter 16 字段契约；正文四区块")


if __name__ == "__main__":
    main()
