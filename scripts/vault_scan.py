#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_scan.py — 基于本地 Git diff 获取 Vault 增量变更。

设计要点：
  1. D（删除）类型不再调 rev-parse HEAD:path——目标文件在 HEAD 已不存在
  2. 加 -M 开启重命名检测，保留 old_path
  3. Redis 不可达时回退本地基线文件，输出 degraded=True
  4. 基线在【入队成功后】推进（由调用方 vault_queue 处理）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import load_config, vault_root                             # noqa: E402
from vault_secrets import redis_conn                                        # noqa: E402

IGNORE_DIRS = {".obsidian", "_kb", "_health"}
USER_ID = "user_01"
SKILL_ID = "game_test_skill"


def run_git(args: list[str], cwd: Path, check: bool = True) -> str:
    res = subprocess.run(["git"] + args, cwd=str(cwd),
                         capture_output=True, text=True, check=False)
    if check and res.returncode != 0:
        raise RuntimeError("git %s 失败: %s" % (" ".join(args), res.stderr.strip()))
    return res.stdout.strip()


def init_vault_git(vault: Path) -> None:
    """幂等初始化本地 Git 仓库（仅本地，不推远端）。"""
    if (vault / ".git").exists():
        return
    run_git(["init"], vault)
    run_git(["config", "user.name", "vault-auto"], vault)
    run_git(["config", "user.email", "vault-auto@local"], vault)
    gi = vault / ".gitignore"
    if not gi.exists():
        gi.write_text("_kb/*.db\n_kb/*.db-journal\n_kb/secrets.local.json\n"
                      "_kb/baseline.json\n__pycache__/\n*.pyc\n", encoding="utf-8")
    run_git(["add", "-A"], vault)
    run_git(["commit", "-m", "vault initial snapshot"], vault, check=False)


def head_commit(vault: Path) -> str:
    return run_git(["rev-parse", "HEAD"], vault)


def parse_name_status(out: str) -> list[dict]:
    """解析 `git diff --name-status -M` 输出。

    A/M/D: STATUS\\tpath
    R###:  R###\\told\\tnew
    """
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        code = parts[0][0].upper()
        if code == "R" and len(parts) >= 3:
            rows.append({"change_type": "R", "old_path": parts[1], "file_path": parts[2]})
        elif len(parts) >= 2:
            rows.append({"change_type": code, "old_path": None, "file_path": parts[1]})
    return rows


def changed_files(base_commit: str, vault: Path) -> list[dict]:
    raw = run_git(["diff", "--name-status", "-M", base_commit, "HEAD"], vault)
    out = []
    for r in parse_name_status(raw):
        p = Path(r["file_path"])
        if p.parts and p.parts[0] in IGNORE_DIRS:
            continue
        if p.suffix != ".md":
            continue

        blob = None
        if r["change_type"] != "D":
            # 只有非删除类型才能从 HEAD 取 blob；D 类型文件已不存在
            blob = run_git(["rev-parse", "HEAD:%s" % r["file_path"]],
                           vault, check=False) or None

        out.append({
            "event_id": event_id(USER_ID, r["change_type"], r["file_path"], blob),
            "user_id": USER_ID,
            "skill_id": SKILL_ID,
            "doc_uuid": p.stem,
            "change_type": r["change_type"],
            "old_path": r["old_path"],
            "file_path": r["file_path"],
            "blob_hash": blob,
        })
    return out


def event_id(user: str, change: str, path: str, blob: str | None) -> str:
    """幂等键：同一用户同一变更只产生一个 id。"""
    key = "%s|%s|%s|%s" % (user, change, path, blob or "")
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:32]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-advance", action="store_true",
                    help="只输出增量，不推进基线（调试用）")
    args = ap.parse_args()

    vault = vault_root(args.vault)
    cfg = load_config(vault)
    init_vault_git(vault)
    cur = head_commit(vault)

    snap_key = "snapshot:git_commit:%s:%s" % (USER_ID, SKILL_ID)
    degraded, base = False, None

    r = redis_conn(vault, timeout=cfg["degrade"]["probe_timeout_ms"] / 1000.0)
    if r is not None:
        try:
            base = r.cmd("GET", snap_key)
        except Exception:
            degraded = True
    else:
        degraded = True

    if base is None:
        # 回退本地基线
        local = vault / "_kb" / "baseline.json"
        if local.exists():
            try:
                base = json.loads(local.read_text(encoding="utf-8")).get(snap_key)
            except Exception:
                base = None

    if base is None:
        # 首次运行：把当前 HEAD 作为基线，无可比增量
        advance = not args.no_advance
        if advance:
            save_baseline(vault, r, snap_key, cur)
        print(json.dumps({"degraded": degraded, "advance": advance,
                          "base": None, "head": cur, "changes": []},
                         ensure_ascii=False, indent=2))
        return 0

    if base == cur:
        print(json.dumps({"degraded": degraded, "advance": False,
                          "base": base, "head": cur, "changes": []},
                         ensure_ascii=False, indent=2))
        return 0

    # 基线必须仍是 HEAD 的祖先；否则说明发生过 rebase / amend / 分支切换，
    # `git diff base HEAD` 的结果不再可信，标记 baseline_broken 交由上层决策。
    baseline_broken = not is_ancestor(base, vault)
    if baseline_broken:
        print(json.dumps({"degraded": degraded, "baseline_broken": True,
                          "base": base, "head": cur, "changes": []},
                         ensure_ascii=False, indent=2))
        return 3   # 退出码 3 = 需要人工跑 vault_admin.py doctor

    changes = changed_files(base, vault)
    print(json.dumps({"degraded": degraded, "baseline_broken": False, "base": base,
                      "head": cur, "changes": changes}, ensure_ascii=False, indent=2))
    return 0


def is_ancestor(base: str, vault: Path) -> bool:
    """base 是否为 HEAD 的祖先。returncode 0 = 是，1 = 否，其他 = 出错。"""
    res = subprocess.run(["git", "merge-base", "--is-ancestor", base, "HEAD"],
                         cwd=str(vault), capture_output=True, text=True)
    return res.returncode == 0


def save_baseline(vault: Path, r, key: str, commit: str) -> None:
    if r is not None:
        try:
            r.cmd("SET", key, commit)
            return
        except Exception:
            pass
    (vault / "_kb").mkdir(parents=True, exist_ok=True)
    p = vault / "_kb" / "baseline.json"
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data[key] = commit
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
