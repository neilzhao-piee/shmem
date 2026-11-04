#!/usr/bin/env python3
"""Unit tests for the reference implementation (stdlib only; this is the CI gate).

Run: python3 tests/test_shmem.py   (or: make test)
"""
import importlib.util
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("shmem", ROOT / "tools" / "memory-search.py")
shmem = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shmem)
sum_spec = importlib.util.spec_from_file_location("shmem_summarize", ROOT / "tools" / "summarize-sessions.py")
summ = importlib.util.module_from_spec(sum_spec)
sum_spec.loader.exec_module(summ)


class ShmemTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        shmem.SM = Path(self.tmp.name)
        shmem.DB = shmem.SM / ".index" / "memory.db"
        self._cred = os.environ.get("SHMEM_SECRET_FILES", "")
        os.environ["SHMEM_SECRET_FILES"] = ""

    def tearDown(self):
        os.environ["SHMEM_SECRET_FILES"] = self._cred
        self.tmp.cleanup()

    def test_cjk_bigrams(self):
        self.assertEqual(shmem.tokenize("共享记忆"), ["共享", "享记", "记忆"])
        self.assertEqual(shmem.tokenize("hello 中"), ["hello", "中"])

    def test_secret_pattern_refused(self):
        self.assertTrue(shmem.secret_hit("note password: hunter2secretvalue here"))
        self.assertIsNone(shmem.secret_hit("plain fact, no secrets"))

    def test_secret_credential_value_refused(self):
        cred = shmem.SM / "cred"
        cred.write_text("SUPERSECRETVALUE\n", encoding="utf-8")
        os.environ["SHMEM_SECRET_FILES"] = str(cred)
        self.assertTrue(shmem.secret_hit("copied SUPERSECRETVALUE into memory"))

    def test_slug_rules(self):
        self.assertTrue(shmem.SLUG.match("fleet"))
        self.assertIsNone(shmem.SLUG.match("Bad/Topic"))
        self.assertIsNone(shmem.SLUG.match(""))

    def test_write_channels(self):
        con = shmem.open_db()
        ok, _ = shmem.channel_write(con, "memory_write",
                                    {"topic": "fleet", "content": "# Fleet\n\n- box B runs CRM.\n"})
        self.assertTrue(ok)
        self.assertEqual((shmem.SM / "facts" / "fleet.md").read_text(encoding="utf-8"),
                         "# Fleet\n\n- box B runs CRM.\n")
        ok, _ = shmem.channel_write(con, "memory_append", {"entry": "- appended"})
        self.assertTrue(ok)
        journal = (shmem.SM / "journal" / f"{shmem.now():%Y-%m}.md").read_text(encoding="utf-8")
        self.assertTrue(journal.startswith("\n## ") and "- appended" in journal)
        ok, _ = shmem.channel_write(con, "inbox_write", {"agent": "agent-b", "content": "line1\nline2"})
        self.assertTrue(ok)
        self.assertEqual((shmem.SM / "inbox" / "TO-agent-b.md").read_text(encoding="utf-8"),
                         "line1\nline2")

    def test_write_refusals(self):
        con = shmem.open_db()
        ok, _ = shmem.channel_write(con, "memory_write",
                                    {"topic": "x", "content": "password: hunter2secretvalue"})
        self.assertFalse(ok)
        self.assertFalse((shmem.SM / "facts" / "x.md").exists())
        ok, _ = shmem.channel_write(con, "inbox_write",
                                    {"agent": "a", "content": "\n".join(["x"] * 21)})
        self.assertFalse(ok)
        ok, _ = shmem.channel_write(con, "memory_write", {"topic": "Bad/Topic", "content": "x"})
        self.assertFalse(ok)

    def test_no_flock_platform_refuses(self):
        con = shmem.open_db()
        real = shmem.fcntl
        shmem.fcntl = None
        try:
            ok, _ = shmem.channel_write(con, "memory_write", {"topic": "fleet", "content": "x"})
            self.assertFalse(ok)
        finally:
            shmem.fcntl = real

    def test_read_only_face_is_default(self):
        self.assertEqual([t["name"] for t in shmem.mcp_tools()],
                         ["memory_search", "memory_stats"])
        self.assertEqual(sorted(t["name"] for t in shmem.mcp_tools(allow_write=True)),
                         ["inbox_write", "memory_append", "memory_search", "memory_stats", "memory_write"])

    def test_quiet_guard_skips_growing_sessions(self):
        now = time.time()
        self.assertTrue(summ.quiet_skip("%d:100" % int(now), now))
        self.assertFalse(summ.quiet_skip("%d:100" % (int(now) - 3600), now))
        self.assertFalse(summ.quiet_skip("42", now))  # non-mtime marker: not guarded

    def test_dedup_identical_block(self):
        journal = shmem.SM / "journal" / "2026-10.md"
        journal.parent.mkdir(parents=True, exist_ok=True)
        block = "### auto-summary · 2026-10-25 · dsh · t\n- fact one\n"
        journal.write_text("\n" + block, encoding="utf-8")
        seen = summ.seen_blocks(str(journal))
        self.assertIn(block, seen)
        self.assertNotIn("### auto-summary · 2026-10-25 · dsh · t\n- fact two\n", seen)


    def run_agents_block(self, journals):
        jr = shmem.SM / "journal"
        jr.mkdir(parents=True, exist_ok=True)
        for name, body in journals.items():
            (jr / name).write_text(body, encoding="utf-8")
        agents = shmem.SM / "AGENTS.md"
        agents.write_text("# hand-written instructions\n", encoding="utf-8")
        env = dict(os.environ, SHMEM_ROOT=str(shmem.SM), DSH_AGENTS_MD=str(agents),
                   HOME=str(shmem.SM), SHMEM_TZ="UTC")
        r = subprocess.run(["bash", str(ROOT / "integrations" / "dsh" / "update-agents-block.sh")],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return agents.read_text(encoding="utf-8")

    def test_agents_block_snapshot_falls_back_to_older_journal(self):
        # Fresh month without a snapshot must not blank the section: the previous
        # journal's real snapshot wins over the placeholder.
        out = self.run_agents_block({
            "2026-10.md": "\n## 2026-10-31 23:00 private memory snapshot (auto)\n```\nOLD MEMORY LINE\n```\n",
            "2026-11.md": "\n### auto-summary · 2026-11-01 · dsh · t\n- new fact\n",
        })
        self.assertIn("OLD MEMORY LINE", out)
        self.assertNotIn("no snapshot yet", out)
        self.assertIn("- new fact", out)          # recent activity still comes from the newest journal

    def test_agents_block_placeholder_when_no_snapshot_anywhere(self):
        out = self.run_agents_block({
            "2026-10.md": "\n### auto-summary · 2026-10-01 · dsh · t\n- older fact\n",
            "2026-11.md": "\n### auto-summary · 2026-11-01 · dsh · t\n- new fact\n",
        })
        self.assertIn("(no snapshot yet)", out)
        self.assertIn("- new fact", out)


if __name__ == "__main__":
    unittest.main()
