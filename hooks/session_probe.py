#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""session_probe.py — 会话钩子：快速探测并写入 last_probe.json，永不抛错。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def main() -> int:
    try:
        sys.path.insert(0, str(SCRIPTS))
        from vault_paths import vault_root

        root = vault_root()
        probe_script = SCRIPTS / "env_probe.py"
        res = subprocess.run(
            [sys.executable, str(probe_script), "--quick", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if res.returncode in (0, 1) and res.stdout.strip():
            data = json.loads(res.stdout)
        else:
            data = {
                "ok": False,
                "error": res.stderr.strip()[:500] or "probe failed",
                "exit_code": res.returncode,
            }
        out = root / "_kb" / "last_probe.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        try:
            out = vault_root() / "_kb" / "last_probe.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False),
                           encoding="utf-8")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
