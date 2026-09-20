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
