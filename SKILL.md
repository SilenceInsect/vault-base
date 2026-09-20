---
name: vault-base
description: >-
  团队知识金库底座：为任意业务 Skill 提供统一的知识检索、沉淀与共享链路。
  当需要检索团队共享知识、把本次问答沉淀为 Obsidian 笔记、检查金库运行环境、
  或对共享知识做审核与运维时使用。触发词：知识金库、Vault、沉淀知识、共享知识、
  金库环境自检、知识审核、事件队列、DLQ、基线快照。
---

# vault-base · 共享知识金库底座

本地 Vault（Obsidian MD）+ 共享金库（MySQL `amrd_qa_vault` + Redis 缓存/事件）。  
**不自建** Redis/MySQL；接入团队共享环境 公共环境。Redis 3.2.12 → 事件用 LIST 后端，不用 Stream。

## Skill 根目录与 common-skills-repo

**真相源**：`~/common-skills-repo/vault-base/`（可用环境变量 `COMMON_SKILLS_REPO` 覆盖仓根）。  
各 IDE 目录应为联接，而非各自拷贝：

| IDE | 联接路径 |
|-----|----------|
| Cursor | `~/.cursor/skills/vault-base` |
| Claude / 部分 Codex | `~/.claude/skills/vault-base` |
| Codex | `~/.codex/skills/vault-base` |

```bash
python "<skill_root>/scripts/common_skills_repo.py" init
python "<skill_root>/scripts/common_skills_repo.py" migrate --skill vault-base --from-ide cursor --replace-source
python "<skill_root>/scripts/common_skills_repo.py" link --skill vault-base --ides cursor,claude,codex --backup-existing
python "<skill_root>/scripts/common_skills_repo.py" status --skill vault-base
```

`<skill_root>` 优先解析到 common 仓；Vault 默认：`<skill_root>/references/vault`。

## 第 0 步（任何金库任务前）

```bash
python "<skill_root>/scripts/env_ensure.py"
python "<skill_root>/scripts/readiness_gate.py" --fix --json
```

- 有 **BLOCKER** → 中止并按 `fix` 修复；`can_write_shared=false` 时**禁止**一切共享侧写入。
- 层 2.5：读 `briefs/{skill_id}.md`；缺失则 `brief_bootstrap.py` / `brief_interview.py`。

## 检索顺序（强制）

1. 共享金库（Redis 缓存 → MySQL）
2. 本地 Vault `references/vault/**/*.md`
3. 资料索引 / 需求原文
4. 向用户提问

## 沉淀

```bash
python "<skill_root>/scripts/vault_dump.py" --skill-id <id> --doc-type answer \
  --title "..." --question "..." --answer "..." [--shared]
```

- 单问答单 MD；追问追加「备注」。
- 落盘前走 `vault_redact`；命中敏感信息强制 `is_candidate_shared=false`。
- 凭据只允许 `_kb/secrets.local.json`（须 SVN/Git 忽略），禁止写入本 SKILL.md。

## 增量与事件（共享侧）

```bash
python "<skill_root>/scripts/vault_scan.py" --user <账号>
python "<skill_root>/scripts/vault_queue.py" ...
python "<skill_root>/scripts/vault_consume.py" --user <账号> --mode session --once
```

基线在**入队成功后**推进；消费侧按 `event_id` 幂等。离线可降级本地，联网后补投。

## 运维

```bash
python "<skill_root>/scripts/env_probe.py" --json
python "<skill_root>/scripts/vault_admin.py" doctor
python "<skill_root>/scripts/vault_admin.py" stats
python "<skill_root>/scripts/vault_review.py" list
```

建库 DDL：`assets/ddl_mysql57.sql`（需 DBA 批复 X-4/X-5 后再在共享 MySQL 执行）。

## 其他团队安装（引导补齐物料清单）

其他 team 安装时，本 skill **必须先引导建 common 仓 + IDE 联接 + 补齐物料清单**，再跑 env_ensure。

1. 确认用户级 `common-skills-repo`（默认 `~/common-skills-repo`），执行 `common_skills_repo.py init`
2. 将 `vault-base` 装入 common 仓，并对本机 IDE 建联接（`link --ides cursor,claude,codex`）
3. 确认/创建组：`team-type:team-name`（例 `demo-team:demo-project`）
4. 初始化清单并列出缺项：

```bash
python "<skill_root>/scripts/install_inventory.py" init --id "demo-team:demo-project"
python "<skill_root>/scripts/install_inventory.py" missing --id "demo-team:demo-project"
```

5. 按 `missing` 输出的问题**逐项问用户**，写入  
   `teams/<type>/<name>/install-inventory.yml`（组目录默认 Git/SVN 忽略）
6. 密码只进 `references/vault/_kb/secrets.local.json`，清单里可写 `<in_secrets>`
7. `validate` 通过后再：`env_ensure` → `readiness_gate` →（共享）DDL/探测

模板：`assets/install-inventory.template.yml`  
说明：`references/install-inventory.md`

## 项目组私有信息（team-type:team-name）

逻辑 ID 例：`demo-team:demo-project` → 目录 `teams/demo-team/demo-project/`（Windows 路径不能含 `:`）。

```bash
python "<skill_root>/scripts/team_workspace.py" init --id "demo-team:demo-project"
python "<skill_root>/scripts/team_workspace.py" fix-ignores
```

- 说明：[teams/README.md](teams/README.md)
- 模板：`teams/_template/team.yml`
- **Git / SVN 默认忽略**各组目录内容；仅提交 README 与 `_template`
- 组私有信息**不要**当共享金库知识沉淀；可复用结论仍走 `vault_dump`

## 业务 Skill 接入

在业务 skill 的 frontmatter：

```yaml
accepts_brief: true
vault_base: "~/common-skills-repo/vault-base"
```

（IDE 联接就绪后，`~/.cursor/skills/vault-base` 等路径等价指向同一目录。）

并在其「第 0 步」调用本底座的 `readiness_gate` + 注入 `briefs/{skill_id}.md`；结案调用 `vault_dump`（或业务侧等价沉淀脚本，再经 scan 入队）。

## 边界

| Skill | 操作对象 |
|-------|----------|
| `vault-base` | `amrd_qa_vault`、`vault:` 键前缀、`briefs/` |
| `amrd-qa-kb-maintenance` / bug-vault | `amrd_qa_kb`、`kb:` 前缀 |
| 业务 skill | 只读调用底座，不直连改共享 schema |

## 参考

- [references/readiness.md](references/readiness.md) · R1–R8
- [references/schema.md](references/schema.md) · MD 契约
- [references/task-brief-schema.md](references/task-brief-schema.md) · 清单
- [references/env-matrix.md](references/env-matrix.md) · 环境检查
- [references/troubleshooting.md](references/troubleshooting.md) · 排查
- 安装手册：[docs/install.html](docs/install.html)
