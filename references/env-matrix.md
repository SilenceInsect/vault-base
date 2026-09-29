# 环境检查矩阵（E 摘要）

| ID | 检查 | 说明 |
|---|---|---|
| E1 | Python 可运行 | 解释器路径；清单 `install.python`：新装默认 3.11.5+，接受 ≥3.10 |
| E2 | pymysql | 缺则共享 MySQL 不可用（易静默 SQLite）；清单 `install.pip.pymysql` |
| E3 | git | 增量扫描依赖；清单 `install.git` |
| E4 | Vault 目录 | references/vault |
| E5 | secrets.local.json | 可选；无则共享降级 |
| E6 | Redis TCP/PING | 208:6379；无 Stream；本机不装服务端 |
| E7 | MySQL SELECT 1 | 身份回读；本机不装 mysqld |
| E8 | redis_version | 须知 3.2.x（清单 `expected_version`） |
| E9 | mysql charset | 建表须 utf8mb4 |
| E10 | aof_enabled | 0 → 事件宕机易丢（X-1） |
| E11 | requirepass | 空 → 安全风险（X-2） |

本机组件默认版本/路径见 [install-inventory.md](install-inventory.md) 覆盖矩阵。  
探测：`env_probe.py --json` / `--quick`
