# Skill × 多金库协作协议（v1）

适用范围：业务 Skill、领域金库（如 bug-vault / `amrd_qa_kb`）、公共金库底座（vault-base / `amrd_qa_vault`）。

目标：**不拆掉领域 Skill 原有读库逻辑**，又能读到公共知识；**写路径可审计**；避免资产归属混乱。

---

## 1. 角色与资产归属

| 角色 | 存什么 | 键/库 | 谁维护 |
|------|--------|-------|--------|
| **领域金库** Domain Vault | 该 Skill 专属资产（bug note、锚点、发号、领域模板） | 例 `kb:` / `amrd_qa_kb` / 本机 `bug-vault/` | 领域 skill（如 amrd-qa-kb-maintenance） |
| **公共金库** Shared Vault | 跨 Skill 可复用结论（answer/decision/reference） | `vault:` / `amrd_qa_vault` + 本地 `references/vault` | vault-base + 审核流 |
| **Skill 本地私货** | 组私有、机器路径、凭据指针 | `teams/`、secrets | 不进共享 |

一条知识**有且仅有一个主归属（owner_store）**；公共库里的副本带 `source_ref` / `source_task_id` 指回领域或会话，**不是第二套主人**。

禁止：

- 业务 Skill 直连改 `amrd_qa_vault` schema 或乱写对方 `kb:` 键
- 把领域锚点全量当「公共答案」灌进 `shared_vault_main`
- 静默把读写切到 SQLite 却仍写共享 Redis（R4）

---

## 2. 读 / 召回协议（Read · Recall）

### 2.1 默认原则：**叠读，不替换源**

对已有领域 Skill：

1. **原领域读库逻辑保持不变**（继续读 bug-vault / `kb:` 等）。
2. **额外**通过 vault-base 召回适配器读公共金库（共享 → 底座本地 Vault）。
3. 合并结果时打 **来源标签**，调用方可见：`domain` | `shared` | `local_vault_base`。

不要求第一天改掉领域 Skill 的「金库源配置」；改源属于显式迁移里程碑，见 §5。

### 2.2 召回顺序（接入 vault-base 的 Skill 强制）

先读本机技能索引，再叠读。索引不代替下面各层的正文。

```
0. 技能索引     skill_catalog（谁 / 技能 / 任务 / 元数据清单）
A. 公共共享层   Redis prefixCache(`vault:c:`) → MySQL amrd_qa_vault（仅 active）
B. 底座本地层   vault-base/references/vault/**/*.md
C. 领域金库层   该 Skill 原逻辑（可选；未声明则跳过）
D. 资料/需求原文
E. 向用户提问
```

实现入口：`scripts/vault_recall.py`（A+B）；缓存契约：`references/prefix-cache.md`。  
注意：`vault:events:` 是事件队列，**不是**知识缓存。

索引用法（`scripts/skill_catalog.py`）：

1. `scan --write` 扫描本机 IDE 安装（及 `--workspace` 下的项目技能），理解每个 `SKILL.md`：归属、任务、元数据清单、当前源路径。
2. 业务 Skill 访问共享库前 `lookup --skill-id`：
   - **历史元数据**：本地 Vault 中 `skill_id` 命中的 brief/笔记，加上共享库里同技能条目的 meta 键。
   - **当前源**：该技能现在的 `SKILL.md`、`references` 里的输入 schema、本技能领域库目录。
   - **合并**：同一键在两边都有则标 both；只在历史里出现的键保留为 historical_only，避免当前源把旧字段弄丢。
   - **再检索**：`lookup_plan` 只带技能身份、必须元数据键、以及合并后仍有效的历史键。不要跳过索引对共享库做无差别全文扫。

说明：

- **A+B** 由 vault-base 统一提供 API/脚本；领域 Skill **不必**自己解析 MySQL DDL。
- **C** 仍走领域原脚本（如 `vault_query.py` / `kbctl`），避免资产索引混乱。
- 结果去重键优先：`uuid` → `claim_key` → `content_hash` → 标题近似；冲突时 **shared active 优先于 domain 未审核 ticket**，领域 **已标定 mechanism/note 优先于** 仅有标题的 inbox ticket。

### 2.3 「不改读逻辑」时如何读到公共库？

| 做法 | 说明 |
|------|------|
| **推荐：召回门面** | Skill 第 0 步调用 `vault-base` 检索入口（或业务封装的 `recall(skill_id, query)`），与原领域检索**并行**，再 merge |
| 不推荐：改领域 store 默认连接 | 易导致发号/锚点/查重仍写 `kb:`、读却以为在 `vault:`，资产管理错乱 |
| 禁止：只改读不改写且混用同一表 | 半迁移状态最危险 |

结论：**都读（叠读）**，不是「只读旧的」也不是「只读新的」。旧逻辑继续服务领域资产；新通道服务公共资产。

---

## 3. 写 / 沉淀协议（Write）

### 3.1 写入路由

| 内容类型 | 写到哪里 | 方式 |
|----------|----------|------|
| 领域专属（bug 正文、锚点、发号） | **领域金库** | 原 skill 写入链路不变 |
| 可复用结论 / 决策 / 规范摘要 | **先本地 vault-base MD**（`vault_dump`） | `is_candidate_shared` 默认 false |
| 候选共享 | 事件入队 → **pending** | scan/queue/consume；**禁止**业务直写 `shared_vault_main` |
| 正式公共 | `shared_vault_main` | 仅审核通过（approve） |

### 3.2 修改写入金库的规范

- **改写目标**只允许两种显式声明（写在 skill frontmatter 或 brief）：
  - `write_stores: [domain]` — 只写领域（默认兼容旧 skill）
  - `write_stores: [domain, vault_base_local]` — 领域 + 底座本地沉淀
  - `write_stores: [domain, vault_base_local, shared_candidate]` — 允许打共享候选（仍不直写 main）
- **永远不要**把 `shared_main` 配成业务 Skill 的默认可写 store。
- 脱敏：`vault_redact` 命中 → 强制不可共享。
- 身份：R4 未过 → `can_write_shared=false`，共享侧写拒绝。

### 3.3 双写禁止项

- 同一 `uuid` 在 `kb_note` 与 `shared_vault_main` **同时当主副本修改**（只允许：领域主 + 公共只读副本，或迁移后公共主 + 领域归档）。
- 用公共库发号器发 `BUG-YYYY-NNNN`（发号仍归领域）。

---

## 4. 公共金库规范（Shared）

- 契约：`schema_version`、`doc_type`、`claim_key`、`share_rev`、`content_hash`（见 schema.md）。
- 状态机：`pending → approved(active) | rejected`；替换走 `supersede`，不物理删。
- 检索只默认返回 `status=active`；`superseded/archived` 需显式参数。
- 来源追溯：从领域迁入的条目必须带 `source_ref`（路径或 note_id）与原 `skill_id`。
- 旧库纳入：`legacy_vaults` + `review_then_import`（见 install-inventory.md）。

---

## 5. 迁移与「改读取源」里程碑（防资产混乱）

分三阶段，**禁止跳阶段改默认读源**：

| 阶段 | 读 | 写 | 资产 |
|------|----|----|------|
| **P0 叠读**（当前目标） | 领域原逻辑 + vault-base 召回 | 领域照旧；可复用走 dump | 两套并存，键前缀隔离 |
| **P1 候选导入** | 同 P0 | 领域→映射 pending | 公共出现只读副本 |
| **P2 读源切换**（可选） | 某类查询改以 public 为主 | 该类停止写领域主库 | 需清单勾选 + 回滚开关 |

P2 准入：

- 该类语料已在 public 标定/抽检通过
- `legacy_vaults_synced_or_skipped=true`
- brief 写明 `read_primary: shared` 与回滚为 `domain`

未满足 P2 时改 Skill「默认金库源」= **制造资产管理混乱**，协议禁止。

---

## 6. Skill 接入清单（最小）

业务 Skill 接入时声明（frontmatter / brief）：

```yaml
accepts_brief: true
vault_base: "~/common-skills-repo/vault-base"
vault_collab:
  domain_store: "bug-vault"          # 或 none
  recall: ["shared", "vault_base_local", "domain"]  # 顺序可裁剪，不可颠倒 shared 优先原则除非 brief 声明
  write_stores: ["domain", "vault_base_local"]
  read_primary: "domain"             # P0/P1 保持 domain；仅 P2 可改 shared
```

第 0 步：`readiness_gate`；召回走协议 §2；结案 `vault_dump`（可共享结论）+ 领域原沉淀（领域资产）。

---

## 7. 决策摘要（回答常见疑虑）

| 问题 | 协议答案 |
|------|----------|
| 不改原读逻辑怎么读公共库？ | **叠读**：原逻辑不动 + vault-base 召回门面 |
| 都读还是只读旧的？ | **都读（分层 merge）**，带来源标签 |
| 改读取源会不会乱？ | **P0/P1 禁止改默认源**；仅 P2 显式切换并保留回滚 |
| 写入改哪里？ | 领域资产→领域库；可复用→dump→pending→审核；禁止业务直写 main |

---

## 8. 待实现（协议已定、代码未齐）

- [x] 本机技能索引 `skill_catalog.py`（scan 索引 + lookup 合并历史元数据与当前源）
- [x] vault-base `recall` CLI（`vault_recall.py`：prefixCache → MySQL → 本地；带 `source`）
- [x] Redis prefixCache（`vault:c:` / `vault_prefix_cache.py`；与队列键分离）
- [x] Laya 共享门控（`laya_client.py` → `vault_consume.judge`；本地 systemone；可微调）
- [ ] 领域 Skill 薄封装示例（并行调 domain query + recall 后 merge）
- [ ] `legacy_vaults` → pending 导入器（mechanism 优先，ticket 需补正文）
- [ ] brief / frontmatter 校验 `vault_collab` 字段
