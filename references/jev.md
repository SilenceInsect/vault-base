# 已迁移：JEV → Laya

共享门控已改为本地 **Laya**（System One / `POST /v1/systemone`）。

请阅读：**[laya.md](laya.md)**（本地部署、配置、微调导出）。

兼容：

- 配置键 `jev.*` 仍可由 `laya_client` / `vault_consume` 回落读取
- `scripts/jev_client.py` 为 shim，转调 `laya_client`
