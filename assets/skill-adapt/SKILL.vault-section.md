<!-- vault-base:begin (managed by skill_vault_adapt; do not edit) -->
## 知识金库接入（vault-base 适配）

本 skill 已由 `vault-base` 的 `skill_vault_adapt` 注入标准共享链路。  
**本节为受管内容**：卸载/升级只操作本 marker 区块，不要手工编辑。

**Vault 根**：`{{VAULT_BASE}}`（可用环境变量 `COMMON_SKILLS_REPO` / frontmatter `vault_base` 覆盖）。  
所有 hooks、preflight 清单、沉淀产物均存放在 vault 侧，本 skill 目录不保存任何注入文件。

### 强制五步（`<vb>` = {{VAULT_BASE}}，本 skill id = `{{SKILL_ID}}`）

1. 前置召回：`python "<vb>/assets/skill-adapt/hooks/pre_task_recall.py" --skill-dir <本skill目录> --skill-id {{SKILL_ID}}`
2. 前置物料清单（vault 侧）：`<vb>/{{SKILL_ID}}/preflight.yml`
3. `python "<vb>/scripts/preflight_materials.py" missing --skill-dir <本skill目录> --skill-id {{SKILL_ID}} --json`  
   → **一次只问一项**补齐（对齐 grill / Matt Pocock 式 Skills 访谈），再 `write-field`
4. 执行中过程沉淀：`python "<vb>/assets/skill-adapt/hooks/post_task_sediment.py" --skill-dir <本skill目录> --skill-id {{SKILL_ID}} ...`  
   （协议见 `<vb>/assets/skill-adapt/rules/vault-share-protocol.md`；分类见 `<vb>/assets/skill-adapt/knowledge-taxonomy.yml`）
5. 结案：可共享结论 `--share-scope shared --enqueue`；私有保持 private。WebPlatform VaultBase 做人审/Laya。

> 卸载本适配：`python "<vb>/scripts/skill_vault_adapt.py" uninstall --skill-id {{SKILL_ID}}`  
> （沉淀知识保留在 vault 侧 `{{SKILL_ID}}/sediment/`，不会随卸载删除。）
<!-- vault-base:end -->
