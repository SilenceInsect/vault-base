#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vault_store.py — 共享/本地知识库存储抽象。"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import vault_root                                                         # noqa: E402
from vault_secrets import DEFAULT_MYSQL_SECTION, mysql_conf                                 # noqa: E402

TZ = timezone(timedelta(hours=8))
DEFAULT_DB = "amrd_qa_vault"


def _now_iso() -> str:
    return datetime.now(TZ).replace(microsecond=0).isoformat()


class VaultStore:
    dialect: str
    server: str

    def event_seen(self, event_id: str) -> bool:
        raise NotImplementedError

    def record_event(self, event_id: str, state: str, note: str = "",
                     ev: dict | None = None) -> None:
        raise NotImplementedError

    def upsert_pending(self, meta: dict, segments: dict, ev: dict, *,
                       dup_candidates: list | None = None,
                       conflict_candidates: list | None = None,
                       needs_llm: int = 0,
                       llm_verdict: str = "",
                       llm_confidence: float = 0.0,
                       llm_reason: str = "") -> int:
        raise NotImplementedError

    def list_pending(self, limit: int = 50,
                     only_needs_llm: bool = False) -> list[dict]:
        raise NotImplementedError

    def approve(self, uuid: str, reviewer: str, note: str = "") -> int:
        raise NotImplementedError

    def reject(self, uuid: str, reviewer: str, note: str = "") -> int:
        raise NotImplementedError

    def detect_dup_conflict(self, meta: dict, segments: dict) -> tuple[list, list]:
        return [], []

    def search(self, query: str, limit: int = 10) -> list[dict]:
        raise NotImplementedError

    def audit(self, uuid: str, action: str, actor: str, **kw) -> None:
        pass

    def merge(self, uuid: str, into_uuid: str, reviewer: str, note: str = "") -> int:
        return 0

    def keep_both(self, uuid: str, reviewer: str, reason: str) -> int:
        return 0

    def supersede(self, old_uuid: str, new_uuid: str, reviewer: str, note: str = "") -> int:
        return 0

    def find_conflicts(self, limit: int = 50) -> list[dict]:
        return []


class SqliteVaultStore(VaultStore):
    def __init__(self, db_path: Path):
        self.dialect = "sqlite"
        self.server = "sqlite:%s" % db_path.name
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        c = self._conn.cursor()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS vault_events (
            event_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pending (
            uuid TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'pending',
            title TEXT,
            body TEXT,
            meta_json TEXT,
            ev_json TEXT,
            dup_json TEXT,
            conflict_json TEXT,
            needs_llm INTEGER DEFAULT 0,
            reviewer TEXT,
            review_note TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS main (
            uuid TEXT PRIMARY KEY,
            title TEXT,
            body TEXT,
            meta_json TEXT,
            status TEXT DEFAULT 'approved',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT,
            action TEXT NOT NULL,
            actor TEXT NOT NULL,
            detail_json TEXT,
            created_at TEXT NOT NULL
        );
        """)
        self._conn.commit()

    def event_seen(self, event_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM vault_events WHERE event_id=?", (event_id,),
        ).fetchone()
        return row is not None

    def record_event(self, event_id: str, state: str, note: str = "",
                     ev: dict | None = None) -> None:
        ts = _now_iso()
        extra = ""
        if ev:
            extra = " ev=" + json.dumps(
                {"file_path": ev.get("file_path"), "change_type": ev.get("change_type")},
                ensure_ascii=False,
            )
        self._conn.execute(
            "INSERT OR REPLACE INTO vault_events(event_id, state, note, created_at) "
            "VALUES (?,?,?,?)",
            (event_id, state, (note or "") + extra, ts),
        )
        self._conn.commit()

    def upsert_pending(self, meta: dict, segments: dict, ev: dict, *,
                       dup_candidates: list | None = None,
                       conflict_candidates: list | None = None,
                       needs_llm: int = 0,
                       llm_verdict: str = "",
                       llm_confidence: float = 0.0,
                       llm_reason: str = "") -> int:
        uid = meta.get("uuid") or ev.get("doc_uuid", "")
        title = meta.get("title", "")
        body = "\n".join(filter(None, [
            segments.get("question", ""),
            segments.get("answer", ""),
            segments.get("basis", ""),
            segments.get("remark", ""),
        ]))
        ts = _now_iso()
        meta_ext = dict(meta)
        if llm_verdict:
            meta_ext["_llm_verdict"] = llm_verdict
            meta_ext["_llm_confidence"] = llm_confidence
            meta_ext["_llm_reason"] = llm_reason
        self._conn.execute(
            "INSERT INTO pending(uuid, status, title, body, meta_json, ev_json, "
            "dup_json, conflict_json, needs_llm, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(uuid) DO UPDATE SET "
            "title=excluded.title, body=excluded.body, meta_json=excluded.meta_json, "
            "ev_json=excluded.ev_json, dup_json=excluded.dup_json, "
            "conflict_json=excluded.conflict_json, needs_llm=excluded.needs_llm, "
            "updated_at=excluded.updated_at",
            (
                uid, "pending", title, body,
                json.dumps(meta_ext, ensure_ascii=False),
                json.dumps(ev, ensure_ascii=False),
                json.dumps(dup_candidates or [], ensure_ascii=False),
                json.dumps(conflict_candidates or [], ensure_ascii=False),
                needs_llm, ts, ts,
            ),
        )
        self._conn.commit()
        return 1

    def list_pending(self, limit: int = 50,
                     only_needs_llm: bool = False) -> list[dict]:
        q = "SELECT * FROM pending WHERE status='pending'"
        if only_needs_llm:
            q += " AND needs_llm=1"
        q += " ORDER BY created_at DESC LIMIT ?"
        rows = self._conn.execute(q, (limit,)).fetchall()
        out = []
        for r in rows:
            out.append({
                "uuid": r["uuid"],
                "status": r["status"],
                "title": r["title"],
                "body": r["body"],
                "meta": json.loads(r["meta_json"] or "{}"),
                "needs_llm": bool(r["needs_llm"]),
                "created_at": r["created_at"],
            })
        return out

    def approve(self, uuid: str, reviewer: str, note: str = "") -> int:
        row = self._conn.execute(
            "SELECT * FROM pending WHERE uuid=? AND status='pending'", (uuid,),
        ).fetchone()
        if not row:
            return 0
        ts = _now_iso()
        self._conn.execute(
            "INSERT OR REPLACE INTO main(uuid, title, body, meta_json, status, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (uuid, row["title"], row["body"], row["meta_json"], "approved", ts, ts),
        )
        self._conn.execute(
            "UPDATE pending SET status='approved', reviewer=?, review_note=?, "
            "reviewed_at=?, updated_at=? WHERE uuid=?",
            (reviewer, note, ts, ts, uuid),
        )
        self.audit(uuid, "approve", reviewer, note=note)
        self._conn.commit()
        return 1

    def reject(self, uuid: str, reviewer: str, note: str = "") -> int:
        row = self._conn.execute(
            "SELECT 1 FROM pending WHERE uuid=? AND status='pending'", (uuid,),
        ).fetchone()
        if not row:
            return 0
        ts = _now_iso()
        self._conn.execute(
            "UPDATE pending SET status='rejected', reviewer=?, review_note=?, "
            "reviewed_at=?, updated_at=? WHERE uuid=?",
            (reviewer, note, ts, ts, uuid),
        )
        self.audit(uuid, "reject", reviewer, note=note)
        self._conn.commit()
        return 1

    def search(self, query: str, limit: int = 10) -> list[dict]:
        like = "%" + query.replace("%", "") + "%"
        rows = self._conn.execute(
            "SELECT uuid, title, body, meta_json FROM main "
            "WHERE title LIKE ? OR body LIKE ? LIMIT ?",
            (like, like, limit),
        ).fetchall()
        return [{
            "uuid": r["uuid"], "title": r["title"], "body": r["body"],
            "meta": json.loads(r["meta_json"] or "{}"),
            "conflict": [],
        } for r in rows]

    def audit(self, uuid: str, action: str, actor: str, **kw) -> None:
        self._conn.execute(
            "INSERT INTO audit(uuid, action, actor, detail_json, created_at) "
            "VALUES (?,?,?,?,?)",
            (uuid, action, actor, json.dumps(kw, ensure_ascii=False), _now_iso()),
        )


def _hash16(value: str) -> str:
    """DDL content_hash 为 CHAR(16)；MD 侧可能带 sha256: 前缀。"""
    h = (value or "").replace("sha256:", "").strip()
    if len(h) >= 16:
        return h[:16]
    return (h + "0" * 16)[:16]


def _as_json(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _as_tags(value) -> str:
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value.strip().startswith("["):
        return value
    return "[]"


def _mysql_now() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S.") + (
        "%03d" % (datetime.now(TZ).microsecond // 1000)
    )


def _row_meta(row: dict) -> dict:
    """从 pending/main 行还原审核侧期望的 meta 字典。"""
    return {
        "uuid": row.get("uuid"),
        "skill_id": row.get("skill_id"),
        "doc_type": row.get("doc_type"),
        "title": row.get("title"),
        "author": row.get("author"),
        "author_ip": row.get("author_ip"),
        "share_rev": row.get("share_rev"),
        "content_hash": row.get("content_hash"),
        "claim_key": row.get("claim_key") or "",
        "source_task_id": row.get("source_task_id") or "",
        "source_ref": row.get("source_ref") or "",
        "tags": row.get("tags") if isinstance(row.get("tags"), list)
                else json.loads(row["tags"]) if isinstance(row.get("tags"), str) and row["tags"]
                else [],
    }


# install_gate / 冒烟用：与 assets/ddl_mysql57.sql 对齐的必填列
MYSQL_REQUIRED_TABLES = {
    "shared_vault_main": {
        "uuid", "skill_id", "doc_type", "title", "body", "segments", "tags",
        "author", "author_ip", "content_hash", "status", "reviewer", "reviewed_at",
    },
    "shared_vault_pending_review": {
        "uuid", "skill_id", "doc_type", "title", "body", "segments", "tags",
        "author", "author_ip", "content_hash", "needs_llm", "status", "enqueued_at",
    },
    "shared_vault_audit_log": {"uuid", "action", "actor", "created_at"},
    "vault_event": {"event_id", "user_id", "skill_id", "doc_uuid", "change_type",
                    "file_path", "payload", "status"},
}
MYSQL_LEGACY_BAD_COLUMNS = {
    "shared_vault_main": {"meta_json"},
    "shared_vault_pending_review": {"meta_json"},
}


class MysqlVaultStore(VaultStore):
    def __init__(self, conf: dict):
        import pymysql
        self._conf = conf
        self.dialect = "mysql"
        self.server = "%s@%s:%s/%s" % (
            conf["user"], conf["host"], conf.get("port", 3306),
            conf.get("database") or DEFAULT_DB,
        )
        self._conn = pymysql.connect(
            host=conf["host"], port=int(conf.get("port") or 3306),
            user=conf["user"], password=conf.get("password"),
            database=conf.get("database") or DEFAULT_DB,
            connect_timeout=4, read_timeout=8,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
            charset="utf8mb4",
        )
        self._fulltext_ok = self._probe_fulltext()

    def _probe_fulltext(self) -> bool:
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM information_schema.statistics "
                    "WHERE table_schema=%s AND table_name='shared_vault_main' "
                    "AND index_type='FULLTEXT' LIMIT 1",
                    (self._conf.get("database") or DEFAULT_DB,),
                )
                return cur.fetchone() is not None
        except Exception:
            return False

    def _ensure_tables(self) -> None:
        pass  # 不在 208 自动跑 DDL

    @classmethod
    def check_schema(cls, conf: dict) -> dict:
        """安装门禁用：校验库表是否与 DDL / 本适配层一致。"""
        import pymysql
        db = conf.get("database") or DEFAULT_DB
        out = {"ok": True, "database": db, "missing_tables": [],
               "missing_columns": {}, "legacy_mismatch": [], "detail": ""}
        try:
            conn = pymysql.connect(
                host=conf["host"], port=int(conf.get("port") or 3306),
                user=conf["user"], password=conf.get("password"),
                database=db, connect_timeout=4,
                cursorclass=pymysql.cursors.DictCursor,
            )
        except Exception as e:
            return {"ok": False, "error": str(e), "detail": "mysql connect failed"}
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW TABLES")
                tables = {list(r.values())[0] for r in cur.fetchall()}
                if "vault_events" in tables and "vault_event" not in tables:
                    out["legacy_mismatch"].append(
                        "found vault_events (legacy); expect vault_event per ddl_mysql57.sql"
                    )
                    out["ok"] = False
                for table, cols in MYSQL_REQUIRED_TABLES.items():
                    if table not in tables:
                        out["missing_tables"].append(table)
                        out["ok"] = False
                        continue
                    cur.execute(
                        "SELECT COLUMN_NAME FROM information_schema.columns "
                        "WHERE table_schema=%s AND table_name=%s",
                        (db, table),
                    )
                    have = {r["COLUMN_NAME"] for r in cur.fetchall()}
                    miss = sorted(cols - have)
                    if miss:
                        out["missing_columns"][table] = miss
                        out["ok"] = False
                    bad = MYSQL_LEGACY_BAD_COLUMNS.get(table) or set()
                    if bad & have:
                        out["legacy_mismatch"].append(
                            "%s has legacy columns %s (store/DDL mismatch)"
                            % (table, sorted(bad & have))
                        )
                        out["ok"] = False
            out["detail"] = "schema ok" if out["ok"] else "schema incompatible with MysqlVaultStore"
            return out
        finally:
            conn.close()

    def event_seen(self, event_id: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM vault_event WHERE event_id=%s "
                "AND status IN ('done','dead') LIMIT 1",
                (event_id,),
            )
            return cur.fetchone() is not None

    def record_event(self, event_id: str, state: str, note: str = "",
                     ev: dict | None = None) -> None:
        """写入 vault_event；state 映射为 status（done/dead/processing/pending）。"""
        status = state if state in ("pending", "processing", "done", "dead") else "done"
        ev = ev or {}
        change = (ev.get("change_type") or "M")[:1].upper()
        if change not in ("A", "M", "D", "R"):
            change = "M"
        doc_uuid = (ev.get("doc_uuid") or "00000000-0000-4000-8000-000000000000")[:36]
        ts = _mysql_now()
        payload = dict(ev)
        if note:
            payload["_note"] = note
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO vault_event "
                "(event_id, user_id, skill_id, doc_uuid, change_type, old_path, "
                "file_path, blob_hash, base_commit, head_commit, payload, needs_llm, "
                "status, last_error, finished_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE status=VALUES(status), "
                "last_error=VALUES(last_error), finished_at=VALUES(finished_at), "
                "payload=VALUES(payload)",
                (
                    event_id,
                    ev.get("user_id") or "unknown",
                    ev.get("skill_id") or "unknown",
                    doc_uuid,
                    change,
                    ev.get("old_path"),
                    ev.get("file_path") or "",
                    ev.get("blob_hash"),
                    ev.get("base_commit"),
                    ev.get("head_commit"),
                    _as_json(payload),
                    int(ev.get("needs_llm") or 0),
                    status,
                    (note or None),
                    ts if status in ("done", "dead") else None,
                ),
            )
        self._conn.commit()

    def upsert_pending(self, meta: dict, segments: dict, ev: dict, *,
                       dup_candidates: list | None = None,
                       conflict_candidates: list | None = None,
                       needs_llm: int = 0,
                       llm_verdict: str = "",
                       llm_confidence: float = 0.0,
                       llm_reason: str = "") -> int:
        uid = meta.get("uuid") or ev.get("doc_uuid", "")
        title = meta.get("title") or uid
        body_parts = []
        for key, label in (("question", "问题"), ("answer", "答案&决策"),
                           ("basis", "决策依据"), ("remark", "备注")):
            val = (segments or {}).get(key) or ""
            if val:
                body_parts.append("## %s\n%s" % (label, val))
        body = "\n\n".join(body_parts) if body_parts else (title or "")
        doc_type = meta.get("doc_type") or "answer"
        if doc_type not in ("answer", "decision", "reference", "brief"):
            doc_type = "answer"
        share_rev = int(meta.get("share_rev") or 1)
        doc_rev = int(meta.get("doc_rev") or meta.get("brief_rev") or 1)
        # DDL: llm_verdict ENUM('share','private','uncertain')
        lv = (llm_verdict or "").strip().lower()
        if lv not in ("share", "private", "uncertain"):
            lv = None
        conf = float(llm_confidence or 0.0)
        if conf < 0:
            conf = 0.0
        if conf > 1:
            conf = 1.0
        reason = (llm_reason or "")[:512] or None
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO shared_vault_pending_review "
                "(uuid, schema_version, skill_id, doc_type, title, body, segments, tags, "
                "author, author_ip, source_task_id, source_ref, content_hash, share_rev, "
                "doc_rev, claim_key, dup_candidates, conflict_candidates, needs_llm, "
                "defer_reason, status, llm_verdict, llm_reason, llm_confidence, "
                "source_event_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "%s,'pending',%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE title=VALUES(title), body=VALUES(body), "
                "segments=VALUES(segments), tags=VALUES(tags), "
                "dup_candidates=VALUES(dup_candidates), "
                "conflict_candidates=VALUES(conflict_candidates), "
                "needs_llm=VALUES(needs_llm), defer_reason=VALUES(defer_reason), "
                "llm_verdict=VALUES(llm_verdict), llm_reason=VALUES(llm_reason), "
                "llm_confidence=VALUES(llm_confidence), status='pending', "
                "source_event_id=VALUES(source_event_id)",
                (
                    uid,
                    int(meta.get("schema_version") or 1),
                    meta.get("skill_id") or ev.get("skill_id") or "unknown",
                    doc_type,
                    title,
                    body,
                    _as_json(segments or {}),
                    _as_tags(meta.get("tags")),
                    meta.get("author") or "unknown",
                    meta.get("author_ip") or "0.0.0.0",
                    meta.get("source_task_id") or None,
                    meta.get("source_ref") or None,
                    _hash16(meta.get("content_hash") or ""),
                    share_rev,
                    doc_rev,
                    meta.get("claim_key") or None,
                    _as_json(dup_candidates or []),
                    _as_json(conflict_candidates or []),
                    int(needs_llm),
                    reason if needs_llm else None,
                    lv,
                    reason,
                    conf if lv else None,
                    ev.get("event_id"),
                ),
            )
        self._conn.commit()
        return 1

    def list_pending(self, limit: int = 50,
                     only_needs_llm: bool = False) -> list[dict]:
        q = ("SELECT uuid, status, title, body, skill_id, doc_type, author, author_ip, "
             "share_rev, content_hash, tags, needs_llm, enqueued_at, source_task_id, "
             "source_ref "
             "FROM shared_vault_pending_review WHERE status='pending'")
        if only_needs_llm:
            q += " AND needs_llm=1"
        q += " ORDER BY enqueued_at DESC LIMIT %s"
        with self._conn.cursor() as cur:
            cur.execute(q, (limit,))
            rows = cur.fetchall()
        return [{
            "uuid": r["uuid"], "status": r["status"], "title": r["title"],
            "body": r["body"],
            "meta": _row_meta(r),
            "needs_llm": bool(r.get("needs_llm")),
            "created_at": str(r.get("enqueued_at", "")),
        } for r in rows]

    def approve(self, uuid: str, reviewer: str, note: str = "") -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM shared_vault_pending_review "
                "WHERE uuid=%s AND status='pending' ORDER BY share_rev DESC LIMIT 1",
                (uuid,),
            )
            row = cur.fetchone()
            if not row:
                return 0
            ts = _mysql_now()
            created = row.get("enqueued_at") or ts
            cur.execute(
                "INSERT INTO shared_vault_main "
                "(uuid, schema_version, skill_id, doc_type, title, body, segments, tags, "
                "author, author_ip, source_task_id, source_ref, content_hash, share_rev, "
                "doc_rev, claim_key, status, reviewer, reviewed_at, review_note, "
                "created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',"
                "%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE title=VALUES(title), body=VALUES(body), "
                "segments=VALUES(segments), tags=VALUES(tags), status='active', "
                "reviewer=VALUES(reviewer), reviewed_at=VALUES(reviewed_at), "
                "review_note=VALUES(review_note), updated_at=VALUES(updated_at), "
                "share_rev=VALUES(share_rev)",
                (
                    row["uuid"], row.get("schema_version") or 1, row["skill_id"],
                    row["doc_type"], row["title"], row["body"],
                    _as_json(row.get("segments") or {}),
                    _as_tags(row.get("tags")),
                    row["author"], row["author_ip"],
                    row.get("source_task_id"), row.get("source_ref"),
                    _hash16(row.get("content_hash") or ""),
                    row.get("share_rev") or 1, row.get("doc_rev") or 1,
                    row.get("claim_key"),
                    reviewer, ts, note or None, created, ts,
                ),
            )
            cur.execute(
                "UPDATE shared_vault_pending_review SET status='approved', "
                "reviewer=%s, review_note=%s, reviewed_at=%s "
                "WHERE uuid=%s AND status='pending'",
                (reviewer, note or None, ts, uuid),
            )
        self._conn.commit()
        self.audit(uuid, "approve", reviewer, note=note,
                   target_table="shared_vault_main")
        # prefixCache 写穿：active 文档入 vault:c:
        try:
            import vault_prefix_cache as vpc
            cache = vpc.open_cache()
            try:
                row_for_cache = dict(row)
                row_for_cache["status"] = "active"
                cache.put_from_row(row_for_cache, source="shared_mysql")
                cache.invalidate_skill_queries(row.get("skill_id") or "")
            finally:
                cache.close()
        except Exception:
            pass
        return 1

    def reject(self, uuid: str, reviewer: str, note: str = "") -> int:
        skill_id = ""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT skill_id FROM shared_vault_pending_review "
                "WHERE uuid=%s AND status='pending' LIMIT 1",
                (uuid,),
            )
            prev = cur.fetchone()
            if prev:
                skill_id = prev.get("skill_id") or ""
            cur.execute(
                "UPDATE shared_vault_pending_review SET status='rejected', "
                "reviewer=%s, review_note=%s, reviewed_at=%s "
                "WHERE uuid=%s AND status='pending'",
                (reviewer, note, _mysql_now(), uuid),
            )
            n = cur.rowcount
        self._conn.commit()
        if n:
            self.audit(uuid, "reject", reviewer, note=note,
                       target_table="shared_vault_pending_review")
            try:
                import vault_prefix_cache as vpc
                cache = vpc.open_cache()
                try:
                    cache.invalidate_doc(uuid, skill_id=skill_id)
                finally:
                    cache.close()
            except Exception:
                pass
        return n

    def get_by_uuid(self, uuid: str) -> dict | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT uuid, title, body, skill_id, doc_type, author, author_ip, "
                "share_rev, content_hash, tags, source_task_id, source_ref, claim_key "
                "FROM shared_vault_main WHERE uuid=%s AND status='active' LIMIT 1",
                (uuid,),
            )
            r = cur.fetchone()
        if not r:
            return None
        return {
            "uuid": r["uuid"], "title": r["title"], "body": r["body"],
            "meta": _row_meta(r), "conflict": [],
            "skill_id": r.get("skill_id"), "doc_type": r.get("doc_type"),
            "claim_key": r.get("claim_key") or "",
        }

    def list_by_skill(self, skill_id: str, limit: int = 20) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT uuid, title, body, skill_id, doc_type, author, author_ip, "
                "share_rev, content_hash, tags, source_task_id, source_ref, claim_key "
                "FROM shared_vault_main WHERE skill_id=%s AND status='active' "
                "ORDER BY updated_at DESC LIMIT %s",
                (skill_id, limit),
            )
            rows = cur.fetchall()
        return [{
            "uuid": r["uuid"], "title": r["title"], "body": r["body"],
            "meta": _row_meta(r), "conflict": [],
            "skill_id": r.get("skill_id"), "doc_type": r.get("doc_type"),
            "claim_key": r.get("claim_key") or "",
        } for r in rows]

    def search(self, query: str, limit: int = 10) -> list[dict]:
        def _pack(rows, source_tag: str = "shared_mysql"):
            out = []
            for r in rows:
                item = {
                    "uuid": r["uuid"], "title": r["title"], "body": r["body"],
                    "meta": _row_meta(r),
                    "conflict": [],
                    "skill_id": r.get("skill_id"),
                    "doc_type": r.get("doc_type"),
                    "claim_key": r.get("claim_key") or "",
                    "source": source_tag,
                }
                out.append(item)
            return out

        if self._fulltext_ok:
            try:
                with self._conn.cursor() as cur:
                    cur.execute(
                        "SELECT uuid, title, body, skill_id, doc_type, author, author_ip, "
                        "share_rev, content_hash, tags, source_task_id, source_ref, claim_key "
                        "FROM shared_vault_main "
                        "WHERE status='active' AND "
                        "MATCH(title, body) AGAINST (%s IN BOOLEAN MODE) LIMIT %s",
                        (query, limit),
                    )
                    packed = _pack(cur.fetchall())
                    self._cache_fill(packed)
                    return packed
            except Exception:
                pass
        like = "%" + query.replace("%", "").replace("_", "") + "%"
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT uuid, title, body, skill_id, doc_type, author, author_ip, "
                "share_rev, content_hash, tags, source_task_id, source_ref, claim_key "
                "FROM shared_vault_main "
                "WHERE status='active' AND (title LIKE %s OR body LIKE %s) LIMIT %s",
                (like, like, limit),
            )
            packed = _pack(cur.fetchall())
        self._cache_fill(packed)
        return packed

    def _cache_fill(self, rows: list[dict]) -> None:
        if not rows:
            return
        try:
            import vault_prefix_cache as vpc
            cache = vpc.open_cache()
            try:
                for row in rows:
                    cache.put_from_row(row, source="shared_mysql")
            finally:
                cache.close()
        except Exception:
            pass

    def audit(self, uuid: str, action: str, actor: str, **kw) -> None:
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO shared_vault_audit_log "
                    "(uuid, action, actor, actor_ip, target_table, snapshot, reason) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        uuid, action, actor,
                        kw.get("actor_ip"),
                        kw.get("target_table"),
                        _as_json({k: v for k, v in kw.items()
                                  if k not in ("actor_ip", "target_table", "note")}),
                        kw.get("note") or kw.get("reason"),
                    ),
                )
            self._conn.commit()
        except Exception:
            pass


def open_default(root: Path | None = None) -> VaultStore:
    """连接优先级：MySQL（凭据齐备且库存在）→ SQLite 兜底。"""
    root = root or vault_root()
    conf = mysql_conf(root, DEFAULT_MYSQL_SECTION)
    if conf and conf.get("host") and conf.get("user"):
        try:
            import pymysql
            conn = pymysql.connect(
                host=conf["host"], port=int(conf.get("port") or 3306),
                user=conf["user"], password=conf.get("password"),
                database=conf.get("database") or DEFAULT_DB,
                connect_timeout=3,
            )
            conn.close()
            return MysqlVaultStore(conf)
        except Exception:
            pass
    db_path = root / "_kb" / "vault.local.db"
    return SqliteVaultStore(db_path)
