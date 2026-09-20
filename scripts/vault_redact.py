#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_redact.py — 落盘前的敏感信息闸门。

命中即返回 hit=True，调用方必须强制 is_candidate_shared=False。
只报类别与位置，不回显命中内容本身。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PATTERNS: list[tuple[str, re.Pattern]] = [
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


@dataclass
class RedactResult:
    hit: bool = False
    categories: list[str] = field(default_factory=list)
    detail: list[str] = field(default_factory=list)

    def as_note(self) -> str:
        if not self.hit:
            return ""
        return ("[敏感信息闸门] 命中类别：%s。该文档已被强制标记为私有，"
                "禁止共享。请确认是否需要脱敏后重新沉淀。"
                % "、".join(self.categories))


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
