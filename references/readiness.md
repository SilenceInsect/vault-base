# 就绪门禁 R1–R8

| # | 项 | 等级 | 缺失行为 |
|---|---|---|---|
| R1 | 本地 Vault 存在且有 md | DEGRADED | `env_ensure` 建骨架；空库提示先积累 |
| R2 | Redis PING + MySQL SELECT 1 | DEGRADED | 本地降级，事件暂存 |
| R3 | Vault 有 Git HEAD | BLOCKER | `--fix` 可 git init |
| R4 | DB 连接身份回读正确 | BLOCKER | **拒绝共享写入**（防静默 SQLite 却写 Redis） |
| R5 | Skill `accepts_brief: true` + 注入能力 | BLOCKER | 声明接入 vault-base |
| R6 | `briefs/{skill_id}.md` | DEGRADED | brief_bootstrap / grill |
| R7 | `briefs/_samples/` 非空 | DEGRADED | brief_bootstrap |
| R8 | brief 含 brief_rev / updated_at / 迭代记录 | INFO | 迭代时维护 |

结果契约：`ready` / `gate` / `capability`（含 `can_write_shared`）。  
`can_write_shared=false` 时共享侧写操作必须直接拒绝。
