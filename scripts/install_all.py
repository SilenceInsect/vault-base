#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install_all.py — vault-base 一站式安装（一次决策，一条命令）。

推荐（交互，只决策一次）：

  python scripts/install_all.py

续装（同一条命令，自动读上次选择，不再提问）：

  python scripts/install_all.py

CI / 已有 choices 文件时可非交互；高级覆盖仍支持 CLI 旗标，但日常不必记。

决策落盘：teams/<type>/<name>/install-choices.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import install_flow as flow  # noqa: E402
import install_inventory as inv  # noqa: E402
from mcp_install import install_mcp  # noqa: E402
from team_workspace import parse_id, team_dir  # noqa: E402
from vault_paths import skill_root, vault_root  # noqa: E402

CHOICES_NAME = "install-choices.json"


def _run_py(script: str, args: list[str]) -> tuple[int, str]:
    return flow._run_py(script, args)


def _load_defaults() -> dict:
    p = skill_root() / "assets" / "install-all.defaults.yml"
    if inv.yaml and p.is_file():
        return inv.load_yaml(p) or {}
    return {}


def choices_path(lid: str) -> Path:
    t, n = parse_id(lid)
    return team_dir(t, n) / CHOICES_NAME


def load_choices(lid: str) -> dict | None:
    p = choices_path(lid)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_choices(lid: str, choices: dict) -> Path:
    p = choices_path(lid)
    p.parent.mkdir(parents=True, exist_ok=True)
    choices = dict(choices)
    choices["schema_version"] = 1
    choices["logical_id"] = lid
    choices["updated_at"] = flow._now()
    p.write_text(json.dumps(choices, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def _ask(msg: str, default: str = "") -> str:
    if not sys.stdin.isatty():
        return default
    hint = " [%s]" % default if default else ""
    try:
        ans = input("%s%s: " % (msg, hint)).strip()
    except EOFError:
        return default
    return ans if ans else default


def _ask_yes(msg: str, default: bool = True) -> bool:
    if not sys.stdin.isatty():
        return default
    suf = " [Y/n] " if default else " [y/N] "
    try:
        ans = input(msg + suf).strip().lower()
    except EOFError:
        return default
    if not ans:
        return default
    return ans in ("y", "yes", "是")


def secrets_already_ok() -> bool:
    dest = vault_root() / "_kb" / "secrets.local.json"
    if dest.is_file():
        return True
    if os.environ.get("VAULT_SECRETS") and Path(os.environ["VAULT_SECRETS"]).expanduser().is_file():
        return True
    if os.environ.get("VAULT_MYSQL_HOST") or os.environ.get("VAULT_REDIS_HOST"):
        return True
    return False


def decide_once(
    *,
    defaults: dict,
    cli: argparse.Namespace,
    force_redecide: bool,
) -> tuple[str, dict]:
    """一次决策：优先已有 choices；否则交互问齐；CI 用 defaults+CLI。

    返回 (lid, choices)。
    """
    # 1) 若 CLI 显式给了足够参数且 --non-interactive / 无 TTY → 不访谈
    # 2) 若已有 choices 且未 --redecide → 直接用
    # 3) 否则交互一次问完

    interactive = (
        not cli.non_interactive
        and sys.stdin.isatty()
        and not cli.assume_defaults
    )

    # 探测「是否已有可续装的 choices」：用默认 id 或 CLI id
    probe_id = cli.id or defaults.get("id") or "test-team:amrd-test"
    existing = None if force_redecide else load_choices(probe_id)

    # 若用户没传 --id，但磁盘上只有一份 choices，可自动续上
    if existing is None and not cli.id and not force_redecide:
        existing = _find_any_choices()

    if existing and not force_redecide:
        lid = str(existing.get("logical_id") or probe_id)
        # CLI 覆盖单项（可选）
        ch = dict(existing)
        if cli.secrets:
            ch["secrets"] = cli.secrets
        if cli.local_only:
            ch["mode"] = "local_only"
        if cli.no_mcp:
            ch["enable_mcp"] = False
        if cli.enable_mcp_flag:
            ch["enable_mcp"] = True
        ch["yes"] = True  # 续装默认继续自动同意副作用
        save_choices(lid, ch)
        return lid, ch

    # —— 需要新决策 ——
    if interactive:
        print("")
        print("vault-base 一站式安装 · 只需决策一次（答案会保存，续装不再问）")
        print("-" * 56)
        lid = _ask(
            "1) 团队 ID (team-type:team-name)",
            str(defaults.get("id") or "test-team:amrd-test"),
        )
        mode_default = str(defaults.get("mode") or "shared_full")
        mode_ans = _ask(
            "2) 安装模式  shared=共享库  local=仅本地",
            "shared" if mode_default != "local_only" else "local",
        ).lower()
        local_only = mode_ans.startswith("l")
        secrets = ""
        if not local_only:
            if secrets_already_ok():
                print("3) 凭据：已检测到本机 secrets / 环境变量，跳过")
            else:
                secrets = _ask(
                    "3) 本机 secrets.local.json 路径（共享模式必填；可回车稍后配）",
                    "",
                )
        enable_mcp = _ask_yes("4) 安装结束后启用最小 MCP？", bool(defaults.get("enable_mcp", True)))
        print("-" * 56)
        print("将自动同意 pip 安装 / DDL（若缺表）/ 业务 skill 适配。")
        if not _ask_yes("确认按上述选择开始安装？", True):
            raise SystemExit(json.dumps({"ok": False, "error": "用户取消"}, ensure_ascii=False))
        choices = {
            "logical_id": lid,
            "mode": "local_only" if local_only else "shared_full",
            "secrets": secrets or None,
            "enable_mcp": enable_mcp,
            "adapt_all": bool(defaults.get("adapt_all", True)),
            "include_optional": bool(defaults.get("include_optional", False)),
            "mcp_global": bool(defaults.get("mcp_global", False)),
            "link_ides": str(defaults.get("link_ides") or "auto"),
            "yes": True,
            "source": "wizard",
        }
        save_choices(lid, choices)
        return lid, choices

    # 非交互：defaults + CLI
    lid = cli.id or defaults.get("id") or "test-team:amrd-test"
    local_only = bool(cli.local_only) or str(defaults.get("mode") or "") == "local_only"
    enable_mcp = bool(defaults.get("enable_mcp", True))
    if cli.no_mcp:
        enable_mcp = False
    if cli.enable_mcp_flag:
        enable_mcp = True
    choices = {
        "logical_id": lid,
        "mode": "local_only" if local_only else "shared_full",
        "secrets": cli.secrets or defaults.get("secrets_path") or None,
        "enable_mcp": enable_mcp,
        "adapt_all": (not cli.no_adapt_all) and bool(defaults.get("adapt_all", True)),
        "include_optional": bool(cli.include_optional or defaults.get("include_optional")),
        "mcp_global": bool(cli.mcp_global or defaults.get("mcp_global")),
        "link_ides": cli.link_ides or defaults.get("link_ides") or "auto",
        "yes": True if (cli.yes or cli.assume_defaults) else bool(defaults.get("yes")),
        "source": "defaults+cli",
    }
    if not choices["yes"] and cli.non_interactive:
        # CI 无同意则无法过 pip/DDL/adapt 门
        choices["yes"] = True
    save_choices(lid, choices)
    return lid, choices


def _find_any_choices() -> dict | None:
    """teams/*/*/install-choices.json 若恰好一份则用于续装。"""
    root = skill_root() / "teams"
    if not root.is_dir():
        return None
    found: list[dict] = []
    for p in root.glob("*/*/install-choices.json"):
        try:
            found.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    if len(found) == 1:
        return found[0]
    return None


def ensure_secrets(
    *,
    secrets_src: str | None,
    local_only: bool,
    interactive: bool,
) -> dict:
    dest = vault_root() / "_kb" / "secrets.local.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"dest": str(dest), "action": "none"}

    if dest.is_file():
        report["action"] = "exists"
        report["ok"] = True
        return report

    src = None
    if secrets_src:
        src = Path(str(secrets_src)).expanduser().resolve()
    elif os.environ.get("VAULT_SECRETS"):
        src = Path(os.environ["VAULT_SECRETS"]).expanduser().resolve()

    if src and src.is_file():
        shutil.copy2(src, dest)
        report["action"] = "copied"
        report["from"] = str(src)
        report["ok"] = True
        return report

    mysql_host = os.environ.get("VAULT_MYSQL_HOST") or ""
    redis_host = os.environ.get("VAULT_REDIS_HOST") or ""
    if mysql_host or redis_host:
        data = {
            "sections": {
                "mysql_shared": {
                    "host": mysql_host or redis_host,
                    "port": os.environ.get("VAULT_MYSQL_PORT", "3306"),
                    "user": os.environ.get("VAULT_MYSQL_USER", ""),
                    "password": os.environ.get("VAULT_MYSQL_PASSWORD", ""),
                    "database": os.environ.get("VAULT_MYSQL_DATABASE", "amrd_qa_vault"),
                }
            },
            "redis": {
                "host": redis_host or mysql_host,
                "port": int(os.environ.get("VAULT_REDIS_PORT", "6379")),
                "db": 0,
                "password": os.environ.get("VAULT_REDIS_PASSWORD", ""),
            },
            "default_section": "mysql_shared",
        }
        dest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["action"] = "synthesized_from_env"
        report["ok"] = True
        return report

    if local_only:
        example = skill_root() / "assets" / "secrets.local.json.example"
        if example.is_file():
            shutil.copy2(example, dest)
            report["action"] = "copied_example_local_only"
            report["ok"] = True
            report["warn"] = "local_only：占位 secrets，共享不可用"
            return report

    report["ok"] = False
    report["error"] = (
        "缺少 secrets。请重跑并在向导第 3 问填写路径，或设置 VAULT_SECRETS / "
        "VAULT_MYSQL_HOST+VAULT_REDIS_HOST，或改选 local 模式。"
    )
    report["example"] = str(skill_root() / "assets" / "secrets.local.json.example")
    if interactive and sys.stdin.isatty():
        path = _ask("补填 secrets.local.json 路径（回车放弃）", "")
        if path:
            p = Path(path).expanduser().resolve()
            if p.is_file():
                shutil.copy2(p, dest)
                report["action"] = "copied_interactive"
                report["from"] = str(p)
                report["ok"] = True
                report.pop("error", None)
    return report


def link_ides(ides: str = "auto") -> dict:
    common = os.environ.get("COMMON_SKILLS_REPO") or str(
        Path.home() / "common-skills-repo"
    )
    rc0, out0 = _run_py("common_skills_repo.py", ["init", "--path", common])
    rc1, out1 = _run_py(
        "common_skills_repo.py",
        [
            "link", "--skill", "vault-base", "--path", common,
            "--ides", ides or "auto", "--backup-existing",
        ],
    )
    return {
        "ok": rc1 == 0,
        "common": common,
        "init_rc": rc0,
        "link_rc": rc1,
        "out": (out1 or out0)[-1500:],
    }


def bootstrap_init(lid: str, force: bool = False) -> dict:
    args = ["init", "--id", lid]
    if force:
        args.append("--force")
    rc, out = _run_py("install_bootstrap.py", args)
    try:
        body = json.loads(out)
    except Exception:
        body = {"raw": (out or "")[-2000:]}
    return {"ok": rc == 0, "rc": rc, "result": body}


def inventory_missing(lid: str) -> dict:
    rc, out = _run_py("install_inventory.py", ["missing", "--id", lid, "--json"])
    try:
        return json.loads(out)
    except Exception:
        return {"raw": (out or "")[-2000:], "rc": rc}


def run_phases(
    lid: str,
    *,
    yes: bool,
    include_optional: bool,
    adapt_all: bool,
    interactive: bool,
    max_steps: int = 24,
) -> dict:
    t, n = parse_id(lid)
    steps: list[dict] = []
    for i in range(max_steps):
        plan = flow.resume_plan(
            t, n, lid,
            yes=yes,
            include_optional=include_optional,
            adapt_all=adapt_all,
        )
        if plan.get("done"):
            return {"ok": True, "done": True, "steps": steps, "final": plan}

        if plan.get("needs_input"):
            return {
                "ok": False,
                "done": False,
                "blocked": "waiting_input",
                "missing": inventory_missing(lid),
                "steps": steps,
                "hint": "清单缺项：用同一条 install_all 不会自动编造；请 write-field 后重跑 python scripts/install_all.py",
            }

        step_yes = yes
        if plan.get("needs_consent") and not yes:
            if interactive and _ask_yes(
                "阶段 %s 需要同意副作用，继续？" % plan.get("next_phase"),
                False,
            ):
                step_yes = True
                yes = True
                ch = load_choices(lid) or {}
                ch["yes"] = True
                save_choices(lid, ch)
            else:
                return {
                    "ok": False,
                    "done": False,
                    "blocked": "waiting_consent",
                    "plan": plan,
                    "steps": steps,
                    "hint": "重跑: python scripts/install_all.py   （会沿用 choices 并继续）",
                }

        class _A:
            pass

        a = _A()
        a.id = lid
        a.team_type = None
        a.team_name = None
        a.execute = True
        a.yes = step_yes
        a.include_optional = include_optional
        a.no_adapt_all = not adapt_all

        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = flow.cmd_resume(a)
        text = buf.getvalue()
        try:
            body = json.loads(text)
        except Exception:
            body = {"raw": text[-3000:], "rc": rc}
        steps.append({"i": i, "phase": plan.get("next_phase"), "rc": rc, "yes": step_yes, "result": body})

        if rc != 0:
            nxt = flow.resume_plan(
                t, n, lid, yes=yes,
                include_optional=include_optional, adapt_all=adapt_all,
            )
            if nxt.get("needs_consent") and not yes:
                return {
                    "ok": False, "done": False, "blocked": "waiting_consent",
                    "plan": nxt, "steps": steps,
                    "hint": "重跑: python scripts/install_all.py",
                }
            if nxt.get("needs_input"):
                return {
                    "ok": False, "done": False, "blocked": "waiting_input",
                    "missing": inventory_missing(lid), "steps": steps,
                }
            if nxt.get("done"):
                return {"ok": True, "done": True, "steps": steps, "final": nxt}
            return {"ok": False, "done": False, "blocked": "phase_failed", "steps": steps, "last_rc": rc}

    return {"ok": False, "done": False, "blocked": "max_steps", "steps": steps}


def record_mcp_manifest(lid: str, mcp_report: dict) -> None:
    t, n = parse_id(lid)
    man = flow.load_manifest(t, n)
    man["mcp"] = {
        "enabled": bool(mcp_report.get("ok")),
        "server": mcp_report.get("server"),
        "configs": [
            c.get("path")
            for step in (mcp_report.get("steps") or [])
            for c in (step.get("mcp_json") or [])
            if isinstance(c, dict)
        ],
        "at": flow._now(),
    }
    flow.save_manifest(t, n, man)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    defaults = _load_defaults()
    ap = argparse.ArgumentParser(
        description="vault-base 一站式安装：默认无参，一次决策后自动跑完；续装同命令。",
        epilog="日常只需: python scripts/install_all.py",
    )
    ap.add_argument("--id", default=None, help="可选；不传则向导询问或读已有 choices")
    ap.add_argument("--redecide", action="store_true", help="忽略已有 choices，重新决策一次")
    ap.add_argument("--assume-defaults", action="store_true",
                    help="非交互：直接用 defaults.yml（CI）")
    ap.add_argument("--yes", "-y", action="store_true", help="兼容旧用法；向导里已含同意")
    ap.add_argument("--secrets", help="可选覆盖 secrets 路径")
    ap.add_argument("--local-only", action="store_true")
    ap.add_argument("--adapt-all", action="store_true", default=None)
    ap.add_argument("--no-adapt-all", action="store_true")
    ap.add_argument("--include-optional", action="store_true")
    ap.add_argument("--enable-mcp", dest="enable_mcp_flag", action="store_true")
    ap.add_argument("--no-mcp", action="store_true")
    ap.add_argument("--mcp-global", action="store_true")
    ap.add_argument("--link-ides", default=None)
    ap.add_argument("--skip-link", action="store_true")
    ap.add_argument("--skip-phases", action="store_true")
    ap.add_argument("--force-bootstrap", action="store_true")
    ap.add_argument("--non-interactive", action="store_true")
    args = ap.parse_args()

    try:
        lid, choices = decide_once(
            defaults=defaults, cli=args, force_redecide=bool(args.redecide),
        )
        parse_id(lid)
    except SystemExit as e:
        if e.code not in (0, None):
            if isinstance(e.code, str):
                print(e.code)
                return 1
            return int(e.code) if isinstance(e.code, int) else 1
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)},
                         ensure_ascii=False, indent=2))
        return 2

    yes = bool(choices.get("yes", True))
    adapt_all = bool(choices.get("adapt_all", True))
    if args.no_adapt_all:
        adapt_all = False
    if args.adapt_all is True:
        adapt_all = True
    enable_mcp = bool(choices.get("enable_mcp", True))
    local_only = str(choices.get("mode") or "") == "local_only"
    include_optional = bool(choices.get("include_optional") or args.include_optional)
    link_ides_v = args.link_ides or choices.get("link_ides") or "auto"
    secrets_src = args.secrets or choices.get("secrets")
    interactive = (not args.non_interactive) and sys.stdin.isatty() and not args.assume_defaults

    report: dict[str, Any] = {
        "ok": False,
        "logical_id": lid,
        "skill_root": str(skill_root()),
        "choices_path": str(choices_path(lid)),
        "choices": {
            "mode": choices.get("mode"),
            "enable_mcp": enable_mcp,
            "adapt_all": adapt_all,
            "has_secrets_arg": bool(secrets_src),
            "source": choices.get("source"),
        },
        "steps": {},
    }

    if not args.skip_link:
        link = link_ides(str(link_ides_v))
        report["steps"]["link"] = link
        if not link.get("ok"):
            report["error"] = "IDE link failed"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2

    sec = ensure_secrets(
        secrets_src=secrets_src if isinstance(secrets_src, str) else None,
        local_only=local_only,
        interactive=interactive,
    )
    report["steps"]["secrets"] = sec
    if not sec.get("ok") and not local_only:
        report["hint"] = "python scripts/install_all.py --redecide"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    # 若向导补填了 secrets，写回 choices
    if sec.get("ok") and sec.get("from") and not choices.get("secrets"):
        choices["secrets"] = sec.get("from")
        save_choices(lid, choices)

    boot = bootstrap_init(lid, force=args.force_bootstrap)
    report["steps"]["bootstrap"] = boot

    if local_only:
        t, n = parse_id(lid)
        path = inv.inventory_path(t, n)
        if path.is_file() and inv.yaml:
            data = inv.load_yaml(path)
            inv._set(data, "install.mode", "local_only")
            inv.dump_yaml(data, path)

    if args.skip_phases:
        report["steps"]["phases"] = {"skipped": True}
    else:
        phases = run_phases(
            lid,
            yes=yes,
            include_optional=include_optional,
            adapt_all=adapt_all,
            interactive=interactive,
        )
        report["steps"]["phases"] = phases
        if not phases.get("ok"):
            report["ok"] = False
            report["blocked"] = phases.get("blocked")
            report["resume"] = "python scripts/install_all.py"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1

    if enable_mcp:
        mcp = install_mcp(
            root=skill_root(),
            also_global=bool(choices.get("mcp_global") or args.mcp_global),
        )
        report["steps"]["mcp"] = mcp
        try:
            record_mcp_manifest(lid, mcp)
        except Exception as e:
            report["steps"]["mcp_manifest_error"] = "%s: %s" % (type(e).__name__, e)
    else:
        report["steps"]["mcp"] = {"skipped": True}

    rc_r, out_r = _run_py("readiness_gate.py", ["--quick", "--json"])
    try:
        ready = json.loads(out_r)
    except Exception:
        ready = {"raw": out_r[-1000:], "rc": rc_r}
    report["steps"]["readiness"] = ready
    report["ok"] = True
    report["message"] = (
        "安装完成。续装/重跑仍用: python scripts/install_all.py"
        + (" ；请刷新 Cursor MCP。" if enable_mcp else "")
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
