#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jev_client.py — 兼容入口；实现已迁移至 laya_client（Laya / System One）。

请改用：python scripts/laya_client.py …
契约：references/laya.md（旧 references/jev.md 仅作跳转）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from laya_client import (  # noqa: E402
    DEFAULT_API_PATH,
    DEFAULT_BASE_URL,
    DEFAULT_LAYA_CFG as DEFAULT_JEV_CFG,
    DEFAULT_MODEL,
    LayaClient as JevClient,
    LayaError as JevError,
    laya_config as jev_config,
    laya_credentials as jev_credentials,
    main as _laya_main,
    open_laya as open_jev,
    share_gate_questions,
)

__all__ = [
    "DEFAULT_API_PATH",
    "DEFAULT_BASE_URL",
    "DEFAULT_JEV_CFG",
    "DEFAULT_MODEL",
    "JevClient",
    "JevError",
    "jev_config",
    "jev_credentials",
    "open_jev",
    "share_gate_questions",
]


if __name__ == "__main__":
    raise SystemExit(_laya_main())
