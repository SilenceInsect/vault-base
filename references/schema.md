# Vault MD 数据契约（摘要）

必填 frontmatter：`schema_version`, `uuid`, `skill_id`, `doc_type`, `title`, `author`, `author_ip`, `created_at`, `updated_at`, `is_candidate_shared`, `share_rev`, `tags`, `content_hash`。

推荐：`source_task_id`, `source_ref`, `claim_key`。

`doc_type`：`answer` | `decision` | `reference` | `brief`（brief 用语义化文件名 `briefs/{skill_id}.md`，不走 uuid 命名）。

正文四区块（answer/decision）：问题 / 答案&决策 / 决策依据 / 备注。

规则：
- `is_candidate_shared` 默认 false；redact 命中强制 false
- 追加备注刷新 `updated_at` 与 `content_hash`，不改 `share_rev`
- 标记共享变更才递增 `share_rev`
