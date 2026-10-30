#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcp_install.py — 安装最小 MCP 依赖并确保 Cursor mcp.json 配置。

用法：
  python mcp_install.py [--skill-root PATH] [--workspace PATH] [--global] [--json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import skill_root  # noqa: E402


SERVER_REL = Path("mcp") / "server.py"
REQ_REL = Path("mcp") / "requirements.txt"


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _dump_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pip_install_reqs(root: Path) -> dict:
    req = root / REQ_REL
    if not req.is_file():
        return {"ok": False, "error": "missing %s" % req}
    cp = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return {
        "ok": cp.returncode == 0,
        "rc": cp.returncode,
        "req": str(req),
        "stdout_tail": (cp.stdout or "")[-800:],
        "stderr_tail": (cp.stderr or "")[-800:],
    }


def ensure_mcp_json(path: Path, server_abs: Path) -> dict:
    """写入/合并 vault-base server 配置。优先用相对 workspace 的变量写法。"""
    data = _load_json(path)
    servers = data.setdefault("mcpServers", {})
    # 已有且指向本仓则保留
    entry = {
        "command": "python",
        "args": [str(server_abs).replace("\\", "/")],
    }
    # 若在 workspace .cursor 下，改用 ${workspaceFolder}
    try:
        if path.parent.name == ".cursor" and (path.parent.parent / "mcp" / "server.py").is_file():
            entry = {
                "command": "python",
                "args": ["${workspaceFolder}/mcp/server.py"],
            }
    except Exception:
        pass
    changed = servers.get("vault-base") != entry
    servers["vault-base"] = entry
    if changed or not path.is_file():
        _dump_json(path, data)
    return {"ok": True, "path": str(path), "changed": changed, "entry": entry}


def install_mcp(
    *,
    root: Path | None = None,
    workspace: Path | None = None,
    also_global: bool = False,
) -> dict:
    root = (root or skill_root()).resolve()
    server = root / SERVER_REL
    report: dict = {"skill_root": str(root), "server": str(server), "steps": []}
    if not server.is_file():
        report["ok"] = False
        report["error"] = "mcp/server.py missing"
        return report

    pip = pip_install_reqs(root)
    report["steps"].append({"pip": pip})
    if not pip.get("ok"):
        report["ok"] = False
        report["error"] = "pip install failed"
        return report

    configs = []
    # 仓内 .cursor/mcp.json（打开 vault-base 工作区时生效）
    configs.append(ensure_mcp_json(root / ".cursor" / "mcp.json", server))
    if workspace:
        ws = Path(workspace).expanduser().resolve()
        configs.append(ensure_mcp_json(ws / ".cursor" / "mcp.json", server))
    if also_global:
        g = Path.home() / ".cursor" / "mcp.json"
        # 全局用绝对路径
        data = _load_json(g)
        servers = data.setdefault("mcpServers", {})
        entry = {"command": "python", "args": [str(server).replace("\\", "/")]}
        changed = servers.get("vault-base") != entry
        servers["vault-base"] = entry
        if changed or not g.is_file():
            _dump_json(g, data)
        configs.append({"ok": True, "path": str(g), "changed": changed, "entry": entry})

    report["steps"].append({"mcp_json": configs})
    report["ok"] = True
    report["hint"] = "Cursor Settings → MCP 刷新后可见 vault-base"
    return report


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="安装 vault-base 最小 MCP")
    ap.add_argument("--skill-root", help="vault-base 根目录")
    ap.add_argument("--workspace", help="额外写入该工作区 .cursor/mcp.json")
    ap.add_argument("--global", dest="also_global", action="store_true",
                    help="同时写入 ~/.cursor/mcp.json（绝对路径）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    root = Path(args.skill_root).expanduser().resolve() if args.skill_root else skill_root()
    report = install_mcp(
        root=root,
        workspace=Path(args.workspace) if args.workspace else None,
        also_global=args.also_global,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
