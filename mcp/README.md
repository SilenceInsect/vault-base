# vault-base · 最小 MCP（验证版）

把底座四类能力暴露为 MCP tools，协议层（五步 / skill 适配）仍由 Skill 负责。

| Tool | 对应脚本 | 用途 |
|------|----------|------|
| `vault_recall` | `vault_recall.py` | cache → MySQL → 本地召回 |
| `vault_dump` | `vault_dump.py` | 沉淀标准化 MD |
| `vault_readiness` | `readiness_gate.py` | R1–R8 门禁 |
| `vault_preflight` | `preflight_materials.py` | init / missing / write-field |

## 安装依赖

```bash
pip install -r mcp/requirements.txt
```

## Cursor 配置

本仓已带项目级 [`.cursor/mcp.json`](../.cursor/mcp.json)。重启 Cursor 或在 Settings → MCP 里刷新后可见 `vault-base`。

全局安装可把同一段拷到 `~/.cursor/mcp.json`，并把路径换成你的 skill 根：

```json
{
  "mcpServers": {
    "vault-base": {
      "command": "python",
      "args": ["${userHome}/common-skills-repo/vault-base/mcp/server.py"]
    }
  }
}
```

可选环境变量：`VAULT_AUTHOR`（dump 默认作者）、`COMMON_SKILLS_REPO`。

## 手工冒烟

```bash
# 确认 server 能起（stdio；无输入会挂起，Ctrl+C 即可）
python mcp/server.py

# 或直接测底层（不经 MCP 协议）
python -c "import runpy; runpy.run_path('mcp/server.py')"
python scripts/vault_recall.py --skill-id vault-base --query test --json
python scripts/readiness_gate.py --quick --json
```

在 Cursor Agent 对话里试：

1. 调用 `vault_readiness`（`quick=true`）
2. 调用 `vault_recall`（`skill_id=req-code-consistency`，`query=一致性`）
3. 调用 `vault_preflight`（`action=missing`，`skill_id=…`）
4. 调用 `vault_dump`（小标题私有沉淀，`candidate_shared=false`）

## 边界

- **不做**：skill 适配、事件队列、Laya、install_flow（后续可扩 tools）
- **写共享前**：`vault_readiness` 返回里 `capability.can_write_shared` 必须为 true
- dump 默认写本地；`candidate_shared=true` 仅标记候选，仍须人审链路
