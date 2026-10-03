# vault-base

团队共享知识金库底座（本地 Obsidian Vault + 可选共享 Redis/MySQL）。

## 快速安装

1. 打开手册：[`docs/install.html`](docs/install.html)
2. Clone 到用户级 common 仓并联接 IDE：

```bash
mkdir -p ~/common-skills-repo   # Windows: mkdir %USERPROFILE%\common-skills-repo
cd ~/common-skills-repo
git clone https://github.com/SilenceInsect/vault-base.git vault-base

python vault-base/scripts/common_skills_repo.py init
python vault-base/scripts/common_skills_repo.py link --skill vault-base --ides cursor,claude,codex --backup-existing
python vault-base/scripts/common_skills_repo.py status --skill vault-base
```

3. 由 skill 引导补齐安装物料清单（组目录默认被 ignore）：

```bash
python vault-base/scripts/install_inventory.py init --id "<team-type>:<team-name>"
python vault-base/scripts/install_inventory.py missing --id "<team-type>:<team-name>"
```

4. `env_ensure` → 填写本机 `secrets.local.json`（勿提交）→ `readiness_gate`
5. 共享开启后必跑安装门禁（I1–I8；勿只看 readiness 绿灯）：

```bash
python vault-base/scripts/install_gate.py --id "<team-type>:<team-name>" --require-shared --json
```

### 安装注意事项（历史阻断）

| 阻断 | 修复 | 门禁 |
|------|------|------|
| MySQL `meta_json` / `vault_events` 与 DDL 不符 | 以 `assets/ddl_mysql57.sql` 为准；store 用 `segments`/`vault_event` | I3/I5/I6 |
| `vault_consume` 从 `vault_paths` 导入 secrets | 改为 `vault_secrets.redis_conn` | I4 |
| Windows 清单 `"C:\Users\..."` YAML 解析失败 | 写 `'C:/Users/...'`；`init` 已自动规范化 | I2 |
| scan 未 `--enqueue` 或 MD 未 git commit | commit 后 `vault_scan.py --user … --enqueue` | 阶段 6 |
| `vault_consume` 空队列 `TimeoutError` | socket timeout ≥ BRPOP 等待；`consume_one` 吞超时 | I8 |

完整步骤与验收：[`docs/install.html`](docs/install.html)。  
功能总览（含 Laya / 召回 / 历史阻断）：[`docs/repo-function-analysis.html`](docs/repo-function-analysis.html)。

### Redis 职责

| 前缀 | 用途 |
|------|------|
| `vault:c:` | 知识 **prefixCache**（可丢；见 `references/prefix-cache.md`） |
| `vault:events:` 等 | 事件队列 |
| `kb:` | 领域旧库（本仓不写） |

召回：`python scripts/vault_recall.py --skill-id <id> --query "..."`

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
