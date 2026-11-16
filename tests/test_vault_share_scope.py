# -*- coding: utf-8 -*-
"""本机环境门禁：dump 强制 private + approve 复检不得进公共主库。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vault_dump import dump_md  # noqa: E402
from vault_redact import MACHINE_CATEGORIES, scan as redact_scan  # noqa: E402
from vault_share_scope import approve_blocked_reason, evaluate_share  # noqa: E402
from vault_store import SqliteVaultStore  # noqa: E402


class RedactMachinePathTests(unittest.TestCase):
    def test_win_abs_path_hit(self):
        red = redact_scan(r"后端代码 E:\AM-RD_Server\trunk\game")
        self.assertTrue(red.hit)
        self.assertIn("WIN_ABS_PATH", red.categories)
        self.assertTrue(red.machine_bound)

    def test_unix_home_path_hit(self):
        red = redact_scan("clone to /Users/alice/work/amrd")
        self.assertTrue(red.hit)
        self.assertIn("UNIX_HOME_PATH", red.categories)

    def test_relative_and_svn_ok(self):
        red = redact_scan(
            "策划表在 Design 仓 trunk/原始表格；"
            "svn http://svn.example/svn/AM-RD_Design/trunk/原始表格"
        )
        self.assertFalse(red.hit)

    def test_owner_scoped_tag(self):
        red = redact_scan("scope: local_personal\nowner: quanguanming")
        self.assertTrue(red.hit)
        self.assertTrue(set(red.categories) & MACHINE_CATEGORIES)


class DumpForcePrivateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "answers").mkdir()
        (self.root / "decisions").mkdir()
        (self.root / "references").mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _base(self, **kw):
        base = dict(
            skill_id="req-config-validate",
            doc_type="reference",
            author="tester",
            title="本机路径门禁回归标题足够长",
            question="AMRD trunk 后端前端策划配置路径是什么？",
            answer=(
                "本机路径：后端 E:\\AM-RD_Server\\trunk\\game；"
                "前端 F:\\RDClient；策划 D:\\AM-RD_Design\\trunk\\原始表格。"
                "仅供本机校验前置物料，不得当作团队默认。"
            ),
            basis="tests/test_vault_share_scope.py",
            source_ref="tests/test_vault_share_scope.py",
            claim_key="req-config-validate/share-scope-gate-regression",
            strict=False,
            root=self.root,
        )
        base.update(kw)
        return dump_md(**base)

    def test_candidate_shared_forced_private_on_win_path(self):
        res = self._base(
            is_candidate_shared=True,
            share_scope="team",
            owner="quanguanming",
        )
        self.assertNotIn("error", res)
        self.assertFalse(res.get("is_candidate_shared"))
        self.assertEqual(res.get("share_scope"), "private")
        self.assertTrue(res.get("forced_private") or res.get("redacted"))
        text = Path(res["path"]).read_text(encoding="utf-8")
        self.assertIn("is_candidate_shared: false", text)
        self.assertIn("share_scope: \"private\"", text)

    def test_strict_team_with_machine_path_fails_gate(self):
        res = self._base(
            is_candidate_shared=True,
            share_scope="team",
            strict=True,
            claim_key="req-config-validate/share-scope-gate-strict",
        )
        self.assertEqual(res.get("error"), "quality_gate_failed")
        self.assertFalse(res.get("ok"))

    def test_portable_team_share_allowed(self):
        res = dump_md(
            skill_id="req-config-validate",
            doc_type="reference",
            author="tester",
            title="可移植策划表路径约定足够长",
            question="策划配置在哪个逻辑路径？",
            answer=(
                "策划配置在 Design 仓 trunk/原始表格 层；"
                "后端在 Server 仓 trunk/game；前端在 Client 仓根。"
                "不写本机盘符，换机仍成立。"
            ),
            basis="tests/test_vault_share_scope.py",
            source_ref="tests/test_vault_share_scope.py",
            claim_key="req-config-validate/portable-design-path",
            is_candidate_shared=True,
            share_scope="team",
            strict=False,
            root=self.root,
        )
        self.assertNotIn("error", res)
        self.assertTrue(res.get("is_candidate_shared"))
        self.assertEqual(res.get("share_scope"), "team")


class ApproveRescanTests(unittest.TestCase):
    def test_approve_blocked_reason_on_drive_path(self):
        reason = approve_blocked_reason(
            title="本机路径",
            body=r"后端 E:\AM-RD_Server\trunk\game",
        )
        self.assertIsNotNone(reason)
        self.assertIn("WIN_ABS_PATH", reason or "")

    def test_sqlite_approve_rejects_machine_path(self):
        td = tempfile.TemporaryDirectory()
        try:
            db = Path(td.name) / "t.db"
            store = SqliteVaultStore(db)
            uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            store.upsert_pending(
                {
                    "uuid": uid,
                    "title": "本机路径不得进主库",
                    "skill_id": "req-config-validate",
                    "share_scope": "team",
                },
                {
                    "question": "路径？",
                    "answer": r"前端 F:\RDClient 为本机工作区",
                    "basis": "test",
                    "remark": "",
                },
                {"event_id": "ev-share-scope-1", "doc_uuid": uid},
                llm_verdict="share",
            )
            n = store.approve(uid, "reviewer", note="should-block")
            self.assertEqual(n, 0)
            self.assertTrue(store.last_approve_error)
            self.assertIn("share_gate_blocked", store.last_approve_error)
            pending = store.list_pending()
            self.assertEqual(pending, [])
            # rejected, not in main
            row = store._conn.execute(
                "SELECT status FROM pending WHERE uuid=?", (uid,),
            ).fetchone()
            self.assertEqual(row["status"], "rejected")
            main = store._conn.execute(
                "SELECT 1 FROM main WHERE uuid=?", (uid,),
            ).fetchone()
            self.assertIsNone(main)
            store._conn.close()
        finally:
            try:
                td.cleanup()
            except PermissionError:
                pass

    def test_evaluate_share_downgrades_without_strict(self):
        ev = evaluate_share(
            text=r"path E:\AM-RD_Server\trunk\game",
            share_scope="team",
            is_candidate_shared=True,
            strict=False,
        )
        self.assertFalse(ev["is_candidate_shared"])
        self.assertEqual(ev["share_scope"], "private")
        self.assertTrue(ev["forced_private"])


if __name__ == "__main__":
    unittest.main()
