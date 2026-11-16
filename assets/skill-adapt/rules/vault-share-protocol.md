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

4. **过程决策自动沉淀（主动 · 勿等用户催促）**  
   执行中凡形成可复用结论/链路/工具选择/质量改进/**检索最短路径或索引策略**，  
   **立即**调用 `hooks/post_task_sediment.py`（读本 rules + taxonomy）。  
   **禁止**把沉淀当成「用户额外请求」；用户未开口也必须写。  
   分类：`pending_human` / `human_reviewed` / `private` / `public_shared` /  
   `reusable_decision` / `scenario_decision` / `business_flow` /  
   `exec_logic` / `search_index_strategy` 等（见 `knowledge-taxonomy.yml`）。

5. **持久化与 Web 归档**  
   本地 YAML/MD →（shared）scan enqueue → MySQL pending → 人审 → Redis `vault:c:`；  
   WebPlatform VaultBase 负责归档、清理、Laya 训练。

## 硬约束

- **主动沉淀**：可复用结论一成型即 `post_task_sediment` / `vault_dump`；等用户说「帮我沉淀」视为协议违规。  
- 敏感/凭证：强制 private，不可旁路。  
- **本机环境绑定**：个人盘符路径、本机工作区清单、仅某使用者可复现的环境事实 → 默认 `share_scope=private` + `owner=<使用者>`；**禁止**对路径类条目使用 `--candidate-shared` / `share_scope=team`。  
  - 可共享写法：`Design 仓 trunk/原始表格`、SVN URL、逻辑仓名  
  - 不可共享写法：`D:\AM-RD_Design\trunk\原始表格`、`E:\AM-RD_Server\trunk\game`、`F:\RDClient`  
- **禁止**业务 Agent 直写 `shared_vault_main` 或绕过 `vault_consume` 手工 `upsert_pending`+`approve`（即使本地脚本也过不了 approve 复检）。  
- 共享候选：只进 pending，人审前不得当「已审公共知识」；人审 `approve` 会再跑本机环境/敏感复检，命中则拒批。  
- HTML 报告路径、置信度、最小/完整执行链路写入沉淀 payload，供跨会话复用。
