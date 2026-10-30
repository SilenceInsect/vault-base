# vault-base

团队共享知识金库底座（本地 Obsidian Vault + 可选共享 Redis/MySQL）。

## 快速安装

**clone ≠ 安装完成。** 推荐一站式 CLI（内部仍走七阶段门禁）：

1. Clone 到用户级 common 仓：

```bash
mkdir -p ~/common-skills-repo   # Windows: mkdir %USERPROFILE%\common-skills-repo
cd ~/common-skills-repo
git clone https://github.com/SilenceInsect/vault-base.git vault-base
```

2. 一站式安装（**只需这一条**；开头决策一次并落盘，续装同命令不再问）：

```bash
cd vault-base
python scripts/install_all.py
```

向导一次问：团队 ID、共享/仅本地、secrets 路径（若需要）、是否启用 MCP。  
pip / DDL / skill 适配在确认后自动同意。断档续跑仍执行：

```bash
python scripts/install_all.py
```

重做决策：`python scripts/install_all.py --redecide`  
CI 无交互：`python scripts/install_all.py --assume-defaults --non-interactive`（可加 `--secrets` / `--local-only`）

3. 手册与流程图：[`docs/install.html`](docs/install.html) · [`docs/skill-lifecycle-flow.html`](docs/skill-lifecycle-flow.html)

4. 共享开启后仍建议核对安装门禁（I1–I9；勿只看 readiness 绿灯）：

```bash
python scripts/install_gate.py --id test-team:amrd-test --require-shared --json
```

分步安装（link / inventory / phase1…6）仍可用；日常优先 `install_all`。

### 安装注意事项（历史阻断）

| 阻断 | 修复 | 门禁 |
|------|------|------|
| MySQL `meta_json` / `vault_events` 与 DDL 不符 | 以 `assets/ddl_mysql57.sql` 为准；store 用 `segments`/`vault_event` | I3/I5/I6 |
| `vault_consume` 从 `vault_paths` 导入 secrets | 改为 `vault_secrets.redis_conn` | I4 |
| Windows 清单 `"C:\Users\..."` YAML 解析失败 | 写 `'C:/Users/...'`；`init` 已自动规范化 | I2 |
| scan 未 `--enqueue` 或 MD 未 git commit | commit 后 `vault_scan.py --user … --enqueue` | 阶段 6 |
| `vault_consume` 空队列 `TimeoutError` | socket timeout ≥ BRPOP 等待；`consume_one` 吞超时 | I8 |

完整步骤与验收：[`docs/install.html`](docs/install.html)。  
安装→使用流程图：[`docs/skill-lifecycle-flow.html`](docs/skill-lifecycle-flow.html)。  
功能总览（含 Laya / 召回 / 历史阻断）：[`docs/repo-function-analysis.html`](docs/repo-function-analysis.html)。

### Redis 职责

| 前缀 | 用途 |
|------|------|
| `vault:c:` | 知识 **prefixCache**（可丢；见 `references/prefix-cache.md`） |
| `vault:events:` 等 | 事件队列 |
| `kb:` | 领域旧库（本仓不写） |

召回：`python scripts/vault_recall.py --skill-id <id> --query "..."`

### 最小 MCP（验证版）

四工具面：`vault_recall` / `vault_dump` / `vault_readiness` / `vault_preflight`。  
见 [`mcp/README.md`](mcp/README.md)。项目内启用：复制或合并 [`.cursor/mcp.json`](.cursor/mcp.json) 后重启/刷新 MCP。

## 公开仓边界

| 可提交 | 勿提交 |
|--------|--------|
| 脚本、模板、DDL、手册 | 真实 host / 密码 |
| `teams/_template/` | `teams/<真实组>/`、`secrets.local.json` |
| sample briefs | 业务 answers / decisions |

凭据只放 `references/vault/_kb/secrets.local.json`。共享环境地址向团队内部索取，本仓仅占位符。

## 布局

```
~/common-skills-repo/vault-base/     # 真相源（本仓库）
~/.cursor/skills/vault-base          # junction → 真相源
~/.claude/skills/vault-base
~/.codex/skills/vault-base
```
