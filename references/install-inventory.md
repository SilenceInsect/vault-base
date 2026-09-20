# 安装物料清单字段说明（给人看；机器以 template 为准）

落盘：`teams/{team_type}/{team_name}/install-inventory.yml`  
模板：`assets/install-inventory.template.yml`

## 布局约定

| 英文短名 / 路径 | 中文含义 |
|-----------------|----------|
| `common-skills-repo` | 用户级公共 Skill 仓（真相源） |
| `junction` | Windows 目录联接（推荐） |
| `symlink` | 符号链接 |
| `copy` | 仅拷贝（易分叉，不推荐） |

默认仓根：`~/common-skills-repo`（可用 `COMMON_SKILLS_REPO` 覆盖）。  
`vault-base` 装在仓内；`~/.cursor|claude|codex/skills/vault-base` 只做联接。

## 安装模式 `install.mode`

| 英文短名 | 中文含义 |
|----------|----------|
| `local_only` | 仅本地 Vault，不连共享 Redis/MySQL |
| `local_then_shared` | 先本地可用，再补齐共享（推荐默认） |
| `shared_full` | 直接按共享全量安装（需 Redis+MySQL 就绪） |

## 协调状态 `coordination.*`

| 英文短名 | 中文含义 |
|----------|----------|
| `unknown` | 未知 / 未处理 |
| `requested` | 已向运维/DBA 申请 |
| `done` | 已完成 |
| `blocked` | 受阻，暂无法推进 |

共享写入前：`X4` / `X5` 至少为 `requested` 或 `done`。

## 访谈进度 `interview.status`

| 英文短名 | 中文含义 |
|----------|----------|
| `pending` | 待开始 |
| `asked` | 已提问 |
| `filled` | 已填齐 |
| `skipped` | 已跳过 |

## 必填项

本地可跑：
- `team.id` / `team_type` / `team_name`
- `common_skills_repo.path`
- `install.skill_root` / `install.vault_root` / `install.python_exe`
- `local.author`

要写共享 Redis/MySQL 时额外：
- `shared_env.redis.host` / `port`
- `shared_env.mysql.host` / `port` / `user` / `database`
- `shared_env.secrets_path`（密码只进 `secrets.local.json`）

敏感字段：`password` 类不得提交 SVN/Git；清单里可写 `<in_secrets>`。
