<!-- vault-base:begin (managed by skill_vault_adapt; do not edit) -->
## 知识金库接入（vault-base 适配）

本 skill 已由 `vault-base` 的 `skill_vault_adapt` 注入标准共享链路。  
**本节为受管内容**：卸载/升级只操作本 marker 区块，不要手工编辑。

**Vault 根**：`{{VAULT_BASE}}`（可用环境变量 `COMMON_SKILLS_REPO` / frontmatter `vault_base` 覆盖）。  
所有 hooks、preflight 清单、沉淀产物均存放在 vault 侧，本 skill 目录不保存任何注入文件。

### Chat 窗口强制提示（不可省略）

钩子 / `vault_dump` 成功后，stdout 会输出一行以 `【金库提示】` 开头的 `chat_notice`。  
**Agent 必须在当前 Chat 窗口原样复述该行给用户**（写入完成 / 技能触发），不得只写进内部日志。

示例：
- `【金库提示】技能触发·前置召回 | skill={{SKILL_ID}} | title=...`
- `【金库提示】金库写入 | skill={{SKILL_ID}} | title=... | uuid=... | shared=no`

事件同时写入 MySQL `vault_skill_event_log` 与本地 `_kb/skill_event_log.jsonl`。

### 主动沉淀（硬规则 · 勿等用户催促）

以下任一出现时，**Agent 必须主动**跑步骤 4 沉淀，**禁止**等用户说「帮我沉淀 / 写进金库」才写：

- 可复用决策、业务口径、字段取值路径（如活动快照世界等级/开服天数来源）
- 检索最短路径 / 优秀索引策略 / 反模式（下次能少绕路）
- 工具调用链路、结案结论、HTML 报告要点

沉淀时机：结论一成型就写（过程中可多次），结案再补全/升 shared。  
默认 `--kind` 见 taxonomy（决策→`reusable_decision`/`scenario_decision`；索引策略/链路→`exec_logic`/`search_index_strategy`/`business_flow`）。  
可共享则 `--share-scope shared`（必要时 `--enqueue`）；敏感强制 private。

### 强制五步（`<vb>` = {{VAULT_BASE}}，本 skill id = `{{SKILL_ID}}`）

1. 前置召回：`python "<vb>/assets/skill-adapt/hooks/pre_task_recall.py" --skill-dir <本skill目录> --skill-id {{SKILL_ID}}`  
   → **把输出的 `【金库提示】` 行贴进 Chat**
2. 前置物料清单（vault 侧）：`<vb>/{{SKILL_ID}}/preflight.yml`
3. `python "<vb>/scripts/preflight_materials.py" missing --skill-dir <本skill目录> --skill-id {{SKILL_ID}} --json`  
   → **一次只问一项**补齐（对齐 grill / Matt Pocock 式 Skills 访谈），再 `write-field`
4. 执行中过程沉淀（**主动**，见上节）：`python "<vb>/assets/skill-adapt/hooks/post_task_sediment.py" --skill-dir <本skill目录> --skill-id {{SKILL_ID}} ...`  
   → **把 `【金库提示】` 行贴进 Chat**  
   （协议见 `<vb>/assets/skill-adapt/rules/vault-share-protocol.md`；分类见 `<vb>/assets/skill-adapt/knowledge-taxonomy.yml`）
5. 结案：可共享结论 `--share-scope shared --enqueue`；私有保持 private。WebPlatform VaultBase 做人审/Laya。  
   → 写入后 **再次**在 Chat 展示 `【金库提示】金库写入 …`

> 卸载本适配：`python "<vb>/scripts/skill_vault_adapt.py" uninstall --skill-id {{SKILL_ID}}`  
> （沉淀知识保留在 vault 侧 `{{SKILL_ID}}/sediment/`，不会随卸载删除。）
<!-- vault-base:end -->
