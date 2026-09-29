#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_consume.py — 事件消费 Agent。

两种运行模式（见 7.3.2 四层调起路径）：
  session  P1/P3/P4：会话内执行，**模型可用**，可做语义判定
  timer    P2：定时任务，**无 Agent 无模型**，只做规则判定；
           规则判不了的事件 defer 到 vault:llm_pending:{user}，不丢弃

流程：BRPOPLPUSH 取出 → 幂等检查 → 解析 MD → 规则+Laya 判定 → 分流 → 确认 / 入 DLQ
队列按 user_id 分片（7.3.3）：Redis 3.2 无消费组，且 P1 天然只消费自己的事件。

Laya：本地 System One 共享门控（见 references/laya.md）；永不直接 approve。
旧配置键 jev.* 仍兼容读取。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault_queue as vq                                              # noqa: E402
from vault_paths import load_config, vault_root                       # noqa: E402
from vault_secrets import redis_conn                                  # noqa: E402

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


def judge_rules(meta: dict, cfg: dict, mode: str) -> dict:
    """规则层判定。返回判定字典（与 Laya 结果同形）。"""
    if not meta.get("is_candidate_shared"):
        return {
            "verdict": "private", "needs_llm": "0",
            "llm_verdict": "private", "llm_confidence": 1.0,
            "llm_reason": "rule:is_candidate_shared=false", "via": "rules",
        }
    if meta.get("redacted") or meta.get("_redacted"):
        return {
            "verdict": "private", "needs_llm": "0",
            "llm_verdict": "private", "llm_confidence": 1.0,
            "llm_reason": "rule:redacted", "via": "rules",
        }
    if cfg.get("auto_share_judge") is False:
        return {
            "verdict": "uncertain", "needs_llm": "1",
            "llm_verdict": "uncertain", "llm_confidence": 0.0,
            "llm_reason": "rule:auto_share_judge=false", "via": "rules",
        }
    # auto_share_judge=True 且无 Laya：session 交 Agent，timer 暂存
    needs = "0" if mode == "session" else "1"
    return {
        "verdict": "uncertain", "needs_llm": needs,
        "llm_verdict": "uncertain", "llm_confidence": 0.0,
        "llm_reason": "rule:need_semantic", "via": "rules",
    }


def _decision_cfg(cfg: dict) -> dict:
    """读取 laya 段；若无则回落旧 jev。"""
    if isinstance(cfg.get("laya"), dict):
        return cfg["laya"]
    if isinstance(cfg.get("jev"), dict):
        return cfg["jev"]
    return {}


def judge(meta: dict, segments: dict, cfg: dict, mode: str,
          vault: Path | None = None) -> dict:
    """规则 →（可选）Laya 共享门控。

    返回 dict：verdict∈{private,share,uncertain}, needs_llm∈{'0','1'}, …
    """
    base = judge_rules(meta, cfg, mode)
    if base["verdict"] == "private":
        return base

    laya_cfg = _decision_cfg(cfg)
    if not laya_cfg.get("enabled") and not laya_cfg.get("mock"):
        return base

    try:
        import laya_client
        force_mock = bool(laya_cfg.get("mock"))
        client = laya_client.open_laya(vault, mock=True if force_mock else None)
        if force_mock:
            client.mock = True
        if not client.available and not client.mock:
            base["llm_reason"] = (base.get("llm_reason") or "") + ";laya_unavailable"
            base["degraded"] = True
            return base
        result = client.share_gate(meta, segments)
        result["via"] = "laya_mock" if client.mock else "laya"
        return result
    except Exception as e:
        base["llm_reason"] = "laya_exception:%s" % e
        base["degraded"] = True
        base["verdict"] = "uncertain"
        base["needs_llm"] = "1"
        return base


def process(r, user: str, store, raw: str, vault: Path, cfg: dict,
            mode: str = "session") -> None:
    ev = json.loads(raw)

    # 幂等：同一 event_id 已处理过则直接确认
    if store.event_seen(ev["event_id"]):
        vq.ack(r, user, raw)
        return

    path = vault / ev["file_path"]
    if ev["change_type"] == "D" or not path.exists():
        store.record_event(ev["event_id"], "done", note="file deleted", ev=ev)
        vq.ack(r, user, raw)
        return

    text = path.read_text(encoding="utf-8")
    meta = parse_frontmatter(text)
    segments = split_sections(text)
    decision = judge(meta, segments, cfg, mode, vault=vault)
    verdict = decision.get("verdict") or "uncertain"
    needs_llm = decision.get("needs_llm") or "1"

    if verdict == "private":
        store.record_event(
            ev["event_id"], "done",
            note="private:%s" % (decision.get("llm_reason") or ""),
            ev=ev,
        )
        vq.ack(r, user, raw)
        return

    # timer 模式下仍需人/会话 → 暂存待 LLM
    if mode == "timer" and needs_llm == "1":
        vq.defer_for_llm(
            r, user, raw,
            decision.get("llm_reason") or "规则/Laya 无法高置信判定",
        )
        return

    # share：入 pending；uncertain：入 pending 并标 needs_llm
    dup, conflict = store.detect_dup_conflict(meta, segments)
    store.upsert_pending(
        meta, segments, ev,
        dup_candidates=dup, conflict_candidates=conflict,
        needs_llm=1 if needs_llm == "1" else 0,
        llm_verdict=decision.get("llm_verdict") or verdict,
        llm_confidence=float(decision.get("llm_confidence") or 0.0),
        llm_reason=decision.get("llm_reason") or "",
    )
    store.record_event(
        ev["event_id"], "done",
        note="pending:%s conf=%.2f via=%s" % (
            verdict,
            float(decision.get("llm_confidence") or 0.0),
            decision.get("via") or "rules",
        ),
        ev=ev,
    )
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
    # socket 超时需大于 BRPOP 等待，避免空队列时 TimeoutError 冒泡
    r = redis_conn(vault, timeout=30)
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
