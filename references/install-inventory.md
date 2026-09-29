# 安装物料清单字段说明（给人看；机器以 template 为准）

落盘：`teams/{team_type}/{team_name}/install-inventory.yml`  
模板：`assets/install-inventory.template.yml`

## 布局约定

| 英文短名 / 路径 | 中文含义 |
|-----------------|----------|
| `common-skills-repo` | 用户级公共 Skill 仓（真相源） |

默认仓根：`~/common-skills-repo`（可用 `COMMON_SKILLS_REPO` 覆盖）。  
`vault-base` 装在仓内；`~/.cursor|claude|codex/skills/vault-base` 只做联接。

## 联接模式 `link_mode`

对应字段：`common_skills_repo.link_mode`  
YAML 旁注与模板同步：`assets/install-inventory.template.yml`、`teams/_template/install-inventory.yml`  
实现：`scripts/common_skills_repo.py`（`create_junction` / `link`）

| 枚举值 | 含义 | 何时用 |
|--------|------|--------|
| `junction` | Windows 目录联接（`mklink /J`） | **默认推荐**；通常无需管理员 |
| `symlink` | 符号链接 | 跨平台；Windows 常需开发者模式或管理员 |
| `copy` | 仅拷贝到 IDE skills 目录 | **不推荐**；易与真相源分叉 |

约定：枚举字段必须在 YAML **字段旁保留注释块**（取值表 + 本小节出处），不能只写裸值让用户猜。

## IDE 联接开关 `ide_links`

对应字段：`common_skills_repo.ide_links.{cursor,claude,codex,workbuddy}`  

| 规则 | 说明 |
|------|------|
| 按本机安装探测 | `true` = 本机已安装/在用该 IDE；`false` = 未装，不建联接 |
| 禁止默认全 true | 模板默认全 `false`；`init` / `refresh-ide-links` 探测后回填 |
| 勿仅凭 skills 目录 | `~/.xxx/skills` 可能由联接脚本创建，不算「已安装」 |

```bash
python scripts/install_inventory.py refresh-ide-links --id "<team-type>:<team-name>"
python scripts/common_skills_repo.py link --skill vault-base --ides auto --backup-existing
```

探测实现：`scripts/common_skills_repo.py` → `detect_ide_links()`。

## 安装模式 `install.mode`

**决策规则（共享优先 / 本地降级）**：`install_bootstrap.py probe-connection` 探测共享 Redis+MySQL；可达且凭据可解析 → 写回 `shared_full`；否则 → `local_only`。模板默认意图为 `shared_full`。

| 英文短名 | 中文含义 | 必填要点 |
|----------|----------|----------|
| `shared_full` | **默认意图**：共享 Redis+MySQL；运行时真相源=`vault:c:`；本地 `vault_root` **可空**（跳过文件仓） | `shared_env.*` + team + `common_skills_repo.path` + `local.author` |
| `local_only` | 无共享时降级：用户目录本地文件仓 | `install.skill_root` / `vault_root` / `python_exe`；不要求 Redis/MySQL |
| `local_then_shared` | 显式双写（兼容旧流程，非默认） | 本地路径 + 共享环境均必填 |

编排入口：`python scripts/install_bootstrap.py run --id <team-type:team-name>`。

## 本机组件约定（`install.python` / `git` / `pip` / `obsidian` / `svn`）

每个本机组件统一字段：

| 字段 | 含义 |
|------|------|
| `enabled` | 是否纳入安装/校验 |
| `min_accept` | 本机已有版本 ≥ 此值则**复用**，不强制重装 |
| `default_version` | 需新装时的目标版本 |
| `install_path` | 新装目录；默认在 `<skill_root>/<Name>` 下（pip 包除外） |

**Python 特例**：默认新装 `3.11.5+`（`default_version: 3.11.5`）；本机已有 **≥3.10**（`min_accept`）即可复用。解析后的解释器写入 `install.python_exe`。

**pip 包**（`pymysql` / `pyyaml`）：无独立 `install_path`，装进当前 `python_exe`；`default_version` 空表示 pip 最新。`env_ensure` 可自动补齐。

**共享 Redis/MySQL**：团队 208 公共环境，**本机不安装服务端**，故无本机 `install_path`；清单用 `shared_env.*.host` + `expected_version` / `expected_charset` 记录约定。

## 旧知识金库 `legacy_vaults`（安装后发现则须纳入）

安装 / `env_probe` 之后若发现旧库（默认探测 `amrd_qa_kb` + Redis `kb:` + 本机 bug-vault 路径），**不得静默忽略**：

| 字段 | 含义 | 默认 |
|------|------|------|
| `discover_on_install` | 是否扫描 | `true` |
| `sync_required_if_found` | 发现则必须纳入或显式 skip | `true` |
| `sources[].sync_policy` | `review_then_import` / `import_pending` / `skip` | `review_then_import` |
| `sources[].local_path` | 本地 MD 根（如 `<local-kb-root>/bug-vault`） | 探测回填 |

纳入原则：
- **先映射进 pending / 审核**，不要直接冲垮 `shared_vault_main`。
- bug ticket（仅元数据、无正文）与 mechanism note（有正文）分开策略；锚点大索引（`kb_anchor`）可分期迁。
- 旧库 `amrd_qa_kb` / `kb:` 在迁移完成前仍由 `amrd-qa-kb-maintenance` 维护；vault-base **不抢写** `kb:` 键。
- `vault_admin.py migrate` 为正式迁移入口（实现前 checklist 保持未完成）。

验收：`checklist.legacy_vaults_scanned` + `legacy_vaults_synced_or_skipped`。

## 环境覆盖矩阵（安装 skill 所需）

| 环境项 | 矩阵 ID | 清单位置 | 默认版本 | 默认安装路径 | 备注 |
|--------|---------|----------|----------|--------------|------|
| Python | E1 | `install.python` + `python_exe` | 新装 3.11.5+；接受 ≥3.10 | `<skill_root>/Python` | 必填 |
| pymysql | E2 | `install.pip.pymysql` | pip 最新（空） | （进 python_exe） | 共享写入需要 |
| Git | E3 | `install.git` | 2.47.1；接受 ≥2.30.0 | `<skill_root>/Git` | clone/增量扫描 |
| Vault 目录 | E4 | `install.vault_root` | — | `<skill_root>/references/vault` | 路径即落点 |
| secrets | E5 | `shared_env.secrets_path` | — | `…/vault/_kb/secrets.local.json` | 共享模式 |
| Redis | E6–E11 | `shared_env.redis` | 服务端约定 3.2.12 | **无本机安装** | 208 公共 |
| MySQL | E7/E9 | `shared_env.mysql` | charset utf8mb4 | **无本机安装** | 208 公共；客户端=pymysql |
| Obsidian | （UX） | `install.obsidian` | latest | `<skill_root>/Obsidian` | 脚本不依赖进程 |
| PyYAML | （清单写回） | `install.pip.pyyaml` | pip 最新（空） | （进 python_exe） | 推荐 |
| SVN | （可选） | `install.svn` | 1.14.5；接受 ≥1.14.0 | `<skill_root>/SVN` | 默认 `enabled=false` |
| IDE 本体 | — | 仅 `ide_links` | — | — | **不代装** Cursor/Claude/Codex |

二进制目录（`Python/` `Git/` `Obsidian/` `SVN/`）须 gitignore，勿提交。

## Obsidian 客户端 `install.obsidian`

| 字段 | 含义 | 默认 |
|------|------|------|
| `enabled` | 是否安装 Obsidian 客户端 | `true` |
| `default_version` | 目标版本 | `latest` |
| `install_path` | 客户端安装目录 | `<skill_root>/Obsidian` |

访谈时须问清「是否安装 Obsidian」（默认是），路径无特殊要求则用默认。

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
