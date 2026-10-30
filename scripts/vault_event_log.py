#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_event_log.py — 金库写入 / 技能触发的统一事件日志 + chat 显式提示。

落库：
  1. MySQL ``vault_skill_event_log``（缺表则 CREATE IF NOT EXISTS）
  2. 本地 JSONL 兜底：``<vault>/_kb/skill_event_log.jsonl``
  3. 共享审核类仍可走 ``shared_vault_audit_log``（submit 等）

chat 提示：
  返回 ``chat_notice`` 字符串；调用方须把该字段原样打印到 stdout，
  Agent 必须在 Chat 窗口显式复述给用户（见 SKILL.vault-section.md）。
"""
from __future__ import annotations

import json
import socket
import uuid as uuidlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from vault_paths import vault_root

TZ = timezone(timedelta(hours=8))

DDL_SKILL_EVENT = """
CREATE TABLE IF NOT EXISTS `vault_skill_event_log` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `event_id`     CHAR(36)        NOT NULL,
  `event_type`   VARCHAR(32)     NOT NULL COMMENT 'write|trigger|review|consume',
  `action`       VARCHAR(64)     NOT NULL COMMENT 'vault_dump|pre_task_recall|sediment|approve…',
  `skill_id`     VARCHAR(64)     NOT NULL DEFAULT '',
  `doc_uuid`     CHAR(36)        NULL,
  `actor`        VARCHAR(64)     NOT NULL DEFAULT '',
  `actor_ip`     VARCHAR(45)     NULL,
  `title`        VARCHAR(256)    NULL,
  `detail_json`  JSON            NULL,
  `chat_notice`  VARCHAR(512)    NULL,
  `created_at`   DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_event` (`event_id`),
  KEY `idx_skill_time` (`skill_id`, `created_at`),
  KEY `idx_type_time` (`event_type`, `created_at`),
  KEY `idx_doc` (`doc_uuid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='技能触发与金库写入事件日志'
"""

ACTION_LABEL = {
    "vault_dump": "金库写入",
    "pre_task_recall": "技能触发·前置召回",
    "post_task_sediment": "技能触发·过程沉淀",
    "vault_consume": "事件消费入队",
    "vault_review_approve": "人审通过",
    "vault_review_reject": "人审拒绝",
    "skill_adapt": "技能适配",
}


def _now() -> str:
    return datetime.now(TZ).replace(microsecond=0).isoformat()


def _local_ip() -> str:
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return ""


def format_chat_notice(
    *,
    action: str,
    skill_id: str = "",
    title: str = "",
    doc_uuid: str = "",
    shared: bool | None = None,
    extra: str = "",
) -> str:
    """生成须在 Chat 窗口显式展示的一行提示。"""
    label = ACTION_LABEL.get(action, action)
    parts = ["【金库提示】%s" % label]
    if skill_id:
        parts.append("skill=%s" % skill_id)
    if title:
        parts.append("title=%s" % (title[:60] + ("…" if len(title) > 60 else "")))
    if doc_uuid:
        parts.append("uuid=%s" % doc_uuid)
    if shared is not None:
        parts.append("shared=%s" % ("yes" if shared else "no"))
    if extra:
        parts.append(extra)
    return " | ".join(parts)


def _jsonl_append(root: Path, record: dict) -> Path:
    p = root / "_kb" / "skill_event_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return p


def _mysql_insert(record: dict) -> bool:
    try:
        import vault_store
        store = vault_store.open_default()
        if type(store).__name__ != "MysqlVaultStore":
            return False
        with store._conn.cursor() as cur:
            cur.execute(DDL_SKILL_EVENT)
            cur.execute(
                "INSERT INTO vault_skill_event_log "
                "(event_id, event_type, action, skill_id, doc_uuid, actor, actor_ip, "
                "title, detail_json, chat_notice) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    record["event_id"],
                    record["event_type"],
                    record["action"],
                    record.get("skill_id") or "",
                    record.get("doc_uuid"),
                    record.get("actor") or "",
                    record.get("actor_ip"),
                    (record.get("title") or "")[:256] or None,
                    json.dumps(record.get("detail") or {}, ensure_ascii=False),
                    (record.get("chat_notice") or "")[:512] or None,
                ),
            )
        store._conn.commit()
        return True
    except Exception:
        return False


def _audit_submit(record: dict) -> None:
    """共享写入类额外写 shared_vault_audit_log（action=submit）。"""
    if record.get("event_type") != "write":
        return
    doc = record.get("doc_uuid")
    if not doc:
        return
    try:
        import vault_store
        store = vault_store.open_default()
        store.audit(
            doc,
            "submit",
            record.get("actor") or "vault",
            actor_ip=record.get("actor_ip"),
            target_table="local_vault_md",
            note=record.get("action"),
            skill_id=record.get("skill_id"),
            title=record.get("title"),
            chat_notice=record.get("chat_notice"),
        )
    except Exception:
        pass


def log_event(
    *,
    event_type: str,
    action: str,
    skill_id: str = "",
    doc_uuid: str = "",
    actor: str = "",
    title: str = "",
    shared: bool | None = None,
    detail: dict | None = None,
    root: Path | None = None,
    print_notice: bool = True,
) -> dict[str, Any]:
    """记录一条事件并返回含 chat_notice 的结果。

    print_notice=True 时向 stdout 打印一行【金库提示】，供 Agent 原样转述。
    """
    root = root or vault_root()
    notice = format_chat_notice(
        action=action,
        skill_id=skill_id,
        title=title,
        doc_uuid=doc_uuid,
        shared=shared,
    )
    record = {
        "event_id": str(uuidlib.uuid4()),
        "event_type": event_type,
        "action": action,
        "skill_id": skill_id or "",
        "doc_uuid": doc_uuid or None,
        "actor": actor or "",
        "actor_ip": _local_ip(),
        "title": title or "",
        "shared": shared,
        "detail": detail or {},
        "chat_notice": notice,
        "created_at": _now(),
    }
    local_path = _jsonl_append(root, record)
    mysql_ok = _mysql_insert(record)
    _audit_submit(record)
    record["mysql_ok"] = mysql_ok
    record["local_log"] = str(local_path)
    if print_notice:
        # 独立一行，便于 Chat / 日志扫描
        print(notice, flush=True)
    return record


def log_write(**kw) -> dict:
    return log_event(event_type="write", **kw)


def log_trigger(**kw) -> dict:
    return log_event(event_type="trigger", **kw)
