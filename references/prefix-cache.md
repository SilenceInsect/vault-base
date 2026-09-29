# Redis prefixCache 契约（`vault:c:`）

适用范围：vault-base 共享知识**只读加速层**。  
真相源：MySQL `amrd_qa_vault.shared_vault_main`（`status=active`）。  
本地笔记、pending、私有 MD **不进**本缓存。

## 1. Redis 职责拆分（对照）

| 键前缀 | 职责 | 可丢？ |
|--------|------|--------|
| `vault:c:` | **prefixCache**（本契约） | 是，可全量重建 |
| `vault:events:` / `processing:` / `dlq:` / `llm_pending:` | 事件队列 | 否（业务在途） |
| `vault:queue:users` | 队列用户集合 | 可重建 |
| `snapshot:git_commit:` | 扫描基线游标 | 可重建（丢则全量对齐） |
| `kb:` | 领域旧库（bug-vault 等） | **禁止** vault-base 写入 |

配置：`vault.config.json` → `cache_ttl_seconds`（默认 172800）。  
实现：`scripts/vault_prefix_cache.py`；召回门面：`scripts/vault_recall.py`。

## 2. 键表

统一前缀：`vault:c:`（c = cache）。

| 键 | 类型 | 值 | TTL |
|----|------|-----|-----|
| `vault:c:doc:{uuid}` | STRING | JSON 文档摘要 | `cache_ttl_seconds` |
| `vault:c:idx:skill:{skill_id}` | SET | uuid 集合 | 无（随成员失效） |
| `vault:c:idx:claim:{claim_key}` | SET | uuid 集合 | 无 |
| `vault:c:q:{skill_id}:{query_hash}` | STRING | JSON 命中列表 | 短 TTL（默认 3600，且 ≤ cache_ttl） |
| `vault:c:qidx:{skill_id}` | SET | 该 skill 下 query 缓存键名 | 无 |
| `vault:c:meta:{skill_id}` | STRING | catalog 元数据键表 JSON | `cache_ttl_seconds` |

### 文档摘要 JSON（`doc`）

```json
{
  "uuid": "...",
  "skill_id": "...",
  "doc_type": "answer",
  "title": "...",
  "body": "...",
  "claim_key": "",
  "content_hash": "16hex",
  "tags": [],
  "author": "...",
  "source": "shared"
}
```

`body` 可截断（实现默认 4000 字符）；完整正文以 MySQL 为准。

## 3. 读写时序

**召回**（`vault_recall.py`）：

1. 可选：查 `vault:c:q:{skill}:{hash}`
2. 查 `idx:skill` → `MGET`/`GET` 各 `doc`
3. 不足则 MySQL `status=active` 查询 → 回填 `doc` + `idx` + 可选 `q`
4. 再叠读本地 Vault MD，打 `source` 标签：`shared_cache` | `shared_mysql` | `local_vault`

**审核通过**（`approve`）：

1. 写 MySQL `shared_vault_main`
2. `put_doc` + `SADD` idx
3. `invalidate_skill_queries(skill_id)`（清 `q` / `qidx` / `meta`）

**supersede / 归档**：删或覆盖 `doc`，从相关 SET 移除 uuid，并 `invalidate_skill_queries`。

## 4. 禁止事项

- 业务 Skill 直写 `vault:c:` 或 `kb:`
- 把 pending / 私有答案写入 cache
- 生产路径使用 `KEYS *`（枚举走业务侧 SET / 已知前缀）
- 把 Redis 当唯一真相源（断连必须能回源 MySQL）

## 5. CLI：直接提问查 Redis

确认**索引命中**与**知识召回**（仅 Redis，不回源 MySQL）：

```bash
# 传问题
python scripts/vault_cache_ask.py "install-gate-smoke"
python scripts/vault_cache_ask.py -q "共享知识怎么沉淀" --skill-id vault-base

# 看 Redis 里已有哪些 skill 索引
python scripts/vault_cache_ask.py --list-index

# 完整 JSON 诊断（query_cache / skill_index / doc_hits / misses）
python scripts/vault_cache_ask.py "..." --skill-id vault-base --json

# 未命中时先 recall 预热 MySQL→cache，再查一次 Redis
python scripts/vault_cache_ask.py "..." --skill-id vault-base --warmup

# 等价子命令
python scripts/vault_prefix_cache.py ask "..." --skill-id vault-base
```

叠读（cache→MySQL→本地）用 `vault_recall.py`，不要与本 CLI 混淆。

## 6. 门禁

`install_gate` I9：对 `vault:c:` 探针键做 SET EX / GET / DEL，确认 TTL 可用。
