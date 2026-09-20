#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""common_skills_repo.py — 用户级公共 skill 仓 + IDE 目录联接。

真相源：~/common-skills-repo/<skill>/
各 IDE：~/.cursor|claude|codex/skills/<skill> → junction 到真相源

用法：
  python common_skills_repo.py init
  python common_skills_repo.py migrate --skill vault-base --from-ide cursor
  python common_skills_repo.py link --skill vault-base --ides cursor,claude,codex
  python common_skills_repo.py status --skill vault-base
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

IDE_SKILLS = {
    "cursor": Path.home() / ".cursor" / "skills",
    "claude": Path.home() / ".claude" / "skills",
    "codex": Path.home() / ".codex" / "skills",
}


def default_common_root() -> Path:
    env = os.environ.get("COMMON_SKILLS_REPO")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / "common-skills-repo").resolve()


def skill_dir(common: Path, skill: str) -> Path:
    return common / skill


def _is_reparse_point(p: Path) -> bool:
    if not p.exists() and not p.is_symlink():
        # junction may show as exists
        try:
            return bool(p.stat().st_file_attributes & 0x400)  # type: ignore[attr-defined]
        except Exception:
            return False
    try:
        if p.is_symlink():
            return True
        # Windows junction: Path.is_junction in 3.12+
        if hasattr(p, "is_junction") and p.is_junction():
            return True
    except Exception:
        pass
    if sys.platform == "win32" and p.exists():
        try:
            out = subprocess.check_output(
                ["cmd", "/c", "dir", "/AL", str(p.parent)],
                text=True,
                encoding="gbk",
                errors="replace",
            )
            name = p.name
            for line in out.splitlines():
                if name in line and ("<JUNCTION>" in line or "<SYMLINKD>" in line):
                    return True
        except Exception:
            pass
    return False


def _resolve_link_target(p: Path) -> Path | None:
    try:
        if p.is_symlink():
            return p.resolve()
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["cmd", "/c", "dir", "/AL", str(p.parent)],
                text=True,
                encoding="gbk",
                errors="replace",
            )
            # 2026/01/01  <JUNCTION>     vault-base [C:\...\vault-base]
            for line in out.splitlines():
                if p.name in line and "[" in line and "]" in line:
                    tgt = line[line.rfind("[") + 1 : line.rfind("]")]
                    return Path(tgt)
        except Exception:
            pass
    return None


def ensure_common_root(common: Path) -> Path:
    common.mkdir(parents=True, exist_ok=True)
    readme = common / "README.md"
    if not readme.exists():
        readme.write_text(
            "# common-skills-repo\n\n"
            "用户级公共 Skill 仓（真相源）。\n\n"
            "- 各 IDE 的 `~/.cursor|claude|codex/skills/<name>` 应 **junction/联接** 到本目录下同名 skill。\n"
            "- 本地 Vault 默认在 `vault-base/references/vault`。\n"
            "- 环境变量可覆盖根路径：`COMMON_SKILLS_REPO`。\n"
            "- 管理脚本：`vault-base/scripts/common_skills_repo.py`。\n",
            encoding="utf-8",
        )
    return common


def cmd_init(args) -> int:
    common = Path(args.path).expanduser().resolve() if args.path else default_common_root()
    ensure_common_root(common)
    print("common_skills_repo:", common)
    return 0


def _robocopy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        # /E 含子目录；/XD 跳过无意义；/NFL 等安静
        cp = subprocess.run(
            [
                "robocopy",
                str(src),
                str(dst),
                "/E",
                "/XD",
                ".git",
                "/NFL",
                "/NDL",
                "/NJH",
                "/NJS",
                "/nc",
                "/ns",
                "/np",
            ],
            check=False,
        )
        # robocopy 0-7 成功
        if cp.returncode >= 8:
            raise RuntimeError("robocopy failed: %s" % cp.returncode)
    else:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, symlinks=True)


def _backup_path(p: Path) -> Path:
    bak = Path(str(p) + ".__migrated_bak__")
    n = 1
    while bak.exists():
        bak = Path(str(p) + ".__migrated_bak__.%d" % n)
        n += 1
    return bak


def create_junction(link: Path, target: Path) -> None:
    target = target.resolve()
    if not target.is_dir():
        raise FileNotFoundError("target missing: %s" % target)
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() or link.is_symlink() or _is_reparse_point(link):
        cur = _resolve_link_target(link)
        if cur and cur.resolve() == target.resolve():
            print("already linked:", link, "->", target)
            return
        raise FileExistsError("path exists, refuse overwrite: %s" % link)
    if sys.platform == "win32":
        cp = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=False,
            capture_output=True,
            text=True,
            encoding="gbk",
            errors="replace",
        )
        if cp.returncode != 0:
            raise RuntimeError("mklink failed: %s %s" % (cp.stdout, cp.stderr))
        print("junction:", link, "->", target)
    else:
        link.symlink_to(target, target_is_directory=True)
        print("symlink:", link, "->", target)


def cmd_migrate(args) -> int:
    common = Path(args.path).expanduser().resolve() if args.path else default_common_root()
    ensure_common_root(common)
    skill = args.skill
    dest = skill_dir(common, skill)
    if args.from_path:
        src = Path(args.from_path).expanduser().resolve()
    else:
        ide = args.from_ide or "cursor"
        src = IDE_SKILLS[ide] / skill
    if not src.is_dir():
        print("source missing:", src, file=sys.stderr)
        return 2
    if _is_reparse_point(src):
        tgt = _resolve_link_target(src)
        print("source already a link:", src, "->", tgt)
        if tgt and tgt.resolve() == dest.resolve() and dest.is_dir():
            print("already migrated")
            return 0
        print("refuse migrate from link; fix manually", file=sys.stderr)
        return 2
    if dest.exists() and not args.force:
        print("dest exists (use --force to refresh copy):", dest)
    else:
        if dest.exists() and args.force:
            # 只覆盖文件，不删整个树（保护 vault 数据）：仍用 robocopy
            pass
        print("copy:", src, "->", dest)
        _robocopy(src, dest)
    # 备份并移除源，便于随后 link
    if args.replace_source:
        bak = _backup_path(src)
        print("rename source ->", bak)
        src.rename(bak)
    print("common skill:", dest)
    return 0


def cmd_link(args) -> int:
    common = Path(args.path).expanduser().resolve() if args.path else default_common_root()
    dest = skill_dir(common, args.skill)
    if not dest.is_dir():
        print("common skill missing, migrate first:", dest, file=sys.stderr)
        return 2
    ides = [x.strip() for x in (args.ides or "cursor,claude,codex").split(",") if x.strip()]
    rc = 0
    for ide in ides:
        if ide not in IDE_SKILLS:
            print("unknown ide:", ide, file=sys.stderr)
            rc = 2
            continue
        link = IDE_SKILLS[ide] / args.skill
        try:
            if link.exists() and not _is_reparse_point(link):
                if args.backup_existing:
                    bak = _backup_path(link)
                    print("backup existing:", link, "->", bak)
                    try:
                        link.rename(bak)
                    except PermissionError:
                        # IDE 可能锁目录：尝试 cmd ren
                        cp = subprocess.run(
                            ["cmd", "/c", "ren", str(link), bak.name],
                            capture_output=True,
                            text=True,
                            encoding="gbk",
                            errors="replace",
                        )
                        if cp.returncode != 0 or link.exists():
                            print(
                                "LOCKED skip %s (close IDE tabs on this skill, then re-run link): %s"
                                % (ide, link),
                                file=sys.stderr,
                            )
                            rc = 1
                            continue
                else:
                    print(
                        "exists as real dir (pass --backup-existing):",
                        link,
                        file=sys.stderr,
                    )
                    rc = 2
                    continue
            create_junction(link, dest)
        except Exception as e:
            print("link failed %s: %s" % (ide, e), file=sys.stderr)
            rc = 1
    return rc


def cmd_status(args) -> int:
    common = Path(args.path).expanduser().resolve() if args.path else default_common_root()
    dest = skill_dir(common, args.skill)
    print("COMMON_SKILLS_REPO:", common)
    print("skill:", dest, "exists=" + str(dest.is_dir()))
    for ide, root in IDE_SKILLS.items():
        link = root / args.skill
        if not link.exists() and not _is_reparse_point(link):
            print("  %s: MISSING %s" % (ide, link))
            continue
        if _is_reparse_point(link):
            print("  %s: LINK %s -> %s" % (ide, link, _resolve_link_target(link)))
        else:
            print("  %s: REAL_DIR %s" % (ide, link))
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="common-skills-repo 管理")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--path", help="覆盖默认 ~/common-skills-repo")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("migrate")
    p.add_argument("--skill", default="vault-base")
    p.add_argument("--path")
    p.add_argument("--from-ide", choices=list(IDE_SKILLS))
    p.add_argument("--from-path")
    p.add_argument("--force", action="store_true")
    p.add_argument(
        "--replace-source",
        action="store_true",
        help="迁移后把源目录改名为 .__migrated_bak__",
    )
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("link")
    p.add_argument("--skill", default="vault-base")
    p.add_argument("--path")
    p.add_argument("--ides", default="cursor,claude,codex")
    p.add_argument(
        "--backup-existing",
        action="store_true",
        help="若 IDE 下已是实体目录则先改名为 bak 再联接",
    )
    p.set_defaults(func=cmd_link)

    p = sub.add_parser("status")
    p.add_argument("--skill", default="vault-base")
    p.add_argument("--path")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
