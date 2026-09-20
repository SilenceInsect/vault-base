# 任务目标清单（brief）契约

- 路径：`briefs/{skill_id}.md`（唯一允许的语义化名）
- 六区块：目标与产出 / 必要信息要素 / 工作环境 / 决策记录 / 待澄清 / 迭代记录
- frontmatter 必含：`doc_type: brief`, `brief_rev`, `updated_at`, `skill_id`
- 归属：由 vault-base 统一管理；业务 skill 只读注入、按规则回写「待澄清」

自举：`brief_bootstrap.py --skill-dir <path> --write`  
访谈：`grill-me`（从零）/ `grill-with-docs`（锚定迭代）
