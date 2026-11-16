# 准入质量基准（Quality Gate）

条目质量决定三件事：召回准不准、审核快不快、Laya 能不能训。
本文件是**唯一口径**——脚本按这里实现，人按这里判定，禁止各自另立标准。

一句话基准：**一条条目 = 一个可以被反驳的判断 + 一个可追溯的依据。**

## 0. 三道闸概览

| 闸 | 判什么 | 谁判 | 失败后果 |
|----|--------|------|----------|
| 字段闸 | 结构完整、锚点齐 | 机器（`vault_dump --strict`） | 拒写 |
| 内容闸 | 是不是一条知识 | 人（30 秒） | 退回补写 |
| 语义闸 | 能不能出本机 | Laya + 人审终审 | 降级 private |

顺序不能换。**字段不全的条目连被审的资格都没有**——让人花 30 秒读一条缺依据的东西，是最大的浪费。

## 1. 字段闸（机器，硬性）

`vault_dump --strict` 不通过即拒写，返回 `error=quality_gate_failed`。

| 检查 | 判据 | 级别 |
|------|------|------|
| `claim_key` | 非空、≤64 字符、小写 `[a-z0-9._/-]`、带 skill 前缀、库内不撞车 | error |
| `source_ref` | 非空 | error |
| `title` | ≥8 字符 | error |
| `question` | 非空 | error |
| `answer` | ≥80 字符 | error |
| `basis` | 非空 | error |
| `doc_type` | ∈ `answer / decision / reference` | error |
| 正文四区块 | 问题 / 答案&决策 / 决策依据 / 备注 | error（dump 自动生成） |
| 时间漂移词 | answer/basis 含「今天/目前/最新/暂时/先这样/这次」 | warning |
| 标题为问句 | 以 ? 结尾 | warning |
| 答案过长 | >2000 字符 | warning |

非 strict 模式只出 warning 照常写入。但**warning 是债，不是免罪**——它会在下一次审核时变成退回理由。

```bash
python scripts/vault_dump.py --strict \
  --skill-id req-code-consistency --doc-type decision --author quanguanming \
  --title "赛季遗物双索引转移时必须先 clear 再全量正向重建" \
  --question "遗物转移后反向索引出现残留条目，为什么？" \
  --answer "..." --basis "..." \
  --source-ref "SeasonRelicService.java:184" \
  --claim-key "req-code-consistency/relic-dual-index-rebuild-after-transfer"
```

## 2. claim_key：去重锚点

**没有 claim_key 就无法去重，无法去重就无法训练。** 这是当前最大的缺口（基线 0/21）。

### 格式

```
{skill_id}/{kebab-slug}
```

- 全小写，仅 `a-z0-9 . _ / -`，首字符为字母或数字，≤64 字符
- slug 用**英文**：中文 slug 在跨语言召回和 URL 化时会碎
- key 描述**判断**，不是描述文档

### 判定原则：同一判断收敛到同一 key

关键不是「标题像不像」，而是「这两条会不会给出不同的结论」。

| 情形 | 是否同 key | 说明 |
|------|-----------|------|
| 同一结论，措辞不同 | 同 key | 典型重复，必须收敛 |
| 同一结论，后来补了新依据 | 同 key | 走 supersede 更新原条，不新增 |
| 同一领域，结论相反 | 不同 key | 这是争议，靠 supersede + 状态机收敛 |
| 同一结论，适用对象不同 | 不同 key | 例如「赛季塔」与「圣光殿堂」各有取快照规则 |

### 典型正反例

| | key |
|---|---|
| 好 | `git-workflow/commit-policy-project-desensitize` |
| 好 | `req-code-consistency/relic-dual-index-rebuild-after-transfer` |
| 差 | `决策记录-2026-10-30`（描述时间，不描述判断） |
| 差 | `git-workflow/脱敏`（中文 + 太短，无法区分具体判断） |
| 差 | `vault-base/misc`（垃圾桶 key，等于没写） |

### 去重策略（预防 → 发现 → 收敛）

| 层 | 机制 | 行为 |
|----|------|------|
| 预防 | `vault_dump` | `claim_key` 撞车 → `error=dedupe_blocked`（**非 strict 也拒写**）；`content_hash` 相同 → 幂等返回已有 uuid，不落第二份 |
| 发现 | `vault_dedupe.py --report` | 多信号：同 claim_key / 同 skill+标题 / 同 content_hash / 草案 `merge:` |
| 收敛 | `vault_dedupe.py --apply` | `merge:<key>` → 移入 `_archive/`，打 `status=superseded` + `superseded_by`；可选 `--archive-junk` |
| 召回 | `vault_recall` | 跳过 `_archive` 与 `status=superseded` |

**没有 claim_key 就无法把「同义不同述」收敛成一条**；标题像不像只是辅助信号，最终以「会不会给出不同结论」判定是否同 key。

### 回填与维护

```bash
# 1) 报告（机械候选 + 垃圾/重复识别；保留上一版人工 merge:）
python scripts/vault_dedupe.py --report

# 2) 人工编辑 references/vault/_kb/claim_key_draft.json
#    claim_key 置空 = 跳过；改成 merge:<key> = 并入他条并归档自身

# 3) 应用：回填 key + 执行 merge 归档（可选清垃圾）
python scripts/vault_dedupe.py --apply --archive-junk
```

草案里的机械候选（`skill_id/标题归一化`）**只能对照，不能直接用**——它区分不了「同义不同述」，收敛判定必须人工完成。

**草案一旦被人编辑过就是人工产物**，重跑 `--report` 会沿用上一版判定（输出里 `carried_over` 计数）；
真要重新机械生成必须显式 `--force-regen`，否则 merge 标记与英文 key 会被覆盖，下一轮 `apply`
就会给重复条目补上机械 key，把缺陷固化。`--apply` 幂等，重复跑不会重复写。

## 3. 内容闸（人，30 秒可判）

六条，任一不过即退回。

1. **标题是陈述句**，不是名词短语
   `评估已实现测试工具时须并列三种演进方案` ✓ ／ `测试工具评估` ✗
2. **问题段是单个疑问句**，别人能复述出来
3. **答案段第一句就是动作**（做什么 / 不做什么），不出现「视情况而定」
4. **依据段有可追溯出处**：文件路径、对话出处、报错原文。不写「经验表明」
5. **无时间漂移词**：今天、目前、最新、暂时、先这样。有就必须写死日期
6. **自包含**：脱离上下文能读懂，不出现「上面那个」「刚才说的」

## 4. 硬拒：这五类不是（可共享）知识

| 类型 | 例子 | 为什么拒 |
|------|------|----------|
| 只有 what 没有 why | 「已修复，改了 X 文件」 | 下次同样的坑还会踩 |
| 一次性过程日志 | 「跑了哪条命令、第几步报错」 | 过程属于 git log，不属于金库 |
| 可从代码/文档直接读到 | 接口签名、配置项含义 | 那是 `reference`，且会随代码腐烂 |
| 组私有信息 | 内网 IP、密钥、人员名单 | `vault_redact` 已硬拦，不要试图绕过 |
| **本机环境绑定** | 个人盘符路径（`E:\AM-RD_Server\...`）、本机工作区清单、仅某使用者可复现的环境事实 | 换人/换机即失效；只写本地 `share_scope=private` + `owner=<使用者>`；共享侧改写为可移植约定（逻辑仓名 / SVN URL / 相对路径）。`vault_redact`（`WIN_ABS_PATH` 等）+ `approve` 复检双硬拦 |

口令：**换人/换机后还成立的判断才可共享。**

负样本（2026-11-16）：误将 `quanguanming` 本机 trunk 路径以 `--candidate-shared` 写入公共库后撤回；此后同类条目不得再进 `shared_vault_main`。

**另加一类：冒烟/演示/占位条目。** `smoke test`、`from-ide-a`、`do-not-share` 这类条目
不是知识，是测试残留。它们会污染召回、拉低训练集信噪比。
识别规则见 `vault_backfill_claim_key.py` 的 `junk_reason()`，发现即归档或删除。

## 5. 篇幅基准

| 指标 | 区间 | 依据 |
|------|------|------|
| 正文总字数 | 300–1500 字符 | 现有条目中位数 750 |
| answer 段 | 80–2000 字符 | <80 只够写 what；>2000 多半混了日志 |
| 标题 | 8–40 字符 | 短了不成为判断，长了装不进列表 |

超过 2000 字符不是拆句，是**拆条**：一条讲判断，其余转 `reference`。

## 6. 语义闸与 Laya 的边界

| | 职责 | 输出 |
|---|---|---|
| 规则层 | `is_candidate_shared=false` / redact 命中（含本机盘符路径） | 直接 private，不问模型 |
| Laya | 规则放行后的语义判定 | `share` / `private` / `uncertain` + 敏感 `noul` |
| 人审 | 最终准入；`approve` 写入主库前再跑 share_scope 复检 | `approve` / `reject --note`；本机环境命中则拒批并记 `approve_blocked` |

三条铁律：

1. **Laya 不决定写不写**，只决定「能不能出本机」。字段闸和内容闸在前面，Laya 看不到垃圾条目。
2. **`uncertain` 一律进 pending**，宁可多审不可漏审。
3. **`reject --note` 是负样本金矿**：note 应转成一条 decision 入库（说明「为什么这条不算知识」），这是训练集里最贵的一类数据。

## 7. 自检与统计口径

```bash
# 格式合规（文件名、frontmatter、正文区块）
VAULT_AUDIT_ROOT=~/.cursor/skills python scripts/_audit_vault_format.py

# claim_key 覆盖率与重复组
python scripts/vault_dedupe.py --report

# 训练集就绪度（样本量 + 标签均衡）
python scripts/laya_export_dataset.py --local-only
```

判定「可训练」的四个门（全部满足）：

| 门 | 判据 |
|----|------|
| G1 总量 | ≥200 条人审一致标签（approved + rejected 都算） |
| G2 均衡 | share : private ∈ [1:4, 4:1]，且 share ≥80 |
| G3 去重 | claim_key 覆盖 ≥90%，同 key 冲突已 supersede |
| G4 留出 | ≥15% held-out，不参与训练也不用于调温度 |

**G2 是最容易被忽略的否决项。** 只有 private 的语料训出来的模型会学成「全判 private」，
在测试集上准确率很高，但毫无作用。

### 基线快照（2026-11-12 去重落地后）

| 指标 | 值 |
|------|-----|
| live answer + decision | 以 `vault_dedupe.py --report` 的 `live` 为准 |
| 疑似垃圾 | 应用 `--archive-junk` 后应进 `_archive/` |
| 重复组 | 标题重复 2 组已标 `merge:` 并应归档：`dcf5a9ff`→`git-workflow/commit-policy-project-desensitize`；`e8e34705`→`game-qa-testcase/season2-pass-paywall-case` |
| 写入闸 | `claim_key` 撞车硬拦；`content_hash` 幂等 |

历史基线（2026-11-08）：21 条 / 垃圾 6 / 重复 2 组 / key 回填 13/15。

## 8. 修订记录

| 日期 | 变更 |
|------|------|
| 2026-11-16 | 本机环境门禁：硬拒「本机环境绑定」；redact 增 WIN_ABS_PATH 等；dump 强制 private；approve 复检不可绕过 |
| 2026-11-12 | 去重落地：dump 硬拦撞车 + hash 幂等；merge 归档到 `_archive`；`vault_dedupe.py` 入口 |
| 2026-11-08 | 首版。确立三道闸、claim_key 规范、硬拒四类、训练四门；配套 `--strict` 与回填脚本 |
