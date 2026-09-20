#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""brief_interview.py — 对接 grill 系列 skill，访谈式生成/迭代任务目标清单。"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="访谈式生成任务清单")
    ap.add_argument("--skill-id", help="业务 skill id")
    ap.add_argument("--iterate", action="store_true", help="迭代已有清单")
    ap.add_argument("--to-questionnaire", action="store_true",
                    help="导出问卷给他人回答")
    args = ap.parse_args()

    skill = args.skill_id or "<skill-id>"
    print("=== brief_interview ===")
    print()
    print("此脚本不直接生成清单，请改用访谈类 skill：")
    print()
    print("  1. grill-me（Codex skill）— 无文档时逐条追问目标、边界、验收标准")
    print("  2. grill-with-docs — 已有需求/规范文档时，对照文档做缺口访谈")
    print()
    print("访谈完成后，将结论写入：")
    print("  references/vault/briefs/%s.md" % skill)
    print()
    print("或先用 brief_bootstrap.py 生成草稿，再人工/访谈补全：")
    print("  brief_bootstrap.py --skill-dir <dir> --skill-id %s --write --promote" % skill)
    if args.iterate:
        print()
        print("[--iterate] 打开已有 briefs/%s.md，按「迭代记录」区块追加 rev。" % skill)
    if args.to_questionnaire:
        print()
        print("[--to-questionnaire] 将「必要信息要素」表格导出为问卷（手工或后续脚本）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
