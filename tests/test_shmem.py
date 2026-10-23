#!/usr/bin/env python3
"""Unit tests for the reference implementation (stdlib only; this is the CI gate).

Run: python3 tests/test_shmem.py   (or: make test)
"""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("shmem", ROOT / "tools" / "memory-search.py")
shmem = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shmem)


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


if __name__ == "__main__":
    unittest.main()
