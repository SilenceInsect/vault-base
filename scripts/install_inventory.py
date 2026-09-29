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

# 各组模式公共必填
REQUIRED_TEAM = [
    "team.id",
    "team.team_type",
    "team.team_name",
    "common_skills_repo.path",
    "local.author",
]

# 本地模式 / local_then_shared 额外必填（点分路径）
REQUIRED_LOCAL_PATHS = [
    "install.skill_root",
    "install.vault_root",
    "install.python_exe",
]

# 共享模式额外必填（shared_full / local_then_shared）
REQUIRED_SHARED = [
    "shared_env.redis.host",
    "shared_env.mysql.host",
    "shared_env.mysql.user",
    "shared_env.mysql.database",
    "shared_env.secrets_path",
]

# 兼容旧名
REQUIRED_LOCAL = REQUIRED_TEAM + REQUIRED_LOCAL_PATHS

# 枚举字段：YAML 旁注 + 此处 + references/install-inventory.md 三处保持同步
FIELD_ENUMS = {
    "common_skills_repo.link_mode": {
        "values": ["junction", "symlink", "copy"],
        "default": "junction",
        "doc": "references/install-inventory.md §联接模式 link_mode",
        "help": (
            "junction=Win目录联接(推荐); symlink=符号链接; copy=仅拷贝(不推荐)"
        ),
    },
    "install.mode": {
        "values": ["local_only", "local_then_shared", "shared_full"],
        "default": "shared_full",
        "doc": "references/install-inventory.md §安装模式 install.mode",
        "help": (
            "shared_full=共享优先(Redis真相源,本地仓可跳过); "
            "local_only=仅本地降级; "
            "local_then_shared=显式双写(兼容)"
        ),
    },
}

QUESTIONS = {
    "team.id": "组逻辑 ID？（格式 team-type:team-name，例 test-team:amrd-test）",
    "team.team_type": "team-type？（例 test-team）",
    "team.team_name": "team-name？（例 amrd-test）",
    "install.skill_root": "vault-base 安装的绝对路径？（应等于 common-skills-repo/vault-base）",
    "install.vault_root": "本地 Vault 根路径？（默认可填 <skill_root>/references/vault）",
    "install.python_exe": (
        "Python 解释器绝对路径？（本机 ≥3.10 可复用；否则默认装 3.11.5+ 到 "
        "<skill_root>/Python，再回填此字段）"
    ),
    "common_skills_repo.path": "用户级 common-skills-repo 绝对路径？（默认 ~/common-skills-repo）",
    "common_skills_repo.link_mode": (
        "IDE 联接模式？junction(Win目录联接,推荐) / symlink(符号链接) / "
        "copy(仅拷贝,不推荐)。详见 references/install-inventory.md §联接模式"
    ),
    "install.mode": (
        "安装模式？shared_full(共享优先/Redis真相源，推荐) / "
        "local_only(仅本地降级) / local_then_shared(显式双写，兼容)。"
        "详见 references/install-inventory.md §安装模式"
    ),
    "install.python.enabled": (
        "是否按清单准备 Python？（默认 true；本机 ≥3.10 可复用，否则装 3.11.5+）"
    ),
    "install.python.install_path": (
        "Python 安装目录？（默认 <skill_root>/Python；本机复用时可留空）"
    ),
    "install.git.enabled": "是否按清单准备 Git？（默认 true；clone/增量扫描需要）",
    "install.git.install_path": "Git 安装目录？（默认 <skill_root>/Git）",
    "install.obsidian.enabled": (
        "是否安装 Obsidian 客户端？（默认 true；脚本读写 Vault 不依赖客户端）"
    ),
    "install.obsidian.install_path": (
        "Obsidian 安装目录？（默认 <skill_root>/Obsidian，即本 skill 目录下）"
    ),
    "install.svn.enabled": "是否安装 SVN 客户端？（默认 false；仅 svn_url/X7 时需要）",
    "install.svn.install_path": "SVN 安装目录？（默认 <skill_root>/SVN）",
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

# 访谈时额外确认项（有默认值，不阻塞 validate；skill 仍应询问）
INTERVIEW_OPTIONAL = [
    "common_skills_repo.link_mode",
    "install.python.enabled",
    "install.python.install_path",
    "install.git.enabled",
    "install.git.install_path",
    "install.obsidian.enabled",
    "install.obsidian.install_path",
    "install.svn.enabled",
]


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


def yaml_path(value: str | Path) -> str:
    """Windows 反斜杠在双引号 YAML 中会触发 \\U 等转义；统一为正斜杠。"""
    return str(value).replace("\\", "/")


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
    """按 install.mode 返回必填字段。

    - shared_full：团队/仓路径/作者 + 共享环境；vault_root / python_exe 非必填
    - local_only：团队 + 本地 skill/vault/python；不要求 Redis/MySQL
    - local_then_shared：本地路径 + 共享环境（兼容旧流程）
    """
    mode = str(_get(data, "install.mode") or "shared_full")
    if mode == "local_only":
        return list(REQUIRED_TEAM) + list(REQUIRED_LOCAL_PATHS)
    if mode == "shared_full":
        return list(REQUIRED_TEAM) + list(REQUIRED_SHARED)
    # local_then_shared 及其他：双写
    return list(REQUIRED_TEAM) + list(REQUIRED_LOCAL_PATHS) + list(REQUIRED_SHARED)


def missing_fields(data: dict) -> list[str]:
    return [k for k in required_for(data) if _empty(_get(data, k))]


def resolve_obsidian_install_path(data: dict) -> str:
    """enabled 默认 true；路径空则 <skill_root>/Obsidian。"""
    enabled = _get(data, "install.obsidian.enabled")
    if enabled is None:
        enabled = True
    if not enabled:
        return ""
    path = _get(data, "install.obsidian.install_path")
    if not _empty(path):
        return str(path).strip()
    skill = _get(data, "install.skill_root") or str(skill_root())
    return str(Path(str(skill)) / "Obsidian")


def _tool_enabled(data: dict, dotted: str, default: bool = True) -> bool:
    v = _get(data, dotted)
    if v is None:
        return default
    return bool(v)


def resolve_tool_install_path(
    data: dict, enabled_key: str, path_key: str, folder: str, *, default_enabled: bool = True
) -> str:
    if not _tool_enabled(data, enabled_key, default_enabled):
        return ""
    path = _get(data, path_key)
    if not _empty(path):
        return str(path).strip()
    skill = _get(data, "install.skill_root") or str(skill_root())
    return str(Path(str(skill)) / folder)


def local_tools_summary(data: dict) -> dict:
    """本机组件默认解析摘要（供 missing / 访谈展示）。"""
    return {
        "python": {
            "enabled": _tool_enabled(data, "install.python.enabled", True),
            "min_accept": _get(data, "install.python.min_accept") or "3.10",
            "default_version": _get(data, "install.python.default_version") or "3.11.5",
            "install_path": resolve_tool_install_path(
                data, "install.python.enabled", "install.python.install_path", "Python"
            ),
            "exe": _get(data, "install.python_exe") or "",
            "rule": "本机≥3.10可复用；否则默认装3.11.5+到skill目录",
        },
        "git": {
            "enabled": _tool_enabled(data, "install.git.enabled", True),
            "min_accept": _get(data, "install.git.min_accept") or "2.30.0",
            "default_version": _get(data, "install.git.default_version") or "2.47.1",
            "install_path": resolve_tool_install_path(
                data, "install.git.enabled", "install.git.install_path", "Git"
            ),
            "exe": _get(data, "install.git.exe") or "",
        },
        "pip": {
            "pymysql": {
                "enabled": _tool_enabled(data, "install.pip.pymysql.enabled", True),
                "default_version": _get(data, "install.pip.pymysql.default_version") or "(pip latest)",
                "install_path": "(python_exe site-packages)",
            },
            "pyyaml": {
                "enabled": _tool_enabled(data, "install.pip.pyyaml.enabled", True),
                "default_version": _get(data, "install.pip.pyyaml.default_version") or "(pip latest)",
                "install_path": "(python_exe site-packages)",
            },
        },
        "obsidian": {
            "enabled": _tool_enabled(data, "install.obsidian.enabled", True),
            "default_version": _get(data, "install.obsidian.default_version") or "latest",
            "install_path": resolve_obsidian_install_path(data),
        },
        "svn": {
            "enabled": _tool_enabled(data, "install.svn.enabled", False),
            "min_accept": _get(data, "install.svn.min_accept") or "1.14.0",
            "default_version": _get(data, "install.svn.default_version") or "1.14.5",
            "install_path": resolve_tool_install_path(
                data,
                "install.svn.enabled",
                "install.svn.install_path",
                "SVN",
                default_enabled=False,
            ),
        },
        "shared_not_local_install": {
            "redis": {
                "expected_version": _get(data, "shared_env.redis.expected_version") or "3.2.12",
                "install_path": None,
                "note": "团队208公共；本机不装 redis-server",
            },
            "mysql": {
                "expected_charset": _get(data, "shared_env.mysql.expected_charset") or "utf8mb4",
                "install_path": None,
                "note": "团队208公共；本机不装 mysqld，客户端用 pymysql",
            },
        },
    }


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

    # 正斜杠 + 单引号，避免 Windows 路径在双引号 YAML 中被解析为转义序列
    common = yaml_path(common_skills_repo())
    skill = yaml_path(skill_root())
    vault = yaml_path(skill_root() / "references" / "vault")
    py_home = yaml_path(skill_root() / "Python")
    git_home = yaml_path(skill_root() / "Git")
    obsidian = yaml_path(skill_root() / "Obsidian")
    svn_home = yaml_path(skill_root() / "SVN")
    py = yaml_path(sys.executable)
    replacements = {
        'id: ""': f"id: '{lid}'",
        'team_type: ""': f"team_type: '{t}'",
        'team_name: ""': f"team_name: '{n}'",
        'path: ""                       # 必填 默认 ~/common-skills-repo': (
            f"path: '{common}'                       # 必填 默认 ~/common-skills-repo"
        ),
        'skill_root: ""': f"skill_root: '{skill}'",
        'vault_root: ""': f"vault_root: '{vault}'",
        'python_exe: ""': f"python_exe: '{py}'",
        'secrets_path: ""': f"secrets_path: '{vault}/_kb/secrets.local.json'",
        'install_path: ""             # 默认 <skill_root>/Python；空则用该默认': (
            f"install_path: '{py_home}'             # 默认 <skill_root>/Python；空则用该默认"
        ),
        'install_path: ""             # 默认 <skill_root>/Git；空则用该默认': (
            f"install_path: '{git_home}'             # 默认 <skill_root>/Git；空则用该默认"
        ),
        'install_path: ""             # 默认 <skill_root>/Obsidian；空则用该默认': (
            f"install_path: '{obsidian}'             # 默认 <skill_root>/Obsidian；空则用该默认"
        ),
        'install_path: ""             # 默认 <skill_root>/SVN；空则用该默认': (
            f"install_path: '{svn_home}'             # 默认 <skill_root>/SVN；空则用该默认"
        ),
    }
    # only replace first empty occurrences carefully
    for old, new in replacements.items():
        text = text.replace(old, new, 1)
    today = datetime.date.today().isoformat()
    text = text.replace('updated_at: ""', f'updated_at: "{today}"', 1)
    text = _apply_detected_ide_links(text)
    dest.write_text(text, encoding="utf-8")
    print("logical_id:", lid)
    print("inventory:", dest)
    print("下一步: python scripts/install_inventory.py missing --id", lid)
    return 0


def _apply_detected_ide_links(text: str) -> str:
    """按本机是否安装 IDE 回填 ide_links.*（不臆造全 true）。"""
    try:
        from common_skills_repo import detect_ide_links
        links = detect_ide_links()
    except Exception as e:
        print("WARN: IDE 探测失败，保留模板 ide_links:", e, file=sys.stderr)
        return text
    for ide, on in links.items():
        val = "true" if on else "false"
        # 兼容单/双引号与无引号布尔
        pat = re.compile(
            r"(^[ \t]*" + re.escape(ide) + r":\s*)(?:true|false|'true'|'false'|\"true\"|\"false\")",
            re.M,
        )
        text, n = pat.subn(r"\g<1>" + val, text, count=1)
        if n == 0:
            print("WARN: 未找到 ide_links.%s 行，跳过回填" % ide, file=sys.stderr)
        else:
            print("ide_links.%s:" % ide, val, "(detected)")
    return text


def cmd_refresh_ide_links(args) -> int:
    """根据本机 IDE 安装情况重写清单中的 ide_links。"""
    t, n = parse_id(args.id) if args.id else (args.team_type, args.team_name)
    path = inventory_path(t, n)
    if not path.exists():
        print("清单不存在，先 init:", path, file=sys.stderr)
        return 2
    text = _apply_detected_ide_links(path.read_text(encoding="utf-8"))
    path.write_text(text, encoding="utf-8")
    print("updated:", path)
    return 0


def cmd_missing(args) -> int:
    t, n = parse_id(args.id) if args.id else (args.team_type, args.team_name)
    path = inventory_path(t, n)
    if not path.exists():
        print("清单不存在，先 init:", path, file=sys.stderr)
        return 2
    data = load_yaml(path)
    miss = missing_fields(data)
    tools = local_tools_summary(data)
    out = {
        "id": logical_id(t, n),
        "path": str(path),
        "mode": _get(data, "install.mode"),
        "missing": miss,
        "questions": [{"field": f, "ask": QUESTIONS.get(f, f"请提供 {f}")} for f in miss],
        "local_tools": tools,
        "field_enums": FIELD_ENUMS,
        "interview_optional": [
            {
                "field": f,
                "ask": QUESTIONS.get(f, f"请提供 {f}"),
                **({"enum": FIELD_ENUMS[f]} if f in FIELD_ENUMS else {}),
            }
            for f in INTERVIEW_OPTIONAL
        ],
    }
    if args.json:
        import json
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("清单:", path)
        print("模式:", out["mode"])
        py = tools["python"]
        print(
            "Python: enabled=%s min_accept=%s default=%s+ path=%s exe=%s"
            % (
                py["enabled"],
                py["min_accept"],
                py["default_version"],
                py["install_path"] or "(skip)",
                py["exe"] or "(unset)",
            )
        )
        print(
            "Git: enabled=%s default=%s path=%s"
            % (
                tools["git"]["enabled"],
                tools["git"]["default_version"],
                tools["git"]["install_path"] or "(skip)",
            )
        )
        print(
            "Obsidian: enabled=%s path=%s"
            % (tools["obsidian"]["enabled"], tools["obsidian"]["install_path"] or "(skip)")
        )
        print(
            "SVN: enabled=%s path=%s"
            % (tools["svn"]["enabled"], tools["svn"]["install_path"] or "(skip)")
        )
        if not miss:
            print("必填项已齐。可执行 env_ensure / readiness_gate。")
        else:
            print("缺 %d 项，请按序提问补齐：\n" % len(miss))
            for i, q in enumerate(out["questions"], 1):
                print("%d. [%s] %s" % (i, q["field"], q["ask"]))
        print("\n访谈确认（有默认，不阻塞 validate）：")
        for i, q in enumerate(out["interview_optional"], 1):
            print("%d. [%s] %s" % (i, q["field"], q["ask"]))
    return 0 if not miss else 1


def cmd_validate(args) -> int:
    return cmd_missing(args)


def _coerce_cli_value(raw: str):
    s = (raw or "").strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if s.lower() == "null":
        return None
    if s.isdigit():
        return int(s)
    try:
        if "." in s:
            return float(s)
    except ValueError:
        pass
    return s


def cmd_write_field(args) -> int:
    """写入点分字段（需 PyYAML；会重写文件并可能丢失旁注注释）。"""
    t, n = parse_id(args.id) if args.id else (args.team_type, args.team_name)
    path = inventory_path(t, n)
    if not path.exists():
        print("清单不存在，先 init:", path, file=sys.stderr)
        return 2
    if yaml is None:
        print("需要 PyYAML：pip install pyyaml", file=sys.stderr)
        return 2
    data = load_yaml(path)
    field = (args.field or "").strip()
    if not field:
        print("--field 必填", file=sys.stderr)
        return 2
    if field in FIELD_ENUMS:
        allowed = FIELD_ENUMS[field]["values"]
        val = _coerce_cli_value(args.value)
        if str(val) not in allowed:
            print(
                "枚举非法 %s=%r，允许: %s" % (field, val, ", ".join(allowed)),
                file=sys.stderr,
            )
            return 2
        _set(data, field, str(val))
    else:
        _set(data, field, _coerce_cli_value(args.value))
    miss = missing_fields(data)
    _set(data, "interview.missing_fields", miss)
    _set(
        data,
        "interview.status",
        "filled" if not miss else "asked",
    )
    _set(data, "interview.updated_at", datetime.date.today().isoformat())
    dump_yaml(data, path)
    print("updated:", path)
    print("field:", field, "=", _get(data, field))
    print("missing_count:", len(miss))
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="共享金库安装物料清单")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="生成 teams/<type>/<name>/install-inventory.yml")
    p_init.add_argument("--id", help="test-team:amrd-test")
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

    p_wf = sub.add_parser("write-field", help="写入点分字段并回写清单")
    p_wf.add_argument("--id")
    p_wf.add_argument("--type", dest="team_type")
    p_wf.add_argument("--name", dest="team_name")
    p_wf.add_argument("--field", required=True, help="点分路径，如 local.author")
    p_wf.add_argument("--value", required=True, help="字段值")
    p_wf.set_defaults(func=cmd_write_field)

    p_ide = sub.add_parser(
        "refresh-ide-links",
        help="按本机是否安装 Cursor/Claude/Codex/WorkBuddy 重写 ide_links",
    )
    p_ide.add_argument("--id")
    p_ide.add_argument("--type", dest="team_type")
    p_ide.add_argument("--name", dest="team_name")
    p_ide.set_defaults(func=cmd_refresh_ide_links)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
