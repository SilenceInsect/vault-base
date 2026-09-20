# 环境检查矩阵（E 摘要）

| ID | 检查 | 说明 |
|---|---|---|
| E1 | Python 可运行 | 解释器路径 |
| E2 | pymysql | 缺则共享 MySQL 不可用（易静默 SQLite） |
| E3 | git | 增量扫描依赖 |
| E4 | Vault 目录 | references/vault |
| E5 | secrets.local.json | 可选；无则共享降级 |
| E6 | Redis TCP/PING | <SHARED_HOST>:6379；无 Stream |
| E7 | MySQL SELECT 1 | 身份回读 |
| E8 | redis_version | 须知 3.2.x |
| E9 | mysql charset | 建表须 utf8mb4 |
| E10 | aof_enabled | 0 → 事件宕机易丢（X-1） |
| E11 | requirepass | 空 → 安全风险（X-2） |

探测：`env_probe.py --json` / `--quick`
