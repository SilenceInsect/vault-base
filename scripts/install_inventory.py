#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install_inventory.py — 安装物料清单：初始化 / 缺项提问 / 校验。

其他团队安装共享金库时，skill 应：
  1. init 生成清单
  2. missing 列出未填必填项并逐项问用户
  3. 用户回答后 write-field / 人工改 yml
  4. validate 通过后再 env_ensure / readiness
"""
from __future__ import annotations

import argparse
import datetime
import re
import shutil
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
from team_workspace import (  # noqa: E402
    ensure_gitignore,
    init_team,
    logical_id,
    parse_id,
    team_dir,
    teams_root,
)
from vault_paths import skill_root  # noqa: E402

ID_RE = re.compile(r"^([a-z0-9][a-z0-9_-]*):([a-z0-9][a-z0-9_-]*)$", re.I)

# 本地模式必填（点分路径）
REQUIRED_LOCAL = [
    "team.id",
    "team.team_type",
    "team.team_name",
    "common_skills_repo.path",
    "install.skill_root",
    "install.vault_root",
    "install.python_exe",
    "local.author",
]

# 共享模式额外必填
REQUIRED_SHARED = [
    "shared_env.redis.host",
    "shared_env.mysql.host",
    "shared_env.mysql.user",
    "shared_env.mysql.database",
    "shared_env.secrets_path",
]

QUESTIONS = {
    "team.id": "组逻辑 ID？（格式 team-type:team-name，例 demo-team:demo-project）",
    "team.team_type": "team-type？（例 demo-team）",
    "team.team_name": "team-name？（例 demo-project）",
    "install.skill_root": "vault-base 安装的绝对路径？（应等于 common-skills-repo/vault-base）",
    "install.vault_root": "本地 Vault 根路径？（默认可填 <skill_root>/references/vault）",
    "install.python_exe": "带 pymysql 的 Python 解释器绝对路径？",
    "common_skills_repo.path": "用户级 common-skills-repo 绝对路径？（默认 ~/common-skills-repo）",
    "install.mode": (
        "安装模式？local_only(仅本地) / local_then_shared(先本地后共享，推荐) / "
        "shared_full(直接共享全量)"
    ),
    "local.author": "你的域账号或姓名？",
    "shared_env.redis.host": "共享 Redis 主机？",
    "shared_env.redis.port": "共享 Redis 端口？（默认 6379）",
    "shared_env.mysql.host": "共享 MySQL 主机？",
    "shared_env.mysql.port": "共享 MySQL 端口？（默认 3306）",
    "shared_env.mysql.user": "MySQL 用户？",
    "shared_env.mysql.database": "MySQL 库名？（默认 amrd_qa_vault）",
    "shared_env.secrets_path": "secrets.local.json 绝对路径？（密码只放该文件）",
    "coordination.X4_create_database": (
        "X-4 建库权限？unknown(未知)/requested(已申请)/done(已完成)/blocked(受阻)"
    ),
    "coordination.X5_ddl_impact_review": (
        "X-5 DDL 影响面评估？unknown(未知)/requested(已申请)/done(已完成)/blocked(受阻)"
    ),
}


def template_path() -> Path:
    return skill_root() / "assets" / "install-inventory.template.yml"


def inventory_path(team_type: str, team_name: str) -> Path:
    return team_dir(team_type, team_name) / "install-inventory.yml"


def _get(data: dict, dotted: str):
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set(data: dict, dotted: str, value):
    cur = data
    parts = dotted.split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _empty(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    if isinstance(v, (list, dict)) and len(v) == 0:
        return True
    return False


def load_yaml(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(text) or {}
    # 无 PyYAML：极简解析（仅支持本模板的缩进 map / 标量 / 空列表）
    return _mini_yaml(text)


def dump_yaml(data: dict, path: Path) -> None:
    if yaml is not None:
        path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return
    # 无 PyYAML 时不重写整文件，避免丢注释；write-field 用文本替换
    raise RuntimeError("需要 PyYAML 才能写回完整 YAML：pip install pyyaml")


def _mini_yaml(text: str) -> dict:
    """足够读 template 缺项检查的子集；复杂结构请装 PyYAML。"""
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if line.startswith("- "):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key, val = key.strip(), val.strip()
        # 去掉未加引号的行尾注释；引号内的 # 保留
        if val and not (val.startswith('"') or val.startswith("'")):
            if " #" in val:
                val = val.split(" #", 1)[0].rstrip()
            elif val.startswith("#"):
                val = ""
        elif val.startswith('"') and val.count('"') >= 2:
            end = val.find('"', 1)
            val = val[1:end]
        elif val.startswith("'") and val.count("'") >= 2:
            end = val.find("'", 1)
            val = val[1:end]
        else:
            val = val.strip('"').strip("'")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if val == "" or val in ("[]", "{}"):
            child: dict = {}
            parent[key] = child if val != "[]" else []
            if val != "[]":
                stack.append((indent, child))
        else:
            if val in ("true", "false"):
                parent[key] = val == "true"
            elif val.isdigit():
                parent[key] = int(val)
            elif val == "null":
                parent[key] = None
            else:
                parent[key] = val
    return root


def required_for(data: dict) -> list[str]:
    mode = str(_get(data, "install.mode") or "local_then_shared")
    req = list(REQUIRED_LOCAL)
    if mode != "local_only":
        req.extend(REQUIRED_SHARED)
    return req


def missing_fields(data: dict) -> list[str]:
    return [k for k in required_for(data) if _empty(_get(data, k))]


def cmd_init(args) -> int:
    ensure_gitignore()
    if args.id:
        t, n = parse_id(args.id)
    else:
        t, n = args.team_type, args.team_name
        if not t or not n:
            print("需要 --id 或 --type/--name", file=sys.stderr)
            return 2
    init_team(t, n)
    dest = inventory_path(t, n)
    if dest.exists() and not args.force:
        print("已存在:", dest)
        return 0
    shutil.copyfile(template_path(), dest)
    # 填入已知 id 与默认路径（common-skills-repo 为真相源）
    text = dest.read_text(encoding="utf-8")
    lid = logical_id(t, n)
    from vault_paths import common_skills_repo  # local import

    common = str(common_skills_repo())
    skill = str(skill_root())
    vault = str(skill_root() / "references" / "vault")
    py = sys.executable
    replacements = {
        'id: ""': f'id: "{lid}"',
        'team_type: ""': f'team_type: "{t}"',
        'team_name: ""': f'team_name: "{n}"',
        'path: ""                       # 必填 默认 ~/common-skills-repo': (
            f'path: "{common}"                       # 必填 默认 ~/common-skills-repo'
        ),
        'skill_root: ""': f'skill_root: "{skill}"',
        'vault_root: ""': f'vault_root: "{vault}"',
        'python_exe: ""': f'python_exe: "{py}"',
        'secrets_path: ""': f'secrets_path: "{vault}/_kb/secrets.local.json"',
    }
    # only replace first empty occurrences carefully
    for old, new in replacements.items():
        text = text.replace(old, new, 1)
    today = datetime.date.today().isoformat()
    text = text.replace('updated_at: ""', f'updated_at: "{today}"', 1)
    dest.write_text(text, encoding="utf-8")
    print("logical_id:", lid)
    print("inventory:", dest)
    print("下一步: python scripts/install_inventory.py missing --id", lid)
    return 0


def cmd_missing(args) -> int:
    t, n = parse_id(args.id) if args.id else (args.team_type, args.team_name)
    path = inventory_path(t, n)
    if not path.exists():
        print("清单不存在，先 init:", path, file=sys.stderr)
        return 2
    data = load_yaml(path)
    miss = missing_fields(data)
    out = {
        "id": logical_id(t, n),
        "path": str(path),
        "mode": _get(data, "install.mode"),
        "missing": miss,
        "questions": [{"field": f, "ask": QUESTIONS.get(f, f"请提供 {f}")} for f in miss],
    }
    if args.json:
        import json
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("清单:", path)
        print("模式:", out["mode"])
        if not miss:
            print("必填项已齐。可执行 env_ensure / readiness_gate。")
        else:
            print("缺 %d 项，请按序提问补齐：\n" % len(miss))
            for i, q in enumerate(out["questions"], 1):
                print("%d. [%s] %s" % (i, q["field"], q["ask"]))
    return 0 if not miss else 1


def cmd_validate(args) -> int:
    return cmd_missing(args)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="共享金库安装物料清单")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="生成 teams/<type>/<name>/install-inventory.yml")
    p_init.add_argument("--id", help="demo-team:demo-project")
    p_init.add_argument("--type", dest="team_type")
    p_init.add_argument("--name", dest="team_name")
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=cmd_init)

    p_miss = sub.add_parser("missing", help="列出未填必填项与提问话术")
    p_miss.add_argument("--id")
    p_miss.add_argument("--type", dest="team_type")
    p_miss.add_argument("--name", dest="team_name")
    p_miss.add_argument("--json", action="store_true")
    p_miss.set_defaults(func=cmd_missing)

    p_val = sub.add_parser("validate", help="同 missing，缺项 exit 1")
    p_val.add_argument("--id")
    p_val.add_argument("--type", dest="team_type")
    p_val.add_argument("--name", dest="team_name")
    p_val.add_argument("--json", action="store_true")
    p_val.set_defaults(func=cmd_validate)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
