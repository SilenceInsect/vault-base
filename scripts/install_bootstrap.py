#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install_bootstrap.py — 安装编排：扫 IDE → 填清单 → 缺项提问 → 探测连接 → apply。

流水线（skill 必须按序引导，禁止跳过清单直接 env_ensure）：
  1. common_skills_repo init（若无仓）
  2. install_inventory init + IDE 回填
  3. 自动填充可确定项，输出 filled / uncertain
  4. missing --json → 逐项提问后 write-field
  5. probe-connection → 写回 install.mode（共享优先 / 本地降级）
  6. apply：shared_full 仅最小 secrets；local_only 跑 env_ensure
  7. link --ides auto
"""
from __future__ import annotations

import argparse
import datetime
import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import install_inventory as inv  # noqa: E402
from common_skills_repo import (  # noqa: E402
    default_common_root,
    detect_ide_links,
    ensure_common_root,
)
from team_workspace import logical_id, parse_id  # noqa: E402
from vault_paths import skill_root, vault_root  # noqa: E402
from vault_secrets import DEFAULT_REDIS, mysql_conf, redis_conf  # noqa: E402


def _run_py(script: str, args: list[str]) -> tuple[int, str]:
    cmd = [sys.executable, str(Path(__file__).resolve().parent / script)] + args
    cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (cp.stdout or "") + (("\n" + cp.stderr) if cp.stderr else "")
    return cp.returncode, out.strip()


def _tcp_ok(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _resolve_id(args) -> tuple[str, str]:
    if args.id:
        return parse_id(args.id)
    return args.team_type, args.team_name


def cmd_init(args) -> int:
    """建 common 仓 + 清单 init + IDE 扫描回填。"""
    common = default_common_root()
    ensure_common_root(common)
    print("common_skills_repo:", common)

    t, n = _resolve_id(args)
    lid = logical_id(t, n)
    force = ["--force"] if args.force else []
    rc, out = _run_py("install_inventory.py", ["init", "--id", lid] + force)
    print(out)
    if rc != 0 and "已存在" not in out and "exists" not in out.lower():
        # init 失败且非已存在
        if not inv.inventory_path(t, n).exists():
            return rc or 2

    # 再刷一次 IDE（init 已探测；显式刷新保证一致）
    _run_py("install_inventory.py", ["refresh-ide-links", "--id", lid])

    filled, uncertain = auto_fill(t, n)
    report = {
        "logical_id": lid,
        "inventory": str(inv.inventory_path(t, n)),
        "ide_links": detect_ide_links(),
        "filled": filled,
        "uncertain": uncertain,
        "next": "python scripts/install_bootstrap.py missing --id %s --json" % lid,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def auto_fill(team_type: str, team_name: str) -> tuple[list[str], list[str]]:
    """扫描可自动填充项写回清单；返回 (filled, uncertain)。"""
    path = inv.inventory_path(team_type, team_name)
    if not path.exists():
        return [], ["inventory_missing"]
    data = inv.load_yaml(path)
    filled: list[str] = []
    uncertain: list[str] = []

    common = inv.yaml_path(default_common_root())
    skill = inv.yaml_path(skill_root())
    vault = inv.yaml_path(Path(skill) / "references" / "vault")
    py = inv.yaml_path(sys.executable)

    defaults = {
        "common_skills_repo.path": common,
        "install.skill_root": skill,
        "install.python_exe": py,
        "shared_env.secrets_path": inv.yaml_path(Path(vault) / "_kb" / "secrets.local.json"),
    }
    # vault_root：shared_full 不强制；仅当已有目录时回填
    if Path(vault).is_dir():
        defaults["install.vault_root"] = vault

    # 共享主机默认（可改）
    if inv._empty(inv._get(data, "shared_env.redis.host")):
        defaults["shared_env.redis.host"] = DEFAULT_REDIS["host"]
    if inv._empty(inv._get(data, "shared_env.redis.port")):
        inv._set(data, "shared_env.redis.port", int(DEFAULT_REDIS["port"]))
        filled.append("shared_env.redis.port")

    for field, val in defaults.items():
        if inv._empty(inv._get(data, field)):
            inv._set(data, field, val)
            filled.append(field)

    # IDE 已在 refresh；这里只记录
    links = detect_ide_links()
    for ide, on in links.items():
        key = "common_skills_repo.ide_links.%s" % ide
        inv._set(data, key, bool(on))
        filled.append(key)

    # 模式：保持 shared_full 意图，除非已显式设为其他
    mode = inv._get(data, "install.mode")
    if inv._empty(mode):
        inv._set(data, "install.mode", "shared_full")
        filled.append("install.mode")

    # 不确定：作者、MySQL 账号等
    for f in ("local.author", "shared_env.mysql.host", "shared_env.mysql.user",
              "shared_env.mysql.database", "team.display_name"):
        if inv._empty(inv._get(data, f)):
            uncertain.append(f)

    inv._set(data, "interview.updated_at", datetime.date.today().isoformat())
    inv._set(data, "interview.status", "asked" if uncertain else "filled")
    inv._set(data, "interview.missing_fields", inv.missing_fields(data))

    if inv.yaml is None:
        print("WARN: 无 PyYAML，跳过 auto_fill 写回；请 pip install pyyaml", file=sys.stderr)
        return filled, uncertain
    inv.dump_yaml(data, path)
    return filled, uncertain


def cmd_missing(args) -> int:
    t, n = _resolve_id(args)
    lid = logical_id(t, n)
    extra = ["--json"] if args.json else []
    rc, out = _run_py("install_inventory.py", ["missing", "--id", lid] + extra)
    print(out)
    return rc


def cmd_write_field(args) -> int:
    t, n = _resolve_id(args)
    lid = logical_id(t, n)
    rc, out = _run_py(
        "install_inventory.py",
        ["write-field", "--id", lid, "--field", args.field, "--value", args.value],
    )
    print(out)
    return rc


def cmd_probe_connection(args) -> int:
    """探测共享 Redis/MySQL，写回 install.mode 与 checklist.probe_*。"""
    t, n = _resolve_id(args)
    path = inv.inventory_path(t, n)
    if not path.exists():
        print("清单不存在:", path, file=sys.stderr)
        return 2
    data = inv.load_yaml(path)

    # 用清单 secrets_path / vault 推导探测 root
    secrets = inv._get(data, "shared_env.secrets_path") or ""
    vroot = inv._get(data, "install.vault_root") or ""
    if secrets:
        sp = Path(str(secrets)).expanduser().resolve()
        kb = sp.parent
        root = kb.parent if kb.name == "_kb" else kb
    elif vroot:
        root = Path(str(vroot)).expanduser().resolve()
    else:
        root = vault_root()

    # 清单里的 host 优先写入临时探测（不改 secrets 文件）
    redis_host = inv._get(data, "shared_env.redis.host") or DEFAULT_REDIS["host"]
    redis_port = int(inv._get(data, "shared_env.redis.port") or DEFAULT_REDIS["port"])
    mysql_host = inv._get(data, "shared_env.mysql.host") or ""
    mysql_port = int(inv._get(data, "shared_env.mysql.port") or 3306)

    redis_tcp = _tcp_ok(str(redis_host), redis_port)
    mysql_tcp = bool(mysql_host) and _tcp_ok(str(mysql_host), mysql_port)

    # 有 secrets 时再做 auth 级探测
    redis_auth_ok = False
    mysql_auth_ok = False
    redis_detail: dict[str, Any] = {"host": redis_host, "port": redis_port, "tcp": redis_tcp}
    mysql_detail: dict[str, Any] = {"host": mysql_host, "port": mysql_port, "tcp": mysql_tcp}
    try:
        from env_probe import probe_mysql_full, probe_redis_full

        if redis_tcp:
            rr = probe_redis_full(root, 2.0)
            redis_detail.update(rr)
            redis_auth_ok = bool(rr.get("reachable") and rr.get("ping"))
        if mysql_tcp:
            mr = probe_mysql_full(root, 2.0)
            mysql_detail.update(mr)
            mysql_auth_ok = bool(mr.get("reachable"))
    except Exception as e:
        redis_detail["probe_error"] = "%s: %s" % (type(e).__name__, e)

    # 无 secrets 时：仅 TCP 双通也视为可走 shared 意图（凭据稍后补）
    conf_redis = redis_conf(root)
    conf_mysql = mysql_conf(root)
    has_secrets = bool(conf_redis.get("host")) and bool(conf_mysql and conf_mysql.get("host"))

    shared_ok = False
    if redis_auth_ok and mysql_auth_ok:
        shared_ok = True
    elif redis_tcp and mysql_tcp and has_secrets and redis_auth_ok:
        # MySQL 可能缺 pymysql：TCP+secrets 足够定 shared 意图
        shared_ok = True
    elif redis_tcp and mysql_tcp and args.allow_tcp_only:
        shared_ok = True

    mode = "shared_full" if shared_ok else "local_only"
    # 用户显式 local_then_shared 且共享 OK 时保留
    prev = str(inv._get(data, "install.mode") or "")
    if prev == "local_then_shared" and shared_ok:
        mode = "local_then_shared"

    inv._set(data, "install.mode", mode)
    inv._set(data, "checklist.probe_redis_ok", bool(redis_auth_ok or redis_tcp))
    inv._set(data, "checklist.probe_mysql_ok", bool(mysql_auth_ok or mysql_tcp))
    inv._set(data, "shared_env.redis.requirepass_set", redis_detail.get("requirepass_set"))
    inv._set(data, "shared_env.redis.supports_stream", redis_detail.get("supports_stream"))
    inv._set(data, "interview.updated_at", datetime.date.today().isoformat())

    if inv.yaml is not None:
        inv.dump_yaml(data, path)
    else:
        print("WARN: 无 PyYAML，mode 未写回文件", file=sys.stderr)

    report = {
        "mode": mode,
        "shared_ok": shared_ok,
        "redis": redis_detail,
        "mysql": mysql_detail,
        "vault_root_for_probe": str(root),
        "inventory": str(path),
        "decision": (
            "shared_full: Redis 为运行时真相源，本地仓可跳过"
            if mode == "shared_full"
            else (
                "local_then_shared: 显式双写"
                if mode == "local_then_shared"
                else "local_only: 降级到本地用户目录文件仓"
            )
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if shared_ok or mode == "local_only" else 1


def _ensure_minimal_secrets_dir(data: dict) -> Path:
    """shared_full：只建 _kb 目录骨架，不强制 Git/占位 MD。"""
    secrets = inv._get(data, "shared_env.secrets_path") or ""
    if secrets:
        sp = Path(str(secrets)).expanduser()
        sp.parent.mkdir(parents=True, exist_ok=True)
        return sp.parent  # _kb
    skill = inv._get(data, "install.skill_root") or str(skill_root())
    kb = Path(str(skill)) / "references" / "vault" / "_kb"
    kb.mkdir(parents=True, exist_ok=True)
    return kb


def cmd_apply(args) -> int:
    """按 mode 落地：shared 最小 secrets；local 全量 env_ensure；再 IDE link。"""
    t, n = _resolve_id(args)
    path = inv.inventory_path(t, n)
    if not path.exists():
        print("清单不存在:", path, file=sys.stderr)
        return 2
    data = inv.load_yaml(path)
    miss = inv.missing_fields(data)
    if miss and not args.force:
        print("清单仍缺必填项，先补齐:", ", ".join(miss), file=sys.stderr)
        print("或传 --force 跳过校验（不推荐）", file=sys.stderr)
        return 1

    mode = str(inv._get(data, "install.mode") or "shared_full")
    report: dict[str, Any] = {"mode": mode, "steps": []}

    if mode == "local_only" or mode == "local_then_shared":
        vroot = inv._get(data, "install.vault_root") or ""
        args_ee = []
        if vroot:
            args_ee = ["--vault", str(vroot)]
        rc, out = _run_py("env_ensure.py", args_ee)
        report["steps"].append({"env_ensure": rc, "out": out[:2000]})
        inv._set(data, "checklist.env_ensure_ok", rc == 0)
        inv._set(data, "checklist.can_write_local", rc == 0)
    else:
        # shared_full：跳过完整本地仓，仅最小 _kb
        kb = _ensure_minimal_secrets_dir(data)
        report["steps"].append({"minimal_kb": str(kb), "skipped_env_ensure_full_vault": True})
        inv._set(data, "checklist.env_ensure_ok", True)
        inv._set(data, "checklist.can_write_local", False)
        inv._set(data, "checklist.can_write_shared", False)  # gate 后再置 true

    # IDE 联接
    common = inv._get(data, "common_skills_repo.path") or str(default_common_root())
    rc_link, out_link = _run_py(
        "common_skills_repo.py",
        ["link", "--skill", "vault-base", "--path", str(common),
         "--ides", "auto", "--backup-existing"],
    )
    report["steps"].append({"ide_link": rc_link, "out": out_link[:1500]})
    inv._set(data, "checklist.ide_links_ready", rc_link == 0)
    inv._set(data, "checklist.common_repo_ready", True)
    inv._set(data, "checklist.team_workspace_created", True)

    if mode in ("shared_full", "local_then_shared") and not args.skip_gate:
        lid = logical_id(t, n)
        rc_g, out_g = _run_py(
            "install_gate.py",
            ["--id", lid, "--require-shared", "--json"],
        )
        report["steps"].append({"install_gate": rc_g, "out": out_g[:2000]})
        inv._set(data, "checklist.can_write_shared", rc_g == 0)

    inv._set(data, "interview.updated_at", datetime.date.today().isoformat())
    if inv.yaml is not None:
        inv.dump_yaml(data, path)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_run(args) -> int:
    """非交互编排：init → auto_fill → missing 摘要 → probe →（可选 apply）。"""
    rc = cmd_init(args)
    if rc != 0:
        return rc
    t, n = _resolve_id(args)
    lid = logical_id(t, n)
    print("--- missing ---")
    rc_m, out_m = _run_py("install_inventory.py", ["missing", "--id", lid, "--json"])
    print(out_m)
    print("--- probe-connection ---")

    class _A:
        pass

    a = _A()
    a.id = lid
    a.team_type = t
    a.team_name = n
    a.allow_tcp_only = bool(getattr(args, "allow_tcp_only", False))
    cmd_probe_connection(a)
    if args.apply:
        a.force = bool(args.force)
        a.skip_gate = bool(getattr(args, "skip_gate", False))
        return cmd_apply(a)
    print(
        "下一步: 按 missing.questions 逐项提问后 "
        "write-field；再 install_bootstrap.py apply --id %s" % lid
    )
    return 0 if rc_m in (0, 1) else rc_m


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="vault-base 安装编排（共享优先/本地降级）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_id(p):
        p.add_argument("--id", help="team-type:team-name")
        p.add_argument("--type", dest="team_type")
        p.add_argument("--name", dest="team_name")

    p = sub.add_parser("init", help="common 仓 + 清单 + IDE 扫描 + auto_fill")
    add_id(p)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("missing", help="列出缺项与提问话术")
    add_id(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_missing)

    p = sub.add_parser("write-field", help="写入清单字段")
    add_id(p)
    p.add_argument("--field", required=True)
    p.add_argument("--value", required=True)
    p.set_defaults(func=cmd_write_field)

    p = sub.add_parser("probe-connection", help="探测 Redis/MySQL 并写回 install.mode")
    add_id(p)
    p.add_argument(
        "--allow-tcp-only",
        action="store_true",
        help="仅 TCP 双通即定 shared_full（凭据可稍后补）",
    )
    p.set_defaults(func=cmd_probe_connection)

    p = sub.add_parser("apply", help="按 mode 落地本地仓/最小 secrets + IDE 联接")
    add_id(p)
    p.add_argument("--force", action="store_true", help="忽略缺项强制 apply")
    p.add_argument("--skip-gate", action="store_true")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("run", help="init + missing + probe（可选 --apply）")
    add_id(p)
    p.add_argument("--force", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--skip-gate", action="store_true")
    p.add_argument("--allow-tcp-only", action="store_true")
    p.set_defaults(func=cmd_run)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
