#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""env_ensure.py — skill 首段的幂等自举。

只做「能自动做的」：补 pip 依赖、建目录、git init、写 meta。
装系统级组件（Redis/MySQL）不在此列——那是运维职责。
"""
from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import ensure_dirs, vault_root, write_default_config, write_meta  # noqa: E402
from vault_scan import init_vault_git, run_git                                      # noqa: E402

PIP_PACKAGES = {"pymysql": "pymysql"}


def ensure_package(mod: str, pkg: str) -> tuple[bool, str]:
    try:
        importlib.import_module(mod)
        return True, "already"
    except ImportError:
        pass
    res = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", pkg],
                         capture_output=True, text=True)
    if res.returncode != 0:
        return False, res.stderr.strip()[:300]
    try:
        importlib.invalidate_caches()
        importlib.import_module(mod)
        return True, "installed"
    except ImportError as e:
        return False, str(e)


def ensure_git(root: Path) -> dict:
    """幂等 git init + 空仓库时做初始提交。"""
    init_vault_git(root)
    has_head = False
    try:
        run_git(["rev-parse", "HEAD"], root)
        has_head = True
    except RuntimeError:
        pass
    if not has_head:
        run_git(["add", "-A"], root)
        run_git(["commit", "-m", "vault initial snapshot"], root, check=False)
        try:
            run_git(["rev-parse", "HEAD"], root)
            has_head = True
        except RuntimeError:
            pass
    return {"git_dir": str(root / ".git"), "has_head": has_head}


def ensure_placeholder_md(root: Path) -> Path | None:
    """若 vault 内无任何 .md，写入占位 answers/_README.md 供 R1 降级通过。"""
    existing = list(root.rglob("*.md"))
    if existing:
        return None
    p = root / "answers" / "_README.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(
            "---\nschema_version: 1\nuuid: \"00000000-0000-4000-8000-000000000001\"\n"
            "doc_type: reference\ntitle: Vault 占位说明\n---\n"
            "# Vault 占位说明\n\n"
            "此文件由 env_ensure 自动创建，确保 R1 门禁在空库时可降级通过。\n"
            "请用 vault_dump 或手工笔记替换为真实内容。\n",
            encoding="utf-8",
        )
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description="Vault 环境幂等自举")
    ap.add_argument("--vault", help="Vault 根目录（默认从 VAULT_ROOT 或 skill 推导）")
    args = ap.parse_args()

    root = ensure_dirs(vault_root(args.vault))
    report = {
        "vault": str(root),
        "packages": {},
        "config": "",
        "git": {},
        "placeholder": None,
        "meta": "",
        "ok": True,
    }

    for mod, pkg in PIP_PACKAGES.items():
        ok, how = ensure_package(mod, pkg)
        report["packages"][mod] = {"ok": ok, "how": how}
        if not ok:
            report["ok"] = False

    report["config"] = str(write_default_config(root))
    report["git"] = ensure_git(root)
    ph = ensure_placeholder_md(root)
    report["placeholder"] = str(ph) if ph else None
    report["meta"] = str(write_meta(root, skill="vault-base"))

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
