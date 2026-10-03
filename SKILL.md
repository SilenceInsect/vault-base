---
name: vault-base
description: >-
  团队知识金库底座：为任意业务 Skill 提供统一的知识检索、沉淀与共享链路。
  安装后用 skill_vault_adapt 扫描本机 skill，单点注入 SKILL.md 路由节（hooks/rules/preflight
  中心化管理，快速卸载），使业务 skill 走五步：Redis+MySQL 召回 → YAML 前置物料 →
  逐问完备 → 过程沉淀 → 人审/归档/Laya。触发词：知识金库、Vault、skill 适配、前置物料、
  沉淀知识、知识审核、Laya、金库环境自检。
---

# vault-base · 共享知识金库底座

本地 Vault（Obsidian MD）+ 共享金库（MySQL `amrd_qa_vault` + Redis 缓存/事件）。  
**不自建** Redis/MySQL；接入团队 208 公共环境。Redis 3.2.12 → 事件用 LIST 后端，不用 Stream。

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

接入 vault-base 的 Skill 先查本机技能索引，再叠读（不替换领域源）：

0. **技能索引**（谁的、什么技能、处理什么任务、任务元数据清单）
1. 共享金库（Redis 缓存 → MySQL `amrd_qa_vault`）
2. 本地 Vault `references/vault/**/*.md`
3. 领域金库（若 Skill 声明有 domain store，**沿用原读逻辑**，与上两层并行后再 merge）
4. 资料索引 / 需求原文
5. 向用户提问

索引是路由，不是另一套知识正文。访问共享金库前：

```bash
python "<skill_root>/scripts/skill_catalog.py" scan --write
python "<skill_root>/scripts/skill_catalog.py" lookup --skill-id <id> --json
```

`lookup` 用索引找出该技能的**历史元数据**（本地 brief/笔记 + 共享库里同 `skill_id` 的条目）和**当前源**（`SKILL.md`、输入 schema、领域库目录），合并成一张元数据清单后，只按合并结果里的技能身份和必须键去检索。项目里另有 `.cursor/skills/` 时，给 `scan` 加上 `--workspace <项目根>`。

索引落在 `references/vault/_kb/skill-catalog.md`（及同名 json）。含本机路径，不入共享候选。

详见 [references/collaboration-protocol.md](references/collaboration-protocol.md)。  
P0/P1 **禁止**把 Skill 默认读源改成公共库，避免资产管理混乱；P2 须 brief 显式切换并保留回滚。

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
python "<skill_root>/scripts/vault_scan.py" --user <账号> --skill-id <id> --enqueue
python "<skill_root>/scripts/vault_consume.py" --user <账号> --mode session --once
```

基线在**入队成功后**推进（`--enqueue`）；消费侧按 `event_id` 幂等。离线可降级本地，联网后补投。

## 运维

```bash
python "<skill_root>/scripts/env_probe.py" --json
python "<skill_root>/scripts/vault_admin.py" doctor
python "<skill_root>/scripts/vault_admin.py" stats
python "<skill_root>/scripts/vault_review.py" list
```

建库 DDL：`assets/ddl_mysql57.sql`（需 DBA 批复 X-4/X-5 后再在 208 执行）。

## 其他团队安装（七阶段 · 断档续装）

其他 team **必须**走 `scripts/install_flow.py`；禁止跳过清单直接 `env_ensure`。  
对照验收与逐步说明见 [docs/install.html](docs/install.html)。

| # | 阶段 | 命令 | 要点 |
|---|------|------|------|
| 1 | 选团队 | `phase1-team` | **amrd-test** 复用默认 Redis/MySQL（`assets/amrd-test.defaults.yml`）；其他团队逐项问答写配置 |
| 2 | 环境检查 | `phase2-env` | 列 Python / pymysql / pyyaml / laya / stuntd 版本与待下载清单；同意后 `--approve-install` |
| 3 | 库连接 | `phase3-db` | 测 Redis/MySQL；校验表结构；缺表时 `--approve-ddl` |
| 4 | skill 改造 | `phase4-skills` | 列出本机 skill；同意后 `--approve-adapt` |
| 5 | harness | `phase5-harness` | `HARNESS.md` + `install-manifest.json`（卸载/更新目录） |
| 6 | 冒烟 | `phase6-smoke` | 门禁 + WebPlatform 归档/人审/Laya 人工清单 |
| — | 续装/卸载 | `status` / `resume` / `uninstall-plan` | 进度：`teams/<type>/<name>/install-progress.json` |

```bash
python "<skill_root>/scripts/install_flow.py" phase1-team --id test-team:amrd-test
python "<skill_root>/scripts/install_flow.py" write-field --id test-team:amrd-test --field local.author --value "<域账号>"
python "<skill_root>/scripts/install_flow.py" phase2-env --id test-team:amrd-test
python "<skill_root>/scripts/install_flow.py" phase3-db --id test-team:amrd-test
python "<skill_root>/scripts/install_flow.py" phase4-skills --id test-team:amrd-test --approve-adapt --all
python "<skill_root>/scripts/install_flow.py" phase5-harness --id test-team:amrd-test
python "<skill_root>/scripts/install_flow.py" phase6-smoke --id test-team:amrd-test
python "<skill_root>/scripts/install_flow.py" resume --id test-team:amrd-test
```

底层仍可用 `install_bootstrap.py`。密码只进 `secrets.local.json`。  
模板：`assets/install-inventory.template.yml` · 字段：`references/install-inventory.md`

## 项目组私有信息（team-type:team-name）

逻辑 ID 例：`test-team:amrd-test` → 目录 `teams/test-team/amrd-test/`（Windows 路径不能含 `:`）。

```bash
python "<skill_root>/scripts/team_workspace.py" init --id "test-team:amrd-test"
python "<skill_root>/scripts/team_workspace.py" fix-ignores
```

- 说明：[teams/README.md](teams/README.md)
- 模板：`teams/_template/team.yml`
- **Git / SVN 默认忽略**各组目录内容；仅提交 README 与 `_template`
- 组私有信息**不要**当共享金库知识沉淀；可复用结论仍走 `vault_dump`

## 业务 Skill 接入（中心化注入 · 快速卸载）

装好 vault-base 后，**不会**自动让所有 skill 沉淀知识。须执行适配——目标 skill 目录**只改 SKILL.md 一个文件**（marker 路由节 + frontmatter 两行），hooks/taxonomy/rules/preflight 全部留在本 skill 目录，不向业务 skill 拷贝任何文件：

```bash
# 扫描本机 IDE + common-skills-repo 中的 skill
python "<skill_root>/scripts/skill_vault_adapt.py" scan --json

# 适配单个 / 全部业务 skill（幂等；v1 旧适配自动迁移）
python "<skill_root>/scripts/skill_vault_adapt.py" adapt --skill-id req-code-consistency
python "<skill_root>/scripts/skill_vault_adapt.py" adapt --all
```

注入内容与落点：

| 内容 | 落点 | 说明 |
|------|------|------|
| SKILL.md「知识金库接入」marker 节 + frontmatter `accepts_brief`/`vault_base` | **目标 skill（唯一改动）** | `<!-- vault-base:begin/end -->` 包裹，五步协议路由 |
| hooks（pre_task_recall / post_task_sediment） | `assets/skill-adapt/hooks/`（不复制） | 参数化 `--skill-dir/--skill-id`，按绝对路径调用 |
| preflight 清单 | `<skill_id>/preflight.yml` | vault 侧 per-skill 单目录，adapt 生成 |
| 适配账本 | `<skill_id>/adapted.json` | vault 侧 |
| 沉淀产物 | `<skill_id>/sediment/` | vault 侧，**卸载时保留** |
| taxonomy / rules | `assets/skill-adapt/`（单一副本） | 路由节内引用路径 |

### 快速卸载

```bash
python "<skill_root>/scripts/skill_vault_adapt.py" uninstall --skill-id <id>
python "<skill_root>/scripts/skill_vault_adapt.py" uninstall --skill-id <id> --purge-legacy
python "<skill_root>/scripts/skill_vault_adapt.py" uninstall --all
```

卸载 = 摘 SKILL.md marker 节 + frontmatter 两行 + 删 vault 侧 preflight/账本；沉淀知识保留。`--purge-legacy` 额外清理 v1 遗留注入文件（与模板一致才删，被改过的仅报告）。

### 跨 IDE 同名 skill（多副本）

知识层**单点汇总**：preflight / sediment / 账本的 key 是 `skill_id`（与 IDE 无关），全部收在 vault 侧 per-skill 单目录 `<skill_id>/`，跨 IDE 使用同名 skill 时读写同一份。注入层按副本处理：

- 同名 skill 在多个 IDE 各有**实体**副本 → `adapt` 给每个副本的 SKILL.md 注入路由节（`scan` 显示 `copies=N`）
- IDE 目录为**联接**（junction/symlink）→ 自动 resolve 到真相源，不重复注入
- 账本 `<skill_id>/adapted.json` 的 `copies` 数组记录各副本路径与来源

### 适配后 Agent 强制五步

1. **召回上下文**：`assets/skill-adapt/hooks/pre_task_recall.py` → Redis `vault:c:`（+ 可选 MySQL）  
2. **生成前置物料清单（YAML）**：`preflight_materials.py init`  
3. **自动填充 + 逐问完备**：`missing --json` → **一次只问一项**（Matt/grill 式 Skills）→ `write-field`  
4. **过程沉淀**：`assets/skill-adapt/hooks/post_task_sediment.py`（决策/链路/工具/置信度/HTML…）  
5. **持久化**：本地 YAML →（shared）enqueue → MySQL pending → 人审 → Redis；WebPlatform VaultBase 归档/清理/Laya 训练  

手工声明（未跑 adapt 时最低限度）：

```yaml
accepts_brief: true
vault_base: "~/common-skills-repo/vault-base"
```

第 0 步仍须 `readiness_gate` + brief；结案 `vault_dump` / `post_task_sediment`。

## 共享判定（Laya 本地，可选）

```bash
# 本机：pip install "laya[serve]" && laya-serve
python "<skill_root>/scripts/laya_client.py" probe
python "<skill_root>/scripts/laya_client.py" share-gate --mock --candidate-shared \
  --title "..." --question "..." --answer "..."
python "<skill_root>/scripts/laya_export_dataset.py" --seed-only
```

- 契约：[references/laya.md](references/laya.md)（部署 + 微调）
- 开启：`vault.config.json` → `laya.enabled=true`，`base_url=http://127.0.0.1:8000`
- 接入点：`vault_consume`（规则之后）；**永不**直接 approve

## 检索（prefixCache → MySQL → 本地）

```bash
# 仅 Redis：确认索引命中 + 知识召回
python "<skill_root>/scripts/vault_cache_ask.py" "你的问题"
python "<skill_root>/scripts/vault_cache_ask.py" -q "..." --skill-id <id> --json
python "<skill_root>/scripts/vault_cache_ask.py" --list-index

# 叠读 cache→MySQL→本地
python "<skill_root>/scripts/vault_recall.py" --skill-id <id> --query "..."
python "<skill_root>/scripts/vault_prefix_cache.py" probe
```

- Redis **`vault:c:`** = 知识 prefixCache（可丢）；**`vault:events:`** = 事件队列（勿混用）
- 契约：[references/prefix-cache.md](references/prefix-cache.md)
- 审核 `approve` 后写穿缓存；召回顺序见 collaboration-protocol §2

## 边界

| Skill | 操作对象 |
|-------|----------|
| `vault-base` | `amrd_qa_vault`、`vault:c:` 缓存、`vault:events:` 队列、`briefs/` |
| `amrd-qa-kb-maintenance` / bug-vault | `amrd_qa_kb`、`kb:` 前缀 |
| 业务 skill | 只读调用底座（优先 `vault_recall`），不直连改共享 schema |

**安装发现旧库**：若探测到 `amrd_qa_kb` / `kb:` / 本机 bug-vault，须按物料清单 `legacy_vaults` **纳入同步**（默认 `review_then_import`），禁止静默忽略。迁移完成前两套并存，互不抢写对方键前缀。

## 参考

- [references/readiness.md](references/readiness.md) · R1–R8
- [references/schema.md](references/schema.md) · MD 契约
- [references/collaboration-protocol.md](references/collaboration-protocol.md) · 多金库读写协作
- [references/prefix-cache.md](references/prefix-cache.md) · Redis prefixCache
- [references/laya.md](references/laya.md) · Laya 本地门控与微调
- [references/install-gate.md](references/install-gate.md) · 安装门禁 I1–I9
- [references/task-brief-schema.md](references/task-brief-schema.md) · 清单
- [references/env-matrix.md](references/env-matrix.md) · 环境检查
- [references/troubleshooting.md](references/troubleshooting.md) · 排查
- 安装手册：[docs/install.html](docs/install.html)
