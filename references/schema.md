# Vault MD 数据契约（摘要）

必填 frontmatter：`schema_version`, `uuid`, `skill_id`, `doc_type`, `title`, `author`, `author_ip`, `created_at`, `updated_at`, `is_candidate_shared`, `share_rev`, `tags`, `content_hash`, `claim_key`, `source_ref`。

推荐：`source_task_id`，`share_scope`，`owner`。

| 字段 | 取值 | 说明 |
|------|------|------|
| `share_scope` | `private` \| `team` | 缺省 `private`。仅 `team` 才可做共享候选 |
| `owner` | 使用者 id（如 `quanguanming`） | 本机私有 / 盘符路径类条目建议必填；标明归属，禁止当团队默认路径 |
| `is_candidate_shared` | bool | 默认 false；redact / 本机环境命中强制 false |

`claim_key` 是去重锚点（格式 `{skill_id}/{kebab-slug}`，≤64 字符）；`source_ref` 是可追溯依据（文件:行 / 对话出处 / 报错原文）。两者在 `vault_dump --strict` 下必填，判定细则见 [quality-gate.md](quality-gate.md)。

`doc_type`：`answer` | `decision` | `reference` | `brief`（brief 用语义化文件名 `briefs/{skill_id}.md`，不走 uuid 命名）。

正文四区块（answer/decision）：问题 / 答案&决策 / 决策依据 / 备注。

规则：
- `is_candidate_shared` 默认 false；redact 命中（密钥 **或** 本机盘符/家目录/local_personal）强制 false
- `--candidate-shared` 且 `share_scope!=team` → 降级 private；strict 下与本机路径互斥则 `quality_gate_failed`
- 本机绝对路径只允许 `share_scope=private` + 本地 vault；**禁止**进 `shared_vault_main`（`approve` 复检硬拦）
- 追加备注刷新 `updated_at` 与 `content_hash`，不改 `share_rev`
- 标记共享变更才递增 `share_rev`
- 元数据回填（补 `claim_key`）不改 `updated_at`：`updated_at` 的语义是正文变更时间
