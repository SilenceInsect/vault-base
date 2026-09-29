## 知识金库接入（vault-base 适配）

本 skill 已由 `vault-base` 的 `skill_vault_adapt` 注入标准共享链路。

**Vault 根**：`~/common-skills-repo/vault-base`（可用 `COMMON_SKILLS_REPO` / frontmatter `vault_base` 覆盖）。

### 强制五步

1. 前置召回：`python hooks/pre_task_recall.py --skill-dir <本skill> --skill-id <id>`
2. 打开前置物料清单：`references/vault-integration/preflight/<id>.preflight.yml`
3. `python <vault_base>/scripts/preflight_materials.py missing --skill-dir ... --skill-id ... --json`  
   → **一次只问一项**补齐（对齐 grill / Matt Pocock 式 Skills 访谈），再 `write-field`
4. 执行中过程沉淀：`python hooks/post_task_sediment.py ...`（见 `references/vault-integration/rules/vault-share-protocol.md`）
5. 结案：可共享结论 `--share-scope shared --enqueue`；私有保持 private。WebPlatform VaultBase 做人审/Laya。

分类与字段：`references/vault-integration/knowledge-taxonomy.yml`。
