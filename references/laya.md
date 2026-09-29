# Laya 决策模型（本地部署 + 微调）

用开源 **Laya**（Apache-2.0）替代托管 JEV：本机 `laya-serve` 提供与 System One 同形的  
`choice` / `score` / `noul` 载荷。本仓只把它用作**共享候选门控**，不替代人审。

上游：[`laya` on PyPI](https://pypi.org/project/laya/) · [Self-host](https://laya-ai.com/guides/self-host-laya) · [Fine-tune](https://laya-ai.com/guides/fine-tune-laya)

## 1. 职责

| 角色 | 职责 |
|------|------|
| 规则层 | `is_candidate_shared` / redact 硬约束 |
| **Laya** | `private` / `share` / `needs_human` + 敏感 `noul`；写 `llm_*` |
| 人审 | `vault_review approve` 最终准入 |
| Agent | 仅低置信 `uncertain` 兜底 |

## 2. 本地部署

```bash
pip install "laya[serve]"
# CPU 示例；有 GPU 用 LAYA_DEVICE=cuda / mps
set LAYA_HOST=127.0.0.1
set LAYA_PORT=8000
set LAYA_DEVICE=cpu
set LAYA_PRELOAD=1
set LAYA_MODELS=typed-decisions
laya-serve
```

探测：

```bash
curl -s http://127.0.0.1:8000/v1/models
curl -s http://127.0.0.1:8000/v1/systemone -H "content-type: application/json" -d "{\"state\":{\"title\":\"ping\"},\"questions\":{\"ok\":{\"type\":\"noul\",\"instructions\":\"alive?\"}}}"
```

暴露到非本机时务必设 `LAYA_API_KEY`，并在 secrets 填同一 key。

## 3. vault-base 配置

`references/vault/_kb/vault.config.json`：

```json
"laya": {
  "enabled": true,
  "mock": false,
  "model": "typed-decisions",
  "base_url": "http://127.0.0.1:8000",
  "api_path": "/v1/systemone",
  "timeout_seconds": 30,
  "share_min_confidence": 0.75,
  "sensitive_noul_threshold": 0.70,
  "require_api_key": false
}
```

`secrets.local.json`（本地默认可空）：

```json
"laya": {
  "api_key": "",
  "base_url": "http://127.0.0.1:8000"
}
```

环境变量：`VAULT_LAYA_ENABLED=1` / `VAULT_LAYA_BASE_URL` / `VAULT_LAYA_API_KEY` / `VAULT_LAYA_MOCK=1`  
（旧 `VAULT_JEV_*` 仍可读。）

## 4. CLI

```bash
python scripts/laya_client.py probe
python scripts/laya_client.py share-gate --mock --candidate-shared \
  --title "团队规范" --question "如何沉淀？" --answer "走 vault_dump 再审核"
# 本机 serve 起来后去掉 --mock
python scripts/laya_client.py share-gate --candidate-shared \
  --title "团队规范" --question "如何沉淀？" --answer "走 vault_dump 再审核"
```

`scripts/jev_client.py` 为兼容 shim，内部转调 Laya。

## 5. 接入点

`vault_consume.judge`：规则通过后，若 `laya.enabled`（或 mock）则 `laya_client.share_gate`。

门控：

1. `is_sensitive.noul >= threshold` → **private**
2. `share_decision=private` 且高置信 → **private**
3. `share_decision=share` 且高置信 → **share**（pending，`needs_llm=0`）
4. 其余 → **uncertain**

## 6. 训练微调

Laya 零样本接近随机；**领域微调**后才适合生产门控。官方用 RLCD notebook（Kaggle 2×T4，约 4–5h / 4 epoch）。

### 6.1 从 vault 已有决策导出

```bash
# 默认：种子 + 本地 answers|decisions|references + MySQL main/pending 历史
python scripts/laya_export_dataset.py

# 只要仓库内 MD（不连库）
python scripts/laya_export_dataset.py --local-only

# 本地 candidate 直接当 share 正样本（慎用）
python scripts/laya_export_dataset.py --local-as-share
```

默认输出：`references/vault/_kb/laya_finetune/share_gate.jsonl`

| 数据源 | 标签 |
|--------|------|
| `shared_vault_main` active | `share` |
| pending `approved` / `rejected` | `share` / `private` |
| 本地 MD `is_candidate_shared=false` | `private` |
| 本地 MD candidate 未审 | `needs_human`（弱；可用 `--local-as-share`） |
| `dataset.sample.jsonl` | 种子（`--no-seed` 可关） |

同 uuid 去重优先级：`mysql_main` > `mysql_pending` > `local_md` > `seed`。  
**正式训练不要用 `--include-pending`。** 样本 &lt;50 时报告会提示仅够冒烟。

### 6.2 跑上游微调

1. 打开上游 notebook：`laya_finetune_typed_decisions_2xT4_kaggle.ipynb`（见 [Fine-tune 指南](https://laya-ai.com/guides/fine-tune-laya)）
2. 把导出的 JSONL 并入其 dataset 构建步骤（或按 notebook schema 转换）
3. 训练 → 校准温度（**必须用 held-out**，勿在训练集上 fit）
4. 导出 checkpoint 到本机目录，例如 `~/models/laya-vault-share-gate`

### 6.3 用微调权重 serve

```bash
# 将 LAYA_MODELS 指到本地目录或 Hub id
set LAYA_MODELS=C:/Users/you/models/laya-vault-share-gate
set LAYA_PRELOAD=1
laya-serve
```

`vault.config.json` → `laya.model` 与 serve 侧 checkpoint 名一致。

### 6.4 数据量建议

| 阶段 | 样本 | 说明 |
|------|------|------|
| 冒烟 | 种子 3 条 | 只验证导出/加载 |
| 可用 | ≥200 条人审一致标签 | share/private/敏感均衡 |
| 稳产 | ≥2k–30k 问句级 | 对齐上游 notebook 量级 |

## 7. 与 Redis / 召回

Laya **不**替代 `vault:c:` prefixCache，也 **不**参与 `vault_cache_ask`。  
只在消费入队后、是否进 pending 时判决。

## 8. 从 JEV 迁移

| 旧 | 新 |
|----|-----|
| `jev.*` 配置 | `laya.*`（读配置时仍回落 `jev`） |
| `https://openclaw-api.com` + `/v1/decisions` | `http://127.0.0.1:8000` + `/v1/systemone` |
| 必须 api_key | loopback 默认可空 |
| `jev_client.py` | `laya_client.py`（shim 保留） |
