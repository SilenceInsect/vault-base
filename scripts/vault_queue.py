#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_queue.py — 事件入队，双后端（redis_list / mysql）。

Redis LIST 后端语义（键按 user_id 分片，见 7.3.3）：
  生产：LPUSH      vault:events:{user} <json>
  消费：BRPOPLPUSH vault:events:{user} vault:processing:{user} <timeout>   # 原子
  确认：LREM       vault:processing:{user} 1 <json>
  失败：LPUSH      vault:dlq:{user} <json> 后 LREM 确认
  暂存：LPUSH      vault:llm_pending:{user} <json>  # timer 模式判不了时，等会话内消费

基线推进时机：**入队成功后**（若放在消费 ACK 后，消费失败时基线不推进，将导致重复投递）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import vault_root                                    # noqa: E402

QUEUE = "vault:events:{user}"
PROCESSING = "vault:processing:{user}"
DLQ = "vault:dlq:{user}"
LLM_PENDING = "vault:llm_pending:{user}"
USERS_SET = "vault:queue:users"
# 分片理由见 7.3.3：Redis 3.2 无消费组，BRPOPLPUSH 只能阻塞单 key，
# 且 P1（会话内）天然只消费自己产生的事件。


def _keys(user: str) -> tuple[str, str, str, str]:
    return (QUEUE.format(user=user), PROCESSING.format(user=user),
            DLQ.format(user=user), LLM_PENDING.format(user=user))


def register_user(r, user: str) -> None:
    """登记用户，供 P2 定时任务遍历（代替阻塞式 KEYS）。"""
    r.cmd("SADD", USERS_SET, user)


def enqueue(r, user: str, events: list[dict]) -> int:
    """批量入队，返回成功条数。全部成功后才推进基线。"""
    queue, _, _, _ = _keys(user)
    n = 0
    for ev in events:
        raw = json.dumps(ev, ensure_ascii=False, sort_keys=True)
        r.cmd("LPUSH", queue, raw)
        n += 1
    register_user(r, user)
    return n


def consume_one(r, user: str, timeout: int = 5) -> str | None:
    """BRPOPLPUSH 原子取出，返回原始 JSON 字符串。超时返回 None。"""
    queue, processing, _, _ = _keys(user)
    res = r.cmd("BRPOPLPUSH", queue, processing, timeout)
    return res if isinstance(res, str) else None


def ack(r, user: str, raw: str) -> None:
    _, processing, _, _ = _keys(user)
    r.cmd("LREM", processing, "1", raw)


def fail(r, user: str, raw: str, error: str) -> None:
    _, _, dlq, _ = _keys(user)
    try:
        payload = json.loads(raw)
        payload["_error"] = error[:500]
        r.cmd("LPUSH", dlq, json.dumps(payload, ensure_ascii=False, sort_keys=True))
    except Exception:
        r.cmd("LPUSH", dlq, raw)
    ack(r, user, raw)


def defer_for_llm(r, user: str, raw: str, reason: str) -> None:
    """P2 定时任务专用：规则判不了且无模型可用 → 暂存等会话内处理。"""
    _, _, _, pending = _keys(user)
    try:
        payload = json.loads(raw)
        payload["_defer_reason"] = reason[:200]
        r.cmd("LPUSH", pending, json.dumps(payload, ensure_ascii=False, sort_keys=True))
    except Exception:
        r.cmd("LPUSH", pending, raw)
    ack(r, user, raw)


def reclaim_stale(r, user: str, lease_seconds: int = 600) -> int:
    """processing 中超时未确认的事件重新入队（替代 Stream 的 pending 认领）。"""
    from datetime import datetime, timezone
    queue, processing, _, _ = _keys(user)
    items = r.cmd("LRANGE", processing, "0", "-1") or []
    now = datetime.now(timezone.utc)
    moved = 0
    for raw in items:
        try:
            ts = json.loads(raw).get("enqueued_at")
            t = datetime.fromisoformat(ts) if ts else now
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        except Exception:
            t = now
        if (now - t).total_seconds() > lease_seconds:
            r.cmd("LPUSH", queue, raw)
            r.cmd("LREM", processing, "1", raw)
            moved += 1
    return moved


def all_users(r) -> list[str]:
    """P2 定时任务遍历用。不用 KEYS vault:events:*（大库上会阻塞）。"""
    return sorted(r.cmd("SMEMBERS", USERS_SET) or [])
