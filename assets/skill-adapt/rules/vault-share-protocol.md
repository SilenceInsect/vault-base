# 业务 Skill · 知识金库共享协议（由 vault-base adapt 注入）

适配后，Agent **必须**按下列步骤执行；禁止跳过前置清单直接开干，禁止业务直写 `shared_vault_main`。

## 五步标准链路

1. **前置召回**  
   运行 `hooks/pre_task_recall.py`，用 Redis `vault:c:`（及可选 MySQL）做相关性召回，构建本任务上下文。

2. **前置物料清单（YAML）**  
   按任务目标生成/打开  
   `references/vault-integration/preflight/<skill_id>.preflight.yml`。  
   用 `preflight_materials.py missing` 列出缺项；**一次只问一项**（Matt/grill 式 Skills 访谈），`write-field` 补齐。

3. **自动填充 + 访谈完备**  
   已知字段（author、workspace、brief 路径、召回结果）自动填；不确定项访谈确认后再改清单结构。

4. **过程决策自动沉淀**  
   执行中凡形成可复用结论/链路/工具选择/质量改进，调用  
   `hooks/post_task_sediment.py`（读本 rules + taxonomy）。  
   分类：`pending_human` / `human_reviewed` / `private` / `public_shared` /  
   `reusable_decision` / `scenario_decision` / `business_flow` 等（见 `knowledge-taxonomy.yml`）。

5. **持久化与 Web 归档**  
   本地 YAML/MD →（shared）scan enqueue → MySQL pending → 人审 → Redis `vault:c:`；  
   WebPlatform VaultBase 负责归档、清理、Laya 训练。

## 硬约束

- 敏感/凭证：强制 private，不可旁路。  
- 共享候选：只进 pending，人审前不得当「已审公共知识」。  
- HTML 报告路径、置信度、最小/完整执行链路写入沉淀 payload，供跨会话复用。
