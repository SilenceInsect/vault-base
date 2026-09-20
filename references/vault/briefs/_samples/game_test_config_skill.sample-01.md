---
schema_version: 1
doc_type: brief
uuid: "a1b2c3d4-e5f6-4789-a012-3456789abcde"
skill_id: "game_test_config_skill"
title: "游戏测试配置 Skill 任务清单"
brief_rev: 1
source: "bootstrap"
author: "sample"
created_at: "2026-09-20T22:00:00+08:00"
updated_at: "2026-09-20T22:00:00+08:00"
is_candidate_shared: false
tags: ["game_test", "sample"]
content_hash: ""
---
# 游戏测试配置 Skill 任务清单

## 目标与产出
- 目标：根据策划需求文档生成符合团队模板的测试用例 Excel
- 交付物：功能文件夹内的 xlsx、xmind、中间表与质量检查报告
- 验收标准：硬闸清零、规范对齐、作者姓名真实有效

## 必要信息要素
| 要素 | 必要性 | 获取方式 | 缺失影响 |
|---|---|---|---|
| 作者真实姓名 | 必须 | 问用户 / ai_case/_作者.json | 阻塞性缺失 |
| 功能文件夹路径 | 必须 | 问用户 / 列出 ai_case/测试用例/ | 阻塞性缺失 |
| 策划需求文档 | 必须 | 功能文件夹内 Word/Excel | 阻塞性缺失 |
| 用例模板路径 | 可选 | 默认值：ai_case/用例模板以及审核标准/ | 影响输出格式 |
| 协议 xml 目录 | 必须 | 问用户（步骤 8 后） | 阻塞性缺失 |
| 协议 ID | 必须 | 问用户 | 阻塞性缺失 |

## 工作环境
| 项 | 值 / 来源 | 获取方式 |
|---|---|---|
| 本地 Vault | skills/vault-base/references/vault | 自动（R1） |
| 共享环境 | 208 Redis/MySQL | 自动探测（R2/R4） |
| Python | .python/python.exe | 项目内约定 |

## 决策记录（已解决）
| # | 问题 | 结论 | 日期 |
|---|---|---|---|
| 1 | 用例作者名来源 | 必须写入 ai_case/_作者.json | 2026-09-20 |

## 待澄清 / 待协调
- [ ] 功能文件夹是否已创建
- [ ] 策划需求是否定稿

## 迭代记录
| rev | 日期 | 变更 |
|---|---|---|
| 1 | 2026-09-20 | 初始样例（从 brief-template 迁移） |
