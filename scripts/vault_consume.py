#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_consume.py — 事件消费 Agent。

两种运行模式（见 7.3.2 四层调起路径）：
  session  P1/P3/P4：会话内执行，**模型可用**，可做语义判定
  timer    P2：定时任务，**无 Agent 无模型**，只做规则判定；
           规则判不了的事件 defer 到 vault:llm_pending:{user}，不丢弃

流程：BRPOPLPUSH 取出 → 幂等检查 → 解析 MD → 相似/矛盾检测 → 分流 → 确认 / 入 DLQ
队列按 user_id 分片（7.3.3）：Redis 3.2 无消费组，且 P1 天然只消费自己的事件。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault_queue as vq                                              # noqa: E402
from vault_paths import load_config, load_secrets, redis_conn, vault_root  # noqa: E402

FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def parse_frontmatter(text: str) -> dict:
    """极简 YAML frontmatter 解析（标量 + 数组），够用即可。"""
    m = FRONT_RE.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            out[k] = [x.strip().strip('"').strip("'")
                      for x in v[1:-1].split(",") if x.strip()]
        elif v in ("true", "false"):
            out[k] = v == "true"
        elif v.isdigit():
            out[k] = int(v)
        else:
            out[k] = v.strip('"').strip("'")
    return out


def split_sections(text: str) -> dict:
    body = FRONT_RE.sub("", text, count=1)
    parts = re.split(r"^##\s+", body, flags=re.M)
    seg = {}
    for p in parts[1:]:
        head, _, rest = p.partition("\n")
        seg[head.strip()] = rest.strip()
    return {
        "question": seg.get("问题", ""),
        "answer": seg.get("答案&决策", ""),
        "basis": seg.get("决策依据", ""),
        "remark": seg.get("备注", ""),
    }


def judge(meta: dict, cfg: dict, mode: str) -> tuple[str, str]:
    """双模式判定。返回 (verdict, needs_llm)。

    verdict ∈ {private, share, uncertain}
    needs_llm ∈ {'0','1'}

    规则层 P1/P2/P3 都能跑；LLM 层只有 session 模式（P1/P3/P4）能跑。
    见 7.3.4。
    """
    if not meta.get("is_candidate_shared"):
        return "private", "0"
    if meta.get("redacted") or meta.get("_redacted"):
        return "private", "0"
    if cfg.get("auto_share_judge") is False:
        # 开关关闭：规则判不了，但不静默归档——标记待 LLM，交给会话内的 Agent 看一眼
        return "uncertain", "1"
    # auto_share_judge=True：仍需语义判断，session 模式直接由 Agent 判、timer 模式暂存
    return ("uncertain", "0") if mode == "session" else ("uncertain", "1")


def process(r, user: str, store, raw: str, vault: Path, cfg: dict,
            mode: str = "session") -> None:
    ev = json.loads(raw)

    # 幂等：同一 event_id 已处理过则直接确认
    if store.event_seen(ev["event_id"]):
        vq.ack(r, user, raw)
        return

    path = vault / ev["file_path"]
    if ev["change_type"] == "D" or not path.exists():
        store.record_event(ev["event_id"], "done", note="file deleted")
        vq.ack(r, user, raw)
        return

    text = path.read_text(encoding="utf-8")
    meta = parse_frontmatter(text)
    segments = split_sections(text)
    verdict, needs_llm = judge(meta, cfg, mode)

    if verdict == "private":
        store.record_event(ev["event_id"], "done")
        vq.ack(r, user, raw)
        return

    # timer 模式下规则判不了 → 暂存待 LLM，不丢弃、不默认归档
    if mode == "timer" and needs_llm == "1":
        vq.defer_for_llm(r, user, raw, "规则无法判定且无模型可用")
        return

    # verdict == share，或 session 模式下交由 Agent 判定（本例直接入队待审）
    dup, conflict = store.detect_dup_conflict(meta, segments)
    store.upsert_pending(meta, segments, ev,
                         dup_candidates=dup, conflict_candidates=conflict,
                         needs_llm=0)
    store.record_event(ev["event_id"], "done")
    vq.ack(r, user, raw)


def drain_deferred(r, user: str, vault: Path, cfg: dict) -> int:
    """P1 会话内专用：把 P2 暂存的待 LLM 事件捞回来处理。"""
    n = 0
    while True:
        raw = r.cmd("RPOPLPUSH", vq.LLM_PENDING.format(user=user),
                    vq.PROCESSING.format(user=user))
        if not raw:
            break
        process(r, user, _store(), raw, vault, cfg, mode="session")
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault")
    ap.add_argument("--user", required=True, help="队列分片键，见 7.3.3")
    ap.add_argument("--mode", default="session", choices=["session", "timer"])
    ap.add_argument("--once", action="store_true", help="处理完当前队列即退出")
    ap.add_argument("--reclaim", action="store_true", help="先回收超时事件")
    ap.add_argument("--drain-deferred", action="store_true",
                    help="先处理 P2 暂存的待 LLM 事件（session 模式）")
    args = ap.parse_args()

    vault = vault_root(args.vault)
    cfg = load_config(vault)
    r = redis_conn(vault, timeout=5)
    if r is None:
        print(json.dumps({"error": "redis 不可达，消费跳过",
                          "hint": "降级：事件留在本地 local_event，联网后补投"},
                         ensure_ascii=False))
        return 2

    if args.reclaim:
        vq.reclaim_stale(r, args.user)
    if args.drain_deferred and args.mode == "session":
        drain_deferred(r, args.user, vault, cfg)

    while True:
        raw = vq.consume_one(r, args.user, timeout=5)
        if raw is None:
            if args.once:
                break
            continue
        try:
            process(r, args.user, _store(), raw, vault, cfg, mode=args.mode)
        except Exception as e:
            vq.fail(r, args.user, raw, "%s: %s" % (type(e).__name__, e))
    return 0


def _store():
    import vault_store
    return vault_store.open_default()


if __name__ == "__main__":
    sys.exit(main())
