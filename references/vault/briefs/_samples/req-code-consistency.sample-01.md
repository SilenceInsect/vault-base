---
schema_version: 1
doc_type: brief
uuid: "642d95df-34a6-4734-99ae-23636e262244"
skill_id: "req-code-consistency"
title: "req-code-consistency 任务清单"
brief_rev: 1
source: "bootstrap"
author: "bootstrap"
created_at: "2026-09-20T22:24:09+08:00"
updated_at: "2026-09-20T22:24:09+08:00"
is_candidate_shared: false
tags: []
content_hash: ""
---
# req-code-consistency 任务清单

## 目标与产出
- 目标：
- 交付物：
- 验收标准：

## 必要信息要素
| 要素 | 必要性 | 获取方式 | 缺失影响 |
|---|---|---|---|
| 任务目标 | 必须 | 问用户 / 读 SKILL.md description | 阻塞性缺失 |
| skill 描述上下文 | 可选 | 自动（SKILL.md） | 帮助对齐范围 |
| vault | 必须 | 问用户 | 影响输出质量 |
| kind | 必须 | 问用户 | 影响输出质量 |
| title | 必须 | 问用户 | 影响输出质量 |
| decision | 可选 | 问用户 | 影响输出质量 |
| basis | 可选 | 问用户 | 影响输出质量 |
| usage | 可选 | 问用户 | 影响输出质量 |
| lesson | 可选 | 问用户 | 影响输出质量 |
| cost | 可选 | 问用户 | 影响输出质量 |
| todo | 可选 | 问用户 | 影响输出质量 |
| trigger | 可选 | 问用户 | 影响输出质量 |
| tags | 可选 | 问用户 | 影响输出质量 |
| version | 可选 | 默认值： | 影响输出质量 |
| business | 可选 | 默认值： | 影响输出质量 |
| user | 可选 | 问用户 | 影响输出质量 |
| date | 可选 | 默认值： | 影响输出质量 |
| task | 可选 | 默认值： | 影响输出质量 |
| supersedes | 可选 | 默认值： | 影响输出质量 |
| docx | 可选 | 问用户 | 影响输出质量 |
| keywords | 可选 | 默认值： | 影响输出质量 |
| context | 可选 | 默认值：2 | 影响输出质量 |
| out | 可选 | 问用户 | 影响输出质量 |
| data | 必须 | 问用户 | 影响输出质量 |
| template | 可选 | 问用户 | 影响输出质量 |
| form | 可选 | 问用户 | 影响输出质量 |
| text | 可选 | 问用户 | 影响输出质量 |
| top | 可选 | 默认值：6 | 影响输出质量 |
| all | 可选 | 问用户 | 影响输出质量 |
| json | 可选 | 问用户 | 影响输出质量 |
| stdout | 可选 | 问用户 | 影响输出质量 |
| references 规范文档 | 可选 | 自动读取 references/*.md | 补充领域约束 |
| 验收标准 | 必须 | 问用户 | 阻塞性缺失 |

## 工作环境
| 项 | 值 / 来源 | 获取方式 |
|---|---|---|
| 本地 Vault | skills/vault-base/references/vault | 自动（R1） |
| 共享环境 | 208 Redis/MySQL | 自动探测（R2/R4） |

## 决策记录（已解决）
| # | 问题 | 结论 | 日期 |
|---|---|---|---|
|  |  |  |  |

## 待澄清 / 待协调
- [ ] （待填）

## 迭代记录
| rev | 日期 | 变更 |
|---|---|---|
| 1 | 2026-09-20 | 初始版本（brief_bootstrap） |
