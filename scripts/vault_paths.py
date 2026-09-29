#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_paths.py — 路径与配置的唯一真相来源。

解析优先级（显式 > 环境变量 > 脚本位置推导），绝不使用隐式 cwd。
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

DEFAULT_CONFIG = {
    "schema_version": 1,
    "auto_share_judge": False,
    "event_backend": "redis_list",
    "cache_ttl_seconds": 172800,
    "retrieval": {"min_score": 0.35, "emit_low_confidence_hint": True},
    "redact": {"enabled": True, "force_private_on_hit": True},
    "degrade": {"probe_timeout_ms": 2000, "allow_offline": True},
    "laya": {
        "enabled": False,
        "mock": False,
        "model": "typed-decisions",
        "base_url": "http://127.0.0.1:8000",
        "api_path": "/v1/systemone",
        "timeout_seconds": 30,
        "share_min_confidence": 0.75,
        "sensitive_noul_threshold": 0.70,
        "require_api_key": False,
    },
}


def common_skills_repo() -> Path:
    """用户级公共 skill 仓根目录。"""
    env = os.environ.get("COMMON_SKILLS_REPO")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / "common-skills-repo").resolve()


def skill_root() -> Path:
    """返回 skill 根目录。

    优先级：
    1. COMMON_SKILLS_REPO/<skill_name>（若存在）
    2. ~/common-skills-repo/<skill_name>（若存在）
    3. 本脚本所在 skill 目录（IDE 联接或实体目录）
    """
    here = Path(__file__).resolve().parents[1]
    name = here.name
    for base in (common_skills_repo(), Path.home() / "common-skills-repo"):
        cand = Path(base).expanduser().resolve() / name
        if cand.is_dir():
            return cand
    return here


def vault_root(explicit: str | None = None) -> Path:
    """返回 Vault 根目录的绝对路径（不一定已存在）。

    1. 显式参数（命令行 --vault）
    2. 环境变量 VAULT_ROOT
    3. <skill_root>/references/vault（优先 common-skills-repo）

    shared_full 且未建本地仓时，路径可能不存在；调用方应用
    ``local_vault_available`` 判断是否叠读本地 MD。
    """
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("VAULT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return skill_root() / "references" / "vault"


def local_vault_available(root: Path | None = None) -> bool:
    """本地文件仓是否可用（目录存在即可；shared_full 可跳过本地仓）。"""
    r = root or vault_root()
    return r.is_dir()


def config_path(root: Path | None = None) -> Path:
    return (root or vault_root()) / "_kb" / "vault.config.json"


def load_config(root: Path | None = None) -> dict:
    """读取配置，缺失字段用默认值补齐（向后兼容）。"""
    p = config_path(root)
    cfg = dict(DEFAULT_CONFIG)
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    return cfg


def ensure_dirs(root: Path | None = None) -> Path:
    """幂等创建 Vault 目录骨架。"""
    root = root or vault_root()
    for sub in (
        "answers", "decisions", "references", "briefs", "briefs/_samples",
        "_kb", ".obsidian",
    ):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def write_default_config(root: Path | None = None) -> Path:
    """若缺失则写入 _kb/vault.config.json（不覆盖已有文件）。"""
    root = root or vault_root()
    p = config_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    return p


def local_ip() -> str:
    """探测本机内网 IP，失败返回 unknown，绝不抛异常。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except Exception:
        return "unknown"


def write_meta(root: Path | None = None, skill: str = "") -> Path:
    """写入数据契约声明（对标 bug-vault 已验证的 .vault-meta.json）。"""
    root = root or vault_root()
    meta = root / ".vault-meta.json"
    if not meta.exists():
        meta.write_text(json.dumps({
            "schema": DEFAULT_CONFIG["schema_version"],
            "skill": skill,
            "created": __import__("datetime").date.today().isoformat(),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta
