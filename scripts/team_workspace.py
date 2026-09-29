#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""team_workspace.py — 项目组私有目录 team-type:team-name。

逻辑 ID 例：test-team:amrd-test
磁盘路径：<skill_root>/teams/test-team/amrd-test/

Windows 禁止路径含 ':'，故用二级目录表达。
"""
from __future__ import annotations

import argparse
import datetime
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import skill_root  # noqa: E402

ID_RE = re.compile(r"^([a-z0-9][a-z0-9_-]*):([a-z0-9][a-z0-9_-]*)$", re.I)
TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$", re.I)


def teams_root() -> Path:
    return skill_root() / "teams"


def parse_id(team_id: str) -> tuple[str, str]:
    m = ID_RE.match((team_id or "").strip())
    if not m:
        raise ValueError(
            "id 格式须为 team-type:team-name，如 test-team:amrd-test（仅字母数字_-）"
        )
    return m.group(1), m.group(2)


def team_dir(team_type: str, team_name: str) -> Path:
    if not TYPE_RE.match(team_type) or not TYPE_RE.match(team_name):
        raise ValueError("team_type / team_name 仅允许 [a-zA-Z0-9_-]")
    return teams_root() / team_type / team_name


def logical_id(team_type: str, team_name: str) -> str:
    return "%s:%s" % (team_type, team_name)


def ensure_gitignore() -> Path:
    """保证 skill 根 .gitignore 含 teams 规则。"""
    gi = skill_root() / ".gitignore"
    block = (
        "# --- team workspaces (team-type:team-name → teams/<type>/<name>/) ---\n"
        "teams/**\n"
        "!teams/README.md\n"
        "!teams/_template/\n"
        "!teams/_template/**\n"
    )
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if "teams/**" not in existing:
        with gi.open("a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("\n" + block)
    return gi


def ensure_svn_ignore() -> dict:
    """对 teams/ 设 svn:ignore=*；无 SVN 工作副本则只写说明文件。"""
    root = teams_root()
    root.mkdir(parents=True, exist_ok=True)
    note = root / "svn-ignore.txt"
    note.write_text(
        "# 在 SVN 工作副本中于 teams/ 目录执行：\n"
        "#   svn propset svn:ignore \"*\" .\n"
        "# 或运行: python scripts/team_workspace.py fix-ignores\n"
        "# 效果：忽略未纳入版本的组目录（如 test-team/）；\n"
        "# 已 add 的 README.md、_template/ 仍受版本管理。\n",
        encoding="utf-8",
    )
    result = {"note": str(note), "svn_prop": None, "ok": True, "detail": ""}
    # teams 是否在 svn wc 内
    try:
        r = subprocess.run(
            ["svn", "info", str(skill_root())],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            result["detail"] = "skill 根不在 SVN 工作副本，已写 svn-ignore.txt 备查"
            return result
    except FileNotFoundError:
        result["detail"] = "本机无 svn 命令，已写 svn-ignore.txt 备查"
        return result

    # 确保 teams 本身被版本管理（才能设 prop）
    subprocess.run(
        ["svn", "add", "--depth=empty", str(root)],
        capture_output=True, text=True,
    )
    # README / _template 尽量纳入版本
    for keep in (root / "README.md", root / "_template"):
        if keep.exists():
            subprocess.run(
                ["svn", "add", "--force", str(keep)],
                capture_output=True, text=True,
            )

    prop = subprocess.run(
        ["svn", "propset", "svn:ignore", "*", str(root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    result["svn_prop"] = prop.returncode
    result["detail"] = (prop.stdout or prop.stderr or "").strip() or (
        "svn:ignore=* 已设在 teams/" if prop.returncode == 0 else "svn propset 失败"
    )
    result["ok"] = prop.returncode == 0
    return result


def init_team(team_type: str, team_name: str, force: bool = False) -> Path:
    ensure_gitignore()
    d = team_dir(team_type, team_name)
    d.mkdir(parents=True, exist_ok=True)
    tpl = teams_root() / "_template" / "team.yml"
    dest = d / "team.yml"
    if dest.exists() and not force:
        return d
    text = tpl.read_text(encoding="utf-8") if tpl.exists() else (
        'id: "{team_type}:{team_name}"\nteam_type: "{team_type}"\nteam_name: "{team_name}"\n'
    )
    text = (
        text.replace("{team_type}", team_type)
        .replace("{team_name}", team_name)
        .replace("{updated_at}", datetime.date.today().isoformat())
    )
    # fill updated_at if blank key remains
    if 'updated_at: ""' in text:
        text = text.replace(
            'updated_at: ""',
            'updated_at: "%s"' % datetime.date.today().isoformat(),
        )
    dest.write_text(text, encoding="utf-8")
    # optional notes stub
    notes = d / "NOTES.md"
    if not notes.exists():
        notes.write_text(
            "# %s\n\n逻辑 ID：`%s`\n\n（组内私有笔记，默认不被 Git/SVN 提交）\n"
            % (logical_id(team_type, team_name), logical_id(team_type, team_name)),
            encoding="utf-8",
        )
    return d


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="项目组私有工作区 team-type:team-name")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="创建 teams/<type>/<name>/")
    p_init.add_argument("--id", help="逻辑 ID，如 test-team:amrd-test")
    p_init.add_argument("--type", dest="team_type", help="team-type")
    p_init.add_argument("--name", dest="team_name", help="team-name")
    p_init.add_argument("--force", action="store_true", help="覆盖 team.yml")

    sub.add_parser("fix-ignores", help="补齐 .gitignore 与 svn:ignore")

    p_path = sub.add_parser("path", help="打印逻辑 ID 对应磁盘路径")
    p_path.add_argument("--id", required=True)

    args = ap.parse_args()
    try:
        if args.cmd == "fix-ignores":
            gi = ensure_gitignore()
            svn = ensure_svn_ignore()
            print("gitignore:", gi)
            print("svn:", svn)
            return 0 if svn.get("ok", True) else 1

        if args.cmd == "path":
            t, n = parse_id(args.id)
            print(team_dir(t, n))
            return 0

        if args.cmd == "init":
            if args.id:
                t, n = parse_id(args.id)
            elif args.team_type and args.team_name:
                t, n = args.team_type, args.team_name
            else:
                print("需要 --id team-type:team-name 或 --type 与 --name", file=sys.stderr)
                return 2
            d = init_team(t, n, force=args.force)
            ensure_svn_ignore()
            print("logical_id:", logical_id(t, n))
            print("path:", d)
            print("files:", ", ".join(sorted(x.name for x in d.iterdir())))
            return 0
    except ValueError as e:
        print("[error]", e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
