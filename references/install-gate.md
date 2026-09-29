# 安装门禁 I1–I9

脚本：`scripts/install_gate.py`

在共享 DDL / secrets 就绪后、宣称「共享可写」之前执行：

```bash
python scripts/install_gate.py --id "<team-type>:<team-name>" --require-shared --json
```

| 项 | 含义 | 缺失等级（`--require-shared`） |
|----|------|-------------------------------|
| I1 | IDE LINK → common-skills-repo | BLOCKER |
| I2 | 物料清单 YAML 可解析；共享 host 可由 secrets 覆盖 | BLOCKER / DEGRADED |
| I3 | MySQL 表结构与 `ddl_mysql57.sql` / MysqlVaultStore 一致 | BLOCKER |
| I4 | `vault_consume` 可导入 | BLOCKER |
| I5 | `list_pending` 可执行 | BLOCKER |
| I6 | `store.search` 可执行 | BLOCKER |
| I7 | Redis PING + LIST | BLOCKER |
| I8 | 空队列 BRPOP 不抛 TimeoutError | BLOCKER |
| I9 | prefixCache `vault:c:` SET EX/GET/DEL | BLOCKER |

与 `readiness_gate`（R1–R8）互补：后者看连通与本地骨架；本门禁看安装闭环与历史阻断点。

## 可选：Laya 共享门控（本地）

非门禁项。默认 `laya.enabled=false`；未启服务时消费降级（规则层 / uncertain）。

```bash
pip install "laya[serve]"
set LAYA_HOST=127.0.0.1& set LAYA_PORT=8000& set LAYA_DEVICE=cpu& set LAYA_PRELOAD=1
laya-serve
python scripts/laya_client.py probe
python scripts/laya_client.py share-gate --mock --candidate-shared \
  --title "团队规范" --question "如何沉淀？" --answer "走 vault_dump 再审核"
python scripts/laya_export_dataset.py --seed-only
```

开启：`vault.config.json` → `laya.enabled=true`，`base_url=http://127.0.0.1:8000`。  
契约：[laya.md](laya.md)。**永不**因 Laya 结果直接 `approve`。

详见 `docs/install.html`「阶段 5.5」「安装注意事项」。
