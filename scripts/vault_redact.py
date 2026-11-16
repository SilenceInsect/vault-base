#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_redact.py — 落盘前的敏感信息 / 本机环境闸门。

命中即返回 hit=True，调用方必须强制 is_candidate_shared=False。
只报类别与位置，不回显命中内容本身。

本机环境绑定（换人/换机即失效）与密钥同类：禁止进入公共金库。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 密钥 / 身份类
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("PRIVATE_IP", re.compile(r"\b(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)"
                              r"\.\d{1,3}\.\d{1,3}\b")),
    ("PHONE_CN", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("ID_CARD", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("BANK_CARD", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("SECRET_KV", re.compile(r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|"
                             r"access[_-]?key)\b\s*[:=]\s*\S{4,}")),
    ("CONN_STRING", re.compile(r"(?i)\b(?:jdbc|mongodb|redis|mysql|amqp)://\S+")),
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("PLAYER_ID_HINT", re.compile(r"(?i)(?:玩家|角色|账号|role_?id|player_?id|openid)"
                                  r"\s*[:=]\s*\w{6,}")),
]

# 本机环境绑定：个人盘符 / 家目录 / 显式私有标记 / 强提示词
MACHINE_PATTERNS: list[tuple[str, re.Pattern]] = [
    # E:\AM-RD_Server\... 或 E:/foo（不拦相对路径、不拦 http(s)/svn URL）
    ("WIN_ABS_PATH", re.compile(
        r"(?<![/\w@])[A-Za-z]:(?:\\|/)[^\s`\'\"<>|?*]+"
    )),
    ("UNIX_HOME_PATH", re.compile(
        r"(?:/Users|/home)/[A-Za-z0-9._-]+(?:/[^\s`\'\"<>|?]*)?"
    )),
    ("OWNER_SCOPED", re.compile(
        r"(?i)(?:scope\s*:\s*local_personal|\blocal-only\b|\bowner\s*:\s*\S+|"
        r"owner=[A-Za-z0-9._-]+)"
    )),
    ("MACHINE_ENV_HINT", re.compile(
        r"(?:本机路径|本机私有|本机默认工作区|我的工作区|仅本人|仅本机|"
        r"个人本机|禁止共享.*路径|machine-local|local_personal)"
    )),
]

PATTERNS: list[tuple[str, re.Pattern]] = SECRET_PATTERNS + MACHINE_PATTERNS

# approve / dump 共享侧统一识别「本机环境」类
MACHINE_CATEGORIES = frozenset(
    name for name, _ in MACHINE_PATTERNS
)


@dataclass
class RedactResult:
    hit: bool = False
    categories: list[str] = field(default_factory=list)
    detail: list[str] = field(default_factory=list)

    def as_note(self) -> str:
        if not self.hit:
            return ""
        machine = [c for c in self.categories if c in MACHINE_CATEGORIES]
        if machine and not [c for c in self.categories if c not in MACHINE_CATEGORIES]:
            return ("[本机环境闸门] 命中类别：%s。该文档绑定本机/使用者环境，"
                    "已强制标记为私有，禁止进入公共金库。共享侧请改写为可移植约定"
                    "（逻辑仓名 / SVN URL / 相对路径）。"
                    % "、".join(self.categories))
        return ("[敏感信息闸门] 命中类别：%s。该文档已被强制标记为私有，"
                "禁止共享。请确认是否需要脱敏后重新沉淀。"
                % "、".join(self.categories))

    @property
    def blocks_share(self) -> bool:
        return bool(self.hit)

    @property
    def machine_bound(self) -> bool:
        return any(c in MACHINE_CATEGORIES for c in self.categories)


def scan(text: str) -> RedactResult:
    res = RedactResult()
    for name, pat in PATTERNS:
        for m in pat.finditer(text or ""):
            res.hit = True
            if name not in res.categories:
                res.categories.append(name)
            line_no = (text or "").count("\n", 0, m.start()) + 1
            res.detail.append("%s@line%d" % (name, line_no))
    return res
