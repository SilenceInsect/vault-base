#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install_flow.py — vault-base 七阶段安装编排（可断档续装）。

阶段：
  1 team-select     选团队；amrd-test 复用默认 Redis/MySQL，否则问答写配置
  2 env-check       列 Python/laya/stuntd/依赖版本与待下载清单；--approve-install 后安装
  3 db-connect      测 Redis/MySQL；检查/可选建立库与表（--approve-ddl）
  4 skill-list      扫本机 skill 清单；--approve-adapt 后改造
  5 harness         写流程 harness + install-manifest（卸载/更新目录）
  6 smoke           amrd-test 冒烟 + WebPlatform 归档/人审/Laya 检查清单
  7（贯穿）progress 安装日志 teams/<type>/<name>/install-progress.json

用法：
  python install_flow.py status --id test-team:amrd-test
  python install_flow.py phase1-team --id test-team:amrd-test
  python install_flow.py phase2-env --id test-team:amrd-test [--approve-install]
  python install_flow.py phase3-db --id test-team:amrd-test [--approve-ddl]
  python install_flow.py phase4-skills --id test-team:amrd-test [--approve-adapt] [--skill-id X]
  python install_flow.py phase5-harness --id test-team:amrd-test
  python install_flow.py phase6-smoke --id test-team:amrd-test
  python install_flow.py resume --id test-team:amrd-test
  python install_flow.py uninstall-plan --id test-team:amrd-test
  python install_flow.py uninstall --id test-team:amrd-test --approve
"""
from __future__ import annotations

import argparse
import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import install_bootstrap as boot  # noqa: E402
import install_inventory as inv  # noqa: E402
from team_workspace import logical_id, parse_id, team_dir  # noqa: E402
from vault_paths import skill_root, vault_root  # noqa: E402
from vault_secrets import DEFAULT_REDIS, load_secrets, mysql_conf, redis_conf  # noqa: E402

PHASES = [
    "1_team",
    "2_env",
    "3_db",
    "4_skills",
    "5_harness",
    "6_smoke",
]

AMRD_LOGICAL = "test-team:amrd-test"

ENV_PACKAGES = [
    {"key": "pymysql", "pip": "pymysql", "import": "pymysql", "required": True, "note": ""},
    {"key": "pyyaml", "pip": "pyyaml", "import": "yaml", "required": True, "note": ""},
    {"key": "redis", "pip": "redis", "import": "redis", "required": False, "note": "可选客户端"},
    {"key": "laya", "pip": "laya[serve]", "import": "laya", "required": False,
     "note": "可选；WebPlatform Laya / laya-serve"},
    {"key": "stuntd", "pip": "stuntd[train]", "import": "stuntd", "required": False,
     "note": "可选；本地决策训练 sidecar"},
]


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _scripts() -> Path:
    return Path(__file__).resolve().parent


def _run_py(script: str, args: list[str]) -> tuple[int, str]:
    cmd = [sys.executable, str(_scripts() / script)] + args
    cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (cp.stdout or "") + (("\n" + cp.stderr) if cp.stderr else "")
    return cp.returncode, out.strip()


def _resolve(args) -> tuple[str, str, str]:
    if args.id:
        t, n = parse_id(args.id)
    else:
        t, n = args.team_type, args.team_name
    return t, n, logical_id(t, n)


def progress_path(t: str, n: str) -> Path:
    return team_dir(t, n) / "install-progress.json"


def manifest_path(t: str, n: str) -> Path:
    return team_dir(t, n) / "install-manifest.json"


def load_progress(t: str, n: str) -> dict:
    p = progress_path(t, n)
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "logical_id": logical_id(t, n),
        "phases": {k: {"status": "pending", "at": "", "detail": {}} for k in PHASES},
        "log": [],
    }


def save_progress(t: str, n: str, data: dict) -> Path:
    p = progress_path(t, n)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def log_event(t: str, n: str, phase: str, event: str, detail: Any = None) -> dict:
    data = load_progress(t, n)
    entry = {"ts": _now(), "phase": phase, "event": event, "detail": detail}
    data.setdefault("log", []).append(entry)
    # keep last 200
    data["log"] = data["log"][-200:]
    save_progress(t, n, data)
    return data


def mark_phase(t: str, n: str, phase: str, status: str, detail: Any = None) -> dict:
    data = load_progress(t, n)
    data["phases"][phase] = {"status": status, "at": _now(), "detail": detail or {}}
    data.setdefault("log", []).append({
        "ts": _now(), "phase": phase, "event": "status=%s" % status, "detail": detail,
    })
    data["log"] = data["log"][-200:]
    save_progress(t, n, data)
    return data


def load_manifest(t: str, n: str) -> dict:
    p = manifest_path(t, n)
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "logical_id": logical_id(t, n),
        "created_at": _now(),
        "updated_at": _now(),
        "adapted_skills": [],
        "created_paths": [],
        "ide_links": [],
        "secrets_path": "",
        "harness_files": [],
    }


def save_manifest(t: str, n: str, data: dict) -> Path:
    data["updated_at"] = _now()
    p = manifest_path(t, n)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def amrd_defaults() -> dict:
    p = skill_root() / "assets" / "amrd-test.defaults.yml"
    if inv.yaml and p.is_file():
        return inv.load_yaml(p) or {}
    return {
        "shared_env": {
            "redis": {"host": DEFAULT_REDIS["host"], "port": int(DEFAULT_REDIS["port"]), "db": 0},
            "mysql": {
                "host": DEFAULT_REDIS["host"],
                "port": 3306,
                "database": "amrd_qa_vault",
            },
        },
        "install": {"mode": "shared_full"},
    }


def cmd_status(args) -> int:
    t, n, lid = _resolve(args)
    prog = load_progress(t, n)
    man = load_manifest(t, n) if manifest_path(t, n).is_file() else None
    inv_p = inv.inventory_path(t, n)
    report = {
        "logical_id": lid,
        "progress": prog,
        "inventory_exists": inv_p.is_file(),
        "inventory": str(inv_p),
        "manifest_exists": man is not None,
        "manifest_adapted_count": len((man or {}).get("adapted_skills") or []),
        "next_phase": next(
            (k for k in PHASES if prog["phases"].get(k, {}).get("status") != "done"),
            None,
        ),
        "resume_hint": (
            "python scripts/install_flow.py resume --id %s" % lid
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_phase1_team(args) -> int:
    """选团队：amrd-test 复用默认库；否则输出需问答字段。"""
    t, n, lid = _resolve(args)
    is_amrd = lid == AMRD_LOGICAL or (t == "test-team" and n == "amrd-test")

    class _A:
        pass

    a = _A()
    a.id = lid
    a.team_type = t
    a.team_name = n
    a.force = bool(getattr(args, "force", False))
    rc = boot.cmd_init(a)
    if rc != 0:
        mark_phase(t, n, "1_team", "blocked", {"init_rc": rc})
        return rc

    path = inv.inventory_path(t, n)
    data = inv.load_yaml(path)
    questions: list[dict] = []

    if is_amrd:
        defs = amrd_defaults()
        se = defs.get("shared_env") or {}
        redis = se.get("redis") or {}
        mysql = se.get("mysql") or {}
        fills = {
            "shared_env.redis.host": redis.get("host") or DEFAULT_REDIS["host"],
            "shared_env.redis.port": int(redis.get("port") or DEFAULT_REDIS["port"]),
            "shared_env.mysql.host": mysql.get("host") or DEFAULT_REDIS["host"],
            "shared_env.mysql.port": int(mysql.get("port") or 3306),
            "shared_env.mysql.database": mysql.get("database") or "amrd_qa_vault",
            "install.mode": (defs.get("install") or {}).get("mode") or "shared_full",
            "team.display_name": (defs.get("team") or {}).get("display_name") or "AMRD 测试组",
        }
        for k, v in fills.items():
            inv._set(data, k, v)
        # secrets：若已有则复用路径
        sp = inv._get(data, "shared_env.secrets_path")
        if inv._empty(sp):
            sp = str((vault_root() / "_kb" / "secrets.local.json").as_posix())
            inv._set(data, "shared_env.secrets_path", sp)
        secrets_ok = Path(str(sp)).expanduser().is_file()
        if secrets_ok:
            # 账号从 secrets 回填到清单（密码永不写回清单）
            try:
                conf = mysql_conf(Path(str(sp)).expanduser().parent.parent)
                if conf and conf.get("user") and inv._empty(inv._get(data, "shared_env.mysql.user")):
                    inv._set(data, "shared_env.mysql.user", conf["user"])
                    fills["shared_env.mysql.user"] = conf["user"]
            except Exception:
                if inv._empty(inv._get(data, "shared_env.mysql.user")):
                    inv._set(data, "shared_env.mysql.user", "<in_secrets>")
                    fills["shared_env.mysql.user"] = "<in_secrets>"
            if inv._empty(inv._get(data, "shared_env.mysql.password")):
                inv._set(data, "shared_env.mysql.password", "<in_secrets>")
        if not secrets_ok:
            questions.append({
                "field": "shared_env.secrets_path",
                "ask": (
                    "amrd-test 复用默认 Redis/MySQL，但本机尚无 secrets.local.json。"
                    "请确认已从 assets/secrets.local.json.example 复制并填好密码，"
                    "或告知 secrets 绝对路径。"
                ),
            })
        if inv._empty(inv._get(data, "local.author")):
            questions.append({
                "field": "local.author",
                "ask": "你的域账号或姓名？（写入安装清单）",
            })
        if inv._empty(inv._get(data, "shared_env.mysql.user")):
            questions.append({
                "field": "shared_env.mysql.user",
                "ask": "MySQL 用户名？（或写 <in_secrets> 表示只在 secrets 文件里）",
            })
        detail = {
            "team_preset": "amrd-test",
            "reuse_default_redis_mysql": True,
            "defaults_applied": fills,
            "secrets_present": secrets_ok,
            "questions": questions,
        }
    else:
        # 非默认团队：必须问答写配置
        for field, ask in (
            ("team.display_name", "团队显示名？"),
            ("local.author", "你的域账号或姓名？"),
            ("shared_env.redis.host", "Redis 主机？"),
            ("shared_env.redis.port", "Redis 端口？（默认 6379）"),
            ("shared_env.mysql.host", "MySQL 主机？"),
            ("shared_env.mysql.port", "MySQL 端口？（默认 3306）"),
            ("shared_env.mysql.user", "MySQL 用户？"),
            ("shared_env.mysql.database", "MySQL 库名？"),
            ("shared_env.secrets_path", "secrets.local.json 路径？（密码只写此文件）"),
            ("install.mode", "安装模式？shared_full / local_then_shared / local_only"),
        ):
            if inv._empty(inv._get(data, field)):
                questions.append({"field": field, "ask": ask})
        detail = {
            "team_preset": "custom",
            "reuse_default_redis_mysql": False,
            "questions": questions,
            "interview_hint": (
                "一次只问一项；回答后："
                "python scripts/install_flow.py write-field --id %s --field <f> --value <v>"
                % lid
            ),
        }

    if inv.yaml:
        inv.dump_yaml(data, path)

    status = "done" if not questions else "waiting_input"
    mark_phase(t, n, "1_team", status, detail)
    print(json.dumps({
        "ok": True,
        "phase": "1_team",
        "status": status,
        "logical_id": lid,
        "inventory": str(path),
        **detail,
        "next": (
            "补齐 questions 后 python scripts/install_flow.py phase2-env --id %s" % lid
            if questions
            else "python scripts/install_flow.py phase2-env --id %s" % lid
        ),
    }, ensure_ascii=False, indent=2))
    return 0 if status == "done" else 1


def cmd_write_field(args) -> int:
    t, n, lid = _resolve(args)
    rc, out = _run_py(
        "install_inventory.py",
        ["write-field", "--id", lid, "--field", args.field, "--value", args.value],
    )
    log_event(t, n, "1_team", "write-field", {"field": args.field, "rc": rc})
    print(out)
    # 若 1_team 在 waiting，重扫 missing
    path = inv.inventory_path(t, n)
    if path.is_file():
        data = inv.load_yaml(path)
        miss = inv.missing_fields(data)
        if not miss:
            mark_phase(t, n, "1_team", "done", {"after_write": args.field})
    return rc


def _probe_import(mod: str) -> tuple[bool, str]:
    try:
        m = __import__(mod.split(".")[0])
        ver = getattr(m, "__version__", "?")
        return True, str(ver)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def cmd_phase2_env(args) -> int:
    t, n, lid = _resolve(args)
    py_ver = "%s.%s.%s" % sys.version_info[:3]
    py_ok = sys.version_info >= (3, 10)
    items = []
    pending_install = []
    for pkg in ENV_PACKAGES:
        ok, ver = _probe_import(pkg["import"])
        row = {
            "key": pkg["key"],
            "pip": pkg["pip"],
            "installed": ok,
            "version": ver if ok else "",
            "required": pkg["required"],
            "note": pkg.get("note") or "",
            "action": "ok" if ok else ("install" if pkg["required"] or args.include_optional else "optional_skip"),
        }
        items.append(row)
        if not ok and (pkg["required"] or (args.include_optional and pkg["key"] in ("laya", "stuntd"))):
            pending_install.append(pkg["pip"])

    # 额外：git
    git_ok = shutil.which("git") is not None
    report = {
        "python": {"exe": sys.executable, "version": py_ver, "ok": py_ok, "min": "3.10"},
        "git": {"ok": git_ok, "path": shutil.which("git") or ""},
        "packages": items,
        "pending_download": pending_install,
        "approve_required": bool(pending_install),
    }

    if pending_install and not args.approve_install:
        mark_phase(t, n, "2_env", "waiting_consent", report)
        print(json.dumps({
            "ok": False,
            "phase": "2_env",
            "status": "waiting_consent",
            "message": "以下包待安装，请确认后加 --approve-install",
            **report,
            "next": (
                "python scripts/install_flow.py phase2-env --id %s --approve-install%s"
                % (lid, " --include-optional" if args.include_optional else "")
            ),
        }, ensure_ascii=False, indent=2))
        return 1

    installed = []
    if args.approve_install and pending_install:
        for pip_spec in pending_install:
            cp = subprocess.run(
                [sys.executable, "-m", "pip", "install", pip_spec],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            installed.append({"pip": pip_spec, "rc": cp.returncode})
            if cp.returncode != 0:
                mark_phase(t, n, "2_env", "blocked", {"install_fail": pip_spec, "stderr": (cp.stderr or "")[:500]})
                print(json.dumps({
                    "ok": False, "phase": "2_env", "error": "pip install failed",
                    "pip": pip_spec, "stderr": (cp.stderr or "")[:1500],
                }, ensure_ascii=False, indent=2))
                return 2
        # re-probe
        for row in items:
            ok, ver = _probe_import(next(p["import"] for p in ENV_PACKAGES if p["key"] == row["key"]))
            row["installed"] = ok
            row["version"] = ver if ok else ""

    # 写回 checklist
    path = inv.inventory_path(t, n)
    if path.is_file() and inv.yaml:
        data = inv.load_yaml(path)
        inv._set(data, "checklist.python_ready", py_ok)
        inv._set(data, "checklist.pip_pymysql_ok", any(
            i["key"] == "pymysql" and i["installed"] for i in items
        ))
        inv._set(data, "checklist.pip_pyyaml_ok", any(
            i["key"] == "pyyaml" and i["installed"] for i in items
        ))
        inv.dump_yaml(data, path)

    report["installed_now"] = installed
    status = "done" if py_ok and all(
        i["installed"] for i in items if i["required"]
    ) else "blocked"
    mark_phase(t, n, "2_env", status, report)
    print(json.dumps({
        "ok": status == "done",
        "phase": "2_env",
        "status": status,
        **report,
        "next": "python scripts/install_flow.py phase3-db --id %s" % lid,
    }, ensure_ascii=False, indent=2))
    return 0 if status == "done" else 1


def cmd_phase3_db(args) -> int:
    t, n, lid = _resolve(args)
    path = inv.inventory_path(t, n)
    if not path.is_file():
        print(json.dumps({"ok": False, "error": "inventory missing"}, ensure_ascii=False))
        return 2

    class _A:
        pass

    a = _A()
    a.id = lid
    a.team_type = t
    a.team_name = n
    a.allow_tcp_only = bool(args.allow_tcp_only)
    # capture probe stdout
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc_probe = boot.cmd_probe_connection(a)
    probe_text = buf.getvalue()
    try:
        probe = json.loads(probe_text)
    except Exception:
        probe = {"raw": probe_text[:2000], "rc": rc_probe}

    root = vault_root()
    data = inv.load_yaml(path)
    secrets = inv._get(data, "shared_env.secrets_path") or ""
    if secrets:
        sp = Path(str(secrets)).expanduser()
        if sp.is_file():
            root = sp.parent.parent if sp.parent.name == "_kb" else sp.parent

    tables_ok = False
    tables_detail: dict[str, Any] = {}
    conf = mysql_conf(root)
    if conf and conf.get("host"):
        try:
            from vault_store import MysqlVaultStore

            tables_detail = MysqlVaultStore.check_schema(conf)
            tables_ok = bool(tables_detail.get("ok"))
        except Exception as e:
            tables_detail = {"error": "%s: %s" % (type(e).__name__, e)}
            rc_g, out_g = _run_py("install_gate.py", ["--id", lid, "--json"])
            tables_detail["install_gate_preview"] = out_g[:1500]
            tables_ok = rc_g == 0
    else:
        tables_detail = {"error": "no mysql conf / secrets"}

    ddl_applied = False
    if not tables_ok and args.approve_ddl:
        ddl = skill_root() / "assets" / "ddl_mysql57.sql"
        conf = mysql_conf(root) or {}
        if not conf.get("host"):
            mark_phase(t, n, "3_db", "blocked", {"error": "no mysql secrets for DDL"})
            print(json.dumps({"ok": False, "error": "secrets 中无 MySQL，无法执行 DDL"},
                             ensure_ascii=False, indent=2))
            return 2
        try:
            import pymysql
            conn = pymysql.connect(
                host=conf["host"], port=int(conf.get("port") or 3306),
                user=conf["user"], password=conf.get("password") or "",
                charset="utf8mb4", autocommit=True,
            )
            sql = ddl.read_text(encoding="utf-8")
            # split naive on ;\n
            cur = conn.cursor()
            for stmt in sql.split(";"):
                s = stmt.strip()
                if not s or s.startswith("--"):
                    continue
                cur.execute(s)
            conn.close()
            ddl_applied = True
            tables_ok = True
            tables_detail["ddl"] = "applied"
            if path.is_file() and inv.yaml:
                data = inv.load_yaml(path)
                inv._set(data, "checklist.ddl_applied", True)
                inv.dump_yaml(data, path)
        except Exception as e:
            mark_phase(t, n, "3_db", "blocked", {"ddl_error": str(e)})
            print(json.dumps({
                "ok": False, "phase": "3_db", "error": "ddl failed",
                "detail": "%s: %s" % (type(e).__name__, e),
            }, ensure_ascii=False, indent=2))
            return 2
    elif not tables_ok:
        mark_phase(t, n, "3_db", "waiting_consent", {
            "probe": probe, "tables": tables_detail,
        })
        print(json.dumps({
            "ok": False,
            "phase": "3_db",
            "status": "waiting_consent",
            "message": "库表缺失或不完整；确认后加 --approve-ddl 执行 assets/ddl_mysql57.sql",
            "probe": probe,
            "tables": tables_detail,
            "next": "python scripts/install_flow.py phase3-db --id %s --approve-ddl" % lid,
        }, ensure_ascii=False, indent=2))
        return 1

    shared_ok = bool(probe.get("shared_ok")) if isinstance(probe, dict) else False
    status = "done" if (tables_ok and (shared_ok or args.allow_tcp_only)) else (
        "done" if tables_ok else "blocked"
    )
    # redis ping from probe
    detail = {
        "probe": probe,
        "tables": tables_detail,
        "ddl_applied": ddl_applied,
        "redis_ok": bool((probe.get("redis") or {}).get("tcp") or (probe.get("redis") or {}).get("ping")),
        "mysql_ok": tables_ok,
    }
    mark_phase(t, n, "3_db", status, detail)
    print(json.dumps({
        "ok": status == "done",
        "phase": "3_db",
        "status": status,
        **detail,
        "next": "python scripts/install_flow.py phase4-skills --id %s" % lid,
    }, ensure_ascii=False, indent=2))
    return 0 if status == "done" else 1


def cmd_phase4_skills(args) -> int:
    t, n, lid = _resolve(args)
    rc, out = _run_py("skill_vault_adapt.py", ["scan", "--json"])
    try:
        scan = json.loads(out)
    except Exception:
        scan = {"raw": out[:2000], "rc": rc}
    skills = scan.get("skills") or []
    candidates = [
        s for s in skills
        if not s.get("skip_suggested") and not s.get("adapted")
    ]
    already = [s for s in skills if s.get("adapted")]

    if not args.approve_adapt:
        mark_phase(t, n, "4_skills", "waiting_consent", {
            "candidates": candidates, "already_adapted": already,
        })
        print(json.dumps({
            "ok": False,
            "phase": "4_skills",
            "status": "waiting_consent",
            "message": "下列 skill 待改造；确认后 --approve-adapt [--skill-id X | --all]",
            "candidates": candidates,
            "already_adapted": already,
            "count_pending": len(candidates),
            "next": (
                "python scripts/install_flow.py phase4-skills --id %s --approve-adapt --all"
                % lid
            ),
        }, ensure_ascii=False, indent=2))
        return 1

    adapt_args = ["adapt"]
    if args.all or (not args.skill_id):
        adapt_args.append("--all")
    else:
        adapt_args += ["--skill-id", args.skill_id]
    if args.force:
        adapt_args.append("--force")
    rc_a, out_a = _run_py("skill_vault_adapt.py", adapt_args)
    try:
        adapt_res = json.loads(out_a)
    except Exception:
        adapt_res = {"raw": out_a[:3000], "rc": rc_a}

    # update manifest
    man = load_manifest(t, n)
    for r in (adapt_res.get("results") or []):
        if not r.get("ok"):
            continue
        sid = r["skill_id"]
        entry = {
            "skill_id": sid,
            "path": r.get("path"),
            "copies": r.get("copies") or [r.get("path")],
            "integration": r.get("integration"),
            "files": [
                "SKILL.md#知识金库接入(marker节) × %d 副本" % len(r.get("copies") or [r.get("path")]),
                "<vault-base>/%s/preflight.yml" % sid,
                "<vault-base>/%s/adapted.json" % sid,
            ],
            "adapted_at": _now(),
        }
        man["adapted_skills"] = [
            x for x in man.get("adapted_skills", []) if x.get("skill_id") != sid
        ] + [entry]
    save_manifest(t, n, man)

    status = "done" if rc_a == 0 else "blocked"
    mark_phase(t, n, "4_skills", status, {"adapt": adapt_res})
    print(json.dumps({
        "ok": status == "done",
        "phase": "4_skills",
        "status": status,
        "adapt": adapt_res,
        "manifest": str(manifest_path(t, n)),
        "next": "python scripts/install_flow.py phase5-harness --id %s" % lid,
    }, ensure_ascii=False, indent=2))
    return 0 if status == "done" else 1


def cmd_phase5_harness(args) -> int:
    """落地 harness 说明 + 完善 install-manifest（卸载/更新目录）。"""
    t, n, lid = _resolve(args)
    td = team_dir(t, n)
    td.mkdir(parents=True, exist_ok=True)

    class _A:
        pass

    a = _A()
    a.id = lid
    a.team_type = t
    a.team_name = n
    # amrd-test：从 secrets 补 mysql.user，避免 apply 因清单缺项阻断
    inv_p = inv.inventory_path(t, n)
    if inv_p.is_file() and inv.yaml:
        data = inv.load_yaml(inv_p)
        if inv._empty(inv._get(data, "shared_env.mysql.user")):
            sp = inv._get(data, "shared_env.secrets_path") or ""
            conf = None
            if sp:
                try:
                    conf = mysql_conf(Path(str(sp)).expanduser().parent.parent)
                except Exception:
                    conf = None
            if conf and conf.get("user"):
                inv._set(data, "shared_env.mysql.user", conf["user"])
            else:
                inv._set(data, "shared_env.mysql.user", "<in_secrets>")
            if inv._empty(inv._get(data, "shared_env.mysql.password")):
                inv._set(data, "shared_env.mysql.password", "<in_secrets>")
            inv.dump_yaml(data, inv_p)

    a.force = bool(args.force)
    a.skip_gate = bool(args.skip_gate)
    buf_rc = boot.cmd_apply(a)

    harness_md = td / "HARNESS.md"
    harness_body = """# vault-base 安装 Harness · {lid}

本文件由 `install_flow.py phase5-harness` 生成，供卸载 / 版本更新对照。

## 工作模式（适配后业务 skill 强制五步；全部命令走 vault-base 中心目录）

1. `assets/skill-adapt/hooks/pre_task_recall.py` — Redis/MySQL 相关性召回
2. `scripts/preflight_materials.py` — YAML 前置物料清单（vault 侧 preflight/）
3. 一次一问完备物料（Matt/grill）→ `write-field`
4. `assets/skill-adapt/hooks/post_task_sediment.py` — 过程沉淀（vault 侧 sediment/）
5. 人审 / Redis+MySQL / WebPlatform 归档与 Laya

## 验收门禁

- `readiness_gate.py --fix --json`
- `install_gate.py --id {lid} --require-shared --json`（I1–I9）

## 目录与可卸载项

见同目录 `install-manifest.json`：

- `adapted_skills`：目标 skill 仅 SKILL.md marker 节；preflight/账本在 vault 侧
- `ide_links`：junction 目标
- `created_paths`：本机新建路径
- `secrets_path`：**卸载时默认保留**（含密码）

卸载预览：

```bash
python scripts/install_flow.py uninstall-plan --id {lid}
python scripts/install_flow.py uninstall --id {lid} --approve
```

更新覆盖：重新 `phase4-skills --approve-adapt --force` + `phase5-harness`。
""".format(lid=lid)
    harness_md.write_text(harness_body, encoding="utf-8")

    man = load_manifest(t, n)
    man["harness_files"] = [str(harness_md.as_posix())]
    man["created_paths"] = sorted(set(
        (man.get("created_paths") or []) + [
            str(td.as_posix()),
            str(progress_path(t, n).as_posix()),
            str(manifest_path(t, n).as_posix()),
            str(harness_md.as_posix()),
        ]
    ))
    # IDE links from inventory
    if inv.inventory_path(t, n).is_file():
        data = inv.load_yaml(inv.inventory_path(t, n))
        links = inv._get(data, "common_skills_repo.ide_links") or {}
        man["ide_links"] = [
            {"ide": k, "enabled": bool(v)} for k, v in dict(links).items()
        ]
        man["secrets_path"] = str(inv._get(data, "shared_env.secrets_path") or "")
    save_manifest(t, n, man)

    detail = {
        "apply_rc": buf_rc,
        "harness": str(harness_md),
        "manifest": str(manifest_path(t, n)),
    }
    status = "done" if buf_rc == 0 else "blocked"
    mark_phase(t, n, "5_harness", status, detail)
    print(json.dumps({
        "ok": status == "done",
        "phase": "5_harness",
        "status": status,
        **detail,
        "next": "python scripts/install_flow.py phase6-smoke --id %s" % lid,
    }, ensure_ascii=False, indent=2))
    return 0 if status == "done" else 1


def cmd_phase6_smoke(args) -> int:
    t, n, lid = _resolve(args)
    checks = []

    # readiness
    rc, out = _run_py("readiness_gate.py", ["--fix", "--json"])
    checks.append({"name": "readiness_gate", "rc": rc, "out": out[:1200]})

    # install_gate
    rc2, out2 = _run_py("install_gate.py", ["--id", lid, "--require-shared", "--json"])
    checks.append({"name": "install_gate", "rc": rc2, "out": out2[:1200]})

    if (_scripts() / "vault_cache_ask.py").is_file():
        rc3, out3 = _run_py(
            "vault_cache_ask.py",
            ["vault-base smoke", "--limit", "3", "--json"],
        )
        checks.append({"name": "vault_cache_ask", "rc": rc3, "out": out3[:800]})
    else:
        checks.append({"name": "vault_cache_ask", "rc": 0, "out": "skipped"})

    web_checklist = [
        "启动 WebPlatform：python run_WebPlatform@AMRD.py → 侧栏 VaultBase",
        "Query：共享 Redis 召回一条（可用空结果）",
        "知识扫描：对已适配 skill 扫本地沉淀 / enqueue",
        "未审核人审：approve/reject 一条 pending（若有）",
        "Laya 训练：查看 metrics / 门禁 0.75·0.95（stuntd/laya 可选）",
    ]

    hard_fail = any(c["name"] in ("readiness_gate", "install_gate") and c["rc"] != 0 for c in checks)
    status = "blocked" if hard_fail else "done"
    detail = {
        "checks": checks,
        "webplatform_manual": web_checklist,
        "note": "WebPlatform 页为人工冒烟；CLI 门禁通过即 phase6 CLI 部分完成",
    }
    mark_phase(t, n, "6_smoke", status, detail)
    print(json.dumps({
        "ok": not hard_fail,
        "phase": "6_smoke",
        "status": status,
        **detail,
        "progress": str(progress_path(t, n)),
    }, ensure_ascii=False, indent=2))
    return 0 if not hard_fail else 1


def cmd_resume(args) -> int:
    t, n, lid = _resolve(args)
    prog = load_progress(t, n)
    nxt = next(
        (k for k in PHASES if prog["phases"].get(k, {}).get("status") not in ("done",)),
        None,
    )
    mapping = {
        "1_team": "phase1-team",
        "2_env": "phase2-env",
        "3_db": "phase3-db",
        "4_skills": "phase4-skills",
        "5_harness": "phase5-harness",
        "6_smoke": "phase6-smoke",
    }
    if nxt is None:
        print(json.dumps({
            "ok": True, "logical_id": lid, "message": "全部阶段已 done",
            "progress": prog,
        }, ensure_ascii=False, indent=2))
        return 0
    st = prog["phases"][nxt].get("status")
    cmd = mapping[nxt]
    hint = "python scripts/install_flow.py %s --id %s" % (cmd, lid)
    if st == "waiting_consent":
        if nxt == "2_env":
            hint += " --approve-install"
        elif nxt == "3_db":
            hint += " --approve-ddl"
        elif nxt == "4_skills":
            hint += " --approve-adapt --all"
    elif st == "waiting_input":
        hint = (
            "先 write-field 补齐 questions，再 "
            "python scripts/install_flow.py %s --id %s" % (cmd, lid)
        )
    print(json.dumps({
        "ok": True,
        "logical_id": lid,
        "next_phase": nxt,
        "status": st,
        "resume_command": hint,
        "phase_detail": prog["phases"][nxt],
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_uninstall_plan(args) -> int:
    t, n, lid = _resolve(args)
    man = load_manifest(t, n)
    plan = {
        "logical_id": lid,
        "will_remove": [],
        "will_keep": [],
    }
    vbroot = skill_root()
    sediment_keep = []
    for s in man.get("adapted_skills") or []:
        root = Path(s["path"])
        sid = s.get("skill_id") or Path(s["path"]).name
        plan["will_remove"].extend([
            "%s  (仅摘「知识金库接入」marker 节 + frontmatter 两行)" % (root / "SKILL.md"),
            str(vbroot / sid / "preflight.yml"),
            str(vbroot / sid / "adapted.json"),
        ])
        sediment_keep.append(str(vbroot / sid / "sediment"))
    plan["will_remove"].extend(man.get("harness_files") or [])
    plan["will_remove"].append(str(manifest_path(t, n)))
    plan["will_remove"].append(str(progress_path(t, n)))
    plan["will_keep"] = [
        "vault 侧沉淀产物（知识资产，卸载不删）: %s" % "; ".join(sediment_keep),
        man.get("secrets_path") or "(secrets.local.json)",
        "IDE junction 到 common-skills-repo/vault-base（需手动 common_skills_repo unlink）",
        "MySQL/Redis 共享数据（永不随卸载删除）",
    ]
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


def cmd_uninstall(args) -> int:
    if not args.approve:
        print(json.dumps({
            "ok": False,
            "error": "需要 --approve；先看 uninstall-plan",
        }, ensure_ascii=False))
        return 2
    t, n, lid = _resolve(args)
    man = load_manifest(t, n)
    removed = []
    # 业务 skill 卸载统一走 skill_vault_adapt uninstall（摘 marker 节 + vault 侧文件；
    # --purge-legacy 清 v1 遗留，与模板一致才删，被改过的保留并报告）
    for s in man.get("adapted_skills") or []:
        sid = s.get("skill_id") or Path(s["path"]).name
        rc_u, out_u = _run_py("skill_vault_adapt.py", [
            "uninstall", "--skill-id", sid, "--purge-legacy",
        ])
        removed.append({"skill_id": sid, "rc": rc_u, "detail": out_u[-1500:]})
    for f in man.get("harness_files") or []:
        p = Path(f)
        if p.is_file():
            p.unlink()
            removed.append(str(p))
    for p in (manifest_path(t, n), progress_path(t, n)):
        if p.is_file():
            p.unlink()
            removed.append(str(p))
    print(json.dumps({
        "ok": True,
        "logical_id": lid,
        "removed": removed,
        "kept": ["secrets", "shared redis/mysql", "vault-base skill 本体"],
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="vault-base 七阶段安装（断档续装）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_id(p):
        p.add_argument("--id", help="team-type:team-name，如 test-team:amrd-test")
        p.add_argument("--type", dest="team_type")
        p.add_argument("--name", dest="team_name")

    p = sub.add_parser("status", help="查看进度与下一阶段")
    add_id(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("write-field", help="写入清单字段（阶段1问答）")
    add_id(p)
    p.add_argument("--field", required=True)
    p.add_argument("--value", required=True)
    p.set_defaults(func=cmd_write_field)

    p = sub.add_parser("phase1-team", help="选团队 / amrd-test 复用默认库")
    add_id(p)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_phase1_team)

    p = sub.add_parser("phase2-env", help="环境检查与同意后安装")
    add_id(p)
    p.add_argument("--approve-install", action="store_true")
    p.add_argument("--include-optional", action="store_true",
                   help="把 laya/stuntd 列入待安装")
    p.set_defaults(func=cmd_phase2_env)

    p = sub.add_parser("phase3-db", help="Redis/MySQL 连通与库表")
    add_id(p)
    p.add_argument("--approve-ddl", action="store_true")
    p.add_argument("--allow-tcp-only", action="store_true")
    p.set_defaults(func=cmd_phase3_db)

    p = sub.add_parser("phase4-skills", help="skill 清单与同意后改造")
    add_id(p)
    p.add_argument("--approve-adapt", action="store_true")
    p.add_argument("--all", action="store_true")
    p.add_argument("--skill-id")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_phase4_skills)

    p = sub.add_parser("phase5-harness", help="流程 harness + install-manifest")
    add_id(p)
    p.add_argument("--force", action="store_true")
    p.add_argument("--skip-gate", action="store_true")
    p.set_defaults(func=cmd_phase5_harness)

    p = sub.add_parser("phase6-smoke", help="amrd-test 冒烟 + WebPlatform 清单")
    add_id(p)
    p.set_defaults(func=cmd_phase6_smoke)

    p = sub.add_parser("resume", help="断档续装：输出下一命令")
    add_id(p)
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("uninstall-plan", help="卸载预览")
    add_id(p)
    p.set_defaults(func=cmd_uninstall_plan)

    p = sub.add_parser("uninstall", help="按 manifest 卸载适配注入")
    add_id(p)
    p.add_argument("--approve", action="store_true")
    p.set_defaults(func=cmd_uninstall)

    args = ap.parse_args()
    # validate id for most commands
    if not getattr(args, "id", None) and not (
        getattr(args, "team_type", None) and getattr(args, "team_name", None)
    ):
        # default amrd-test for convenience
        args.id = AMRD_LOGICAL
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
