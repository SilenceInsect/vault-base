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

    def record_event(self, event_id: str, state: str, note: str = "") -> None:
        raise NotImplementedError

    def upsert_pending(self, meta: dict, segments: dict, ev: dict, *,
                       dup_candidates: list | None = None,
                       conflict_candidates: list | None = None,
                       needs_llm: int = 0) -> int:
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

    def record_event(self, event_id: str, state: str, note: str = "") -> None:
        ts = _now_iso()
        self._conn.execute(
            "INSERT OR REPLACE INTO vault_events(event_id, state, note, created_at) "
            "VALUES (?,?,?,?)",
            (event_id, state, note, ts),
        )
        self._conn.commit()

    def upsert_pending(self, meta: dict, segments: dict, ev: dict, *,
                       dup_candidates: list | None = None,
                       conflict_candidates: list | None = None,
                       needs_llm: int = 0) -> int:
        uid = meta.get("uuid") or ev.get("doc_uuid", "")
        title = meta.get("title", "")
        body = "\n".join(filter(None, [
            segments.get("question", ""),
            segments.get("answer", ""),
            segments.get("basis", ""),
            segments.get("remark", ""),
        ]))
        ts = _now_iso()
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
                json.dumps(meta, ensure_ascii=False),
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
        )
        self._fulltext_ok = self._probe_fulltext()

    def _probe_fulltext(self) -> bool:
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema=%s AND table_name='shared_vault_main'",
                    (self._conf.get("database") or DEFAULT_DB,),
                )
                return cur.fetchone() is not None
        except Exception:
            return False

    def _ensure_tables(self) -> None:
        pass  # 不在 208 自动跑 DDL

    def event_seen(self, event_id: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM vault_events WHERE event_id=%s LIMIT 1", (event_id,),
            )
            return cur.fetchone() is not None

    def record_event(self, event_id: str, state: str, note: str = "") -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO vault_events(event_id, state, note, created_at) "
                "VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE state=VALUES(state)",
                (event_id, state, note, _now_iso()),
            )
        self._conn.commit()

    def upsert_pending(self, meta: dict, segments: dict, ev: dict, *,
                       dup_candidates: list | None = None,
                       conflict_candidates: list | None = None,
                       needs_llm: int = 0) -> int:
        uid = meta.get("uuid") or ev.get("doc_uuid", "")
        title = meta.get("title", "")
        body = "\n".join(filter(None, [
            segments.get("question", ""), segments.get("answer", ""),
            segments.get("basis", ""), segments.get("remark", ""),
        ]))
        ts = _now_iso()
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO shared_vault_pending_review "
                "(uuid, status, title, body, meta_json, needs_llm, created_at, updated_at) "
                "VALUES (%s,'pending',%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE title=VALUES(title), body=VALUES(body), "
                "updated_at=VALUES(updated_at)",
                (uid, title, body, json.dumps(meta, ensure_ascii=False),
                 needs_llm, ts, ts),
            )
        self._conn.commit()
        return 1

    def list_pending(self, limit: int = 50,
                     only_needs_llm: bool = False) -> list[dict]:
        q = ("SELECT uuid, status, title, body, meta_json, needs_llm, created_at "
             "FROM shared_vault_pending_review WHERE status='pending'")
        if only_needs_llm:
            q += " AND needs_llm=1"
        q += " ORDER BY created_at DESC LIMIT %s"
        with self._conn.cursor() as cur:
            cur.execute(q, (limit,))
            rows = cur.fetchall()
        return [{
            "uuid": r["uuid"], "status": r["status"], "title": r["title"],
            "body": r["body"],
            "meta": json.loads(r["meta_json"] or "{}") if isinstance(r["meta_json"], str)
                    else (r["meta_json"] or {}),
            "needs_llm": bool(r.get("needs_llm")),
            "created_at": str(r.get("created_at", "")),
        } for r in rows]

    def approve(self, uuid: str, reviewer: str, note: str = "") -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM shared_vault_pending_review WHERE uuid=%s AND status='pending'",
                (uuid,),
            )
            row = cur.fetchone()
            if not row:
                return 0
            ts = _now_iso()
            cur.execute(
                "INSERT INTO shared_vault_main(uuid, title, body, meta_json, status, "
                "created_at, updated_at) VALUES (%s,%s,%s,%s,'approved',%s,%s) "
                "ON DUPLICATE KEY UPDATE title=VALUES(title), body=VALUES(body), "
                "updated_at=VALUES(updated_at)",
                (uuid, row["title"], row["body"], row["meta_json"], ts, ts),
            )
            cur.execute(
                "UPDATE shared_vault_pending_review SET status='approved', "
                "reviewer=%s, review_note=%s, reviewed_at=%s WHERE uuid=%s",
                (reviewer, note, ts, uuid),
            )
        self._conn.commit()
        self.audit(uuid, "approve", reviewer, note=note)
        return 1

    def reject(self, uuid: str, reviewer: str, note: str = "") -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE shared_vault_pending_review SET status='rejected', "
                "reviewer=%s, review_note=%s, reviewed_at=%s WHERE uuid=%s AND status='pending'",
                (reviewer, note, _now_iso(), uuid),
            )
            n = cur.rowcount
        self._conn.commit()
        if n:
            self.audit(uuid, "reject", reviewer, note=note)
        return n

    def search(self, query: str, limit: int = 10) -> list[dict]:
        if self._fulltext_ok:
            try:
                with self._conn.cursor() as cur:
                    cur.execute(
                        "SELECT uuid, title, body, meta_json FROM shared_vault_main "
                        "WHERE MATCH(title, body) AGAINST (%s IN BOOLEAN MODE) LIMIT %s",
                        (query, limit),
                    )
                    rows = cur.fetchall()
                return [{
                    "uuid": r["uuid"], "title": r["title"], "body": r["body"],
                    "meta": json.loads(r["meta_json"] or "{}") if isinstance(r["meta_json"], str)
                            else (r["meta_json"] or {}),
                    "conflict": [],
                } for r in rows]
            except Exception:
                pass
        import warnings
        warnings.warn("MySQL FULLTEXT unavailable, falling back to LIKE")
        like = "%" + query.replace("%", "") + "%"
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT uuid, title, body, meta_json FROM shared_vault_main "
                "WHERE title LIKE %s OR body LIKE %s LIMIT %s",
                (like, like, limit),
            )
            rows = cur.fetchall()
        return [{
            "uuid": r["uuid"], "title": r["title"], "body": r["body"],
            "meta": json.loads(r["meta_json"] or "{}") if isinstance(r["meta_json"], str)
                    else (r["meta_json"] or {}),
            "conflict": [],
        } for r in rows]

    def audit(self, uuid: str, action: str, actor: str, **kw) -> None:
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO shared_vault_audit_log "
                    "(uuid, action, actor, detail_json, created_at) VALUES (%s,%s,%s,%s,%s)",
                    (uuid, action, actor, json.dumps(kw, ensure_ascii=False), _now_iso()),
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
