#!/usr/bin/env python3
"""Unit tests for the reference implementation (stdlib only; this is the CI gate).

Run: python3 tests/test_shmem.py   (or: make test)
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # test the source in this checkout, not an installed copy

import shmem  # noqa: E402
import shmem.search as shmem_mod  # noqa: E402
import shmem.summarize as summ  # noqa: E402

# The tests were written against the module the shim exposes; SM/DB are patched per test.
shmem = shmem_mod


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
        self.addCleanup(con.close)
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
        box = shmem.SM / "inbox" / "TO-agent-b"
        msgs = sorted(box.glob("*.md"))
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].read_text(encoding="utf-8"), "line1\nline2")
        self.assertIn(f"{shmem.now():%Y-%m-%d}-line1.md", msgs[0].name)

    def test_write_refusals(self):
        con = shmem.open_db()
        self.addCleanup(con.close)
        ok, _ = shmem.channel_write(con, "memory_write",
                                    {"topic": "x", "content": "password: hunter2secretvalue"})
        self.assertFalse(ok)
        self.assertFalse((shmem.SM / "facts" / "x.md").exists())
        ok, _ = shmem.channel_write(con, "inbox_write",
                                    {"agent": "a", "content": "\n".join(["x"] * 21)})
        self.assertFalse(ok)
        ok, _ = shmem.channel_write(con, "memory_write", {"topic": "Bad/Topic", "content": "x"})
        self.assertFalse(ok)

    def test_inbox_never_clobbers_unread(self):
        # One file per recipient is last-writer-wins: a second delivery used to erase
        # an unread message. The inbox is a queue of files, not a scratchpad.
        con = shmem.open_db()
        self.addCleanup(con.close)
        for text in ("first conclusion", "second conclusion"):
            ok, msg = shmem.channel_write(con, "inbox_write",
                                          {"agent": "agent-b", "content": f"{text}\n- pointer"})
            self.assertTrue(ok, msg)
        box = shmem.SM / "inbox" / "TO-agent-b"
        bodies = sorted(p.read_text(encoding="utf-8") for p in box.glob("*.md"))
        self.assertEqual(bodies, ["first conclusion\n- pointer", "second conclusion\n- pointer"])
        # an identical resend on the same day still gets its own file
        ok, _ = shmem.channel_write(con, "inbox_write", {"agent": "agent-b", "content": "first conclusion\n- pointer"})
        self.assertTrue(ok)
        self.assertEqual(len(list(box.glob("*.md"))), 3)
        # a corrected message is a new message: delivery is never rewritten in place
        ok, msg = shmem.channel_write(con, "inbox_write",
                                      {"agent": "agent-b", "content": "correction: edited draft"})
        self.assertTrue(ok, msg)
        base = box / f"{shmem.now():%Y-%m-%d}-first-conclusion.md"
        self.assertEqual(base.read_text(encoding="utf-8"), "first conclusion\n- pointer")
        self.assertEqual(len(list(box.glob("*.md"))), 4)

    def test_stats_flags_stale_inbox(self):
        # Protocol: an unread inbox file is a delivery failure. --stats makes it visible.
        con = shmem.open_db()
        self.addCleanup(con.close)
        ok, _ = shmem.channel_write(con, "inbox_write", {"agent": "agent-b", "content": "old news\n- pointer"})
        self.assertTrue(ok)
        msg = next((shmem.SM / "inbox" / "TO-agent-b").glob("*.md"))
        back = time.time() - 5 * 86400
        os.utime(msg, (back, back))
        st = shmem.stats(con)
        self.assertEqual([e["path"] for e in st["stale_inbox"]], [str(msg.relative_to(shmem.SM))])
        self.assertGreater(st["stale_inbox"][0]["age_days"], 3)
        ok, _ = shmem.channel_write(con, "inbox_write", {"agent": "agent-b", "content": "fresh news\n- pointer"})
        self.assertTrue(ok)
        self.assertEqual(len(shmem.stats(con)["stale_inbox"]), 1)

    def test_no_flock_platform_refuses(self):
        con = shmem.open_db()
        self.addCleanup(con.close)
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

    def test_seen_blocks_covers_other_months(self):
        # Markers churn (mtime), so a session summarized in October is picked up again
        # in November; dedup must see the OLD month's file or the block is re-appended.
        jr = shmem.SM / "journal"
        jr.mkdir(parents=True, exist_ok=True)
        block = "### auto-summary · 2026-10-20 · dsh · t\n- fact one\n"
        (jr / "2026-10.md").write_text("\n" + block, encoding="utf-8")
        (jr / "2026-11.md").write_text("\n## manual entry\n", encoding="utf-8")
        seen = summ.seen_blocks(str(jr / "2026-11.md"))
        self.assertIn(block, seen)

    def test_fallback_output_is_marked_low_quality(self):
        old = (summ.ROOT, summ.STATE, summ.LOG, summ.ENDPOINT, summ.MODEL, summ.SOURCES, summ.ask_model)
        try:
            jr = shmem.SM / "journal"
            jr.mkdir(parents=True, exist_ok=True)
            transcript = "\n".join("决定 部署到 /srv/app 端口 8080 commit abc%03d" % i for i in range(30))
            summ.ROOT = str(shmem.SM)
            summ.STATE = str(shmem.SM / ".state.json")
            summ.LOG = str(shmem.SM / ".log")
            summ.ENDPOINT, summ.MODEL = "http://x.invalid/v1/chat/completions", "m"
            summ.SOURCES = (("dsh", lambda: iter([("kk", "m1", "title", transcript, "2026-11-20")])),)
            def boom(*a, **k):
                raise RuntimeError("down")
            summ.ask_model = boom
            summ.main([])
        except SystemExit:
            pass
        finally:
            (summ.ROOT, summ.STATE, summ.LOG, summ.ENDPOINT, summ.MODEL, summ.SOURCES, summ.ask_model) = old
        text = (jr / ("%s.md" % __import__("datetime").datetime.now(summ.TZ).strftime("%Y-%m"))).read_text(encoding="utf-8")
        self.assertIn("### auto-summary (LOW-QUALITY fallback) · 2026-11-20 · dsh · title", text)


    def run_agents_block(self, journals):
        jr = shmem.SM / "journal"
        jr.mkdir(parents=True, exist_ok=True)
        for name, body in journals.items():
            (jr / name).write_text(body, encoding="utf-8")
        agents = shmem.SM / "AGENTS.md"
        agents.write_text("# hand-written instructions\n", encoding="utf-8")
        env = dict(os.environ, SHMEM_ROOT=str(shmem.SM), DSH_AGENTS_MD=str(agents),
                   HOME=str(shmem.SM), SHMEM_TZ="UTC", SHMEM_HEALTH_PROBE="0")
        r = subprocess.run(["bash", str(ROOT / "integrations" / "dsh" / "update-agents-block.sh")],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return agents.read_text(encoding="utf-8")

    def test_workspace_mirror_maintained_and_sentinel(self):
        self.run_agents_block({"2026-11.md": "\n### auto-summary · 2026-11-01 · dsh · t\n- new fact\n"})
        mirror = shmem.SM / "WS-AGENTS.md"
        mirror.write_text("# hand notes\n\n<!-- BEGIN SHMEM MIRROR (auto-generated by update-agents-block.sh) -->\nplaceholder\n<!-- END SHMEM MIRROR (auto-generated by update-agents-block.sh) -->\n",
                          encoding="utf-8")
        env = dict(os.environ, SHMEM_ROOT=str(shmem.SM), DSH_AGENTS_MD=str(shmem.SM / "AGENTS.md"),
                   HOME=str(shmem.SM), SHMEM_TZ="UTC", SHMEM_HEALTH_PROBE="0", SHMEM_MIRROR_MD=str(mirror))
        r = subprocess.run(["bash", str(ROOT / "integrations" / "dsh" / "update-agents-block.sh")],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = mirror.read_text(encoding="utf-8")
        self.assertNotIn("placeholder", out)  # generated region replaced itself
        self.assertIn("# hand notes", out)    # hand-written text outside markers untouched
        self.assertIn("memory-search.py", out)
        # prose merely MENTIONS the sentinel; only a whole-line sentinel disables
        self.run_agents_block({"2026-11.md": "\n### auto-summary · 2026-11-02 · dsh · t2\n- newer fact\n"})
        mirror.write_text("# hand notes\nshmem-mirror: disabled\n", encoding="utf-8")
        r = subprocess.run(["bash", str(ROOT / "integrations" / "dsh" / "update-agents-block.sh")],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(mirror.read_text(encoding="utf-8"), "# hand notes\nshmem-mirror: disabled\n")

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

    def test_agents_block_snapshot_keeps_entries_beyond_the_second(self):
        # Stale facts hide below the first two entries; the injected slice must keep
        # every entry's opening (clip per line, drop the "§" separators) so a rewrite
        # of entry 3 or 4 is visible in AGENTS.md instead of being silently truncated.
        out = self.run_agents_block({
            "2026-11.md": "\n## 2026-11-08 09:58 private memory snapshot (auto)\n```\n"
                         "[MEMORY.md]\nTOKEN ONE\n§\nTOKEN TWO\n§\nTOKEN THREE stale model\n"
                         "[USER.md]\nTOKEN USER\n```\n",
        })
        self.assertIn("TOKEN ONE", out)
        self.assertIn("TOKEN THREE stale model", out)
        self.assertIn("TOKEN USER", out)

    def test_sync_prunes_deleted_files(self):
        # A deleted or renamed journal must not keep matching: ghost results pointing
        # at vanished paths are worse than a cache miss.
        jr = shmem.SM / "journal"
        jr.mkdir(parents=True)
        gone = jr / "2026-01.md"
        gone.write_text("# Journal\n\nzebra-crm was decided on the whiteboard.\n", encoding="utf-8")
        con = shmem.open_db()
        self.addCleanup(con.close)
        self.assertTrue([r for r in shmem.search("zebra-crm", con=con) if "2026-01" in r["path"]])
        gone.unlink()
        shmem.sync(con)
        self.assertEqual([r for r in shmem.search("zebra-crm", con=con) if "2026-01" in r["path"]], [])

    def test_db_concurrency_pragmas(self):
        con = shmem.open_db()
        self.addCleanup(con.close)
        self.assertEqual(con.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertGreaterEqual(con.execute("PRAGMA busy_timeout").fetchone()[0], 5000)

    def test_clean_facts_drops_process_chatter(self):
        raw = "\n".join([
            "- CRM gate layer shipped in 7956fb9, deployed and verified.",
            "- Report delivered to the parent agent. Full findings:",
            "- user: \u7ee7\u7eed\u3002\u5bbf\u4e3b\u5728\u4f60\u4e0a\u4e00\u8f6e\u8fd0\u884c\u671f\u95f4\u91cd\u542f\u4e86",
            "- assistant: \u6536\u5230\u3002\u5f00\u59cb\u524d\u5148\u628a\u5f71\u54cd\u9762\u786e\u8ba4\u6389",
            '- echo "=== teleport loop since restart ==="',
            "- [model changed: assistant turns above this point were generated by a/b; the session continues with c/d]",
            "- Interim findings (research 90% done; full structured report follows):",
            "- \u7ee7\u7eed",
        ])
        out = summ.clean_facts(raw, "2026-11-16")
        self.assertEqual(out.splitlines(), ["- CRM gate layer shipped in 7956fb9, deployed and verified."])

    def test_heuristic_gates_and_normalizes_fallback_lines(self):
        transcript = "\n".join([
            "assistant: \u6536\u5230\u3002\u5f00\u59cb\u90e8\u7f72 CRM \u5230 pve01 VM101\uff0c\u5df2\u90e8\u7f72\u9a8c\u8bc1\u3002",
            "\u6539\u7528 pve01 VM101 \u627f\u8f7d CRM\uff0c2026-10-24 \u5df2\u90e8\u7f72\uff0c\u65e7 VM \u9000\u5f79\u3002",
            "\u7aef\u53e3 8085",
            "Report delivered to the parent agent; CRM deployed and verified.",
        ])
        out = summ.heuristic(transcript)
        self.assertEqual(out, "- \u6539\u7528 pve01 VM101 \u627f\u8f7d CRM\uff0c2026-10-24 \u5df2\u90e8\u7f72\uff0c\u65e7 VM \u9000\u5f79\u3002")

    def test_split_topic_extracts_short_heading(self):
        raw = "TOPIC: CRM \u90e8\u7f72\u5230 pve01\n- CRM \u8dd1\u5728 pve01 VM101\uff0c:8443\u3002"
        topic, rest = summ.split_topic(raw)
        self.assertEqual(topic, "CRM \u90e8\u7f72\u5230 pve01")
        self.assertEqual(rest, "- CRM \u8dd1\u5728 pve01 VM101\uff0c:8443\u3002")
        # tolerant: leading blank line, fullwidth colon
        topic, rest = summ.split_topic("\nTOPIC\uff1a\u4e2d\u6587\u6807\u9898\n- x")
        self.assertEqual(topic, "\u4e2d\u6587\u6807\u9898")
        self.assertEqual(rest, "- x")
        # no topic line at all → old behavior preserved
        topic, rest = summ.split_topic("- only facts\n- here")
        self.assertIsNone(topic)
        self.assertEqual(rest, "- only facts\n- here")
        # empty topic after the colon must not become a blank heading
        topic, _ = summ.split_topic("TOPIC:\n- x")
        self.assertIsNone(topic)

    def test_clean_facts_drops_process_chatter_via_model_path(self):
        raw = "\n".join([
            "TOPIC: CRM request engine task review",
            "- The working directory changed from \"/root\" to \"/root/crm/.agents/worktrees/request-engine\".",
            "- The reviewed branch is request-engine with single commit 905d141.",
        ])
        out = summ.clean_facts(raw, "2026-11-19")
        self.assertEqual(out, "- The reviewed branch is request-engine with single commit 905d141.")

    def test_clean_title_collapses_and_caps(self):
        # A whole task prompt (with newlines) must become one short line, never a
        # multi-line heading — this was polluting every injected digest.
        t = summ.clean_title("\u4f60\u662f Phase 2\u300c\u53d6\u6570\u7ba1\u9053\u300d\u7684\u5b9e\u73b0\u8005\uff08\u53ea\u505a\u5e93\uff09\u3002\n\n\u5de5\u4f5c\u76ee\u5f55 /root/bookkeeping\u3002" + "\u5570" * 200)
        self.assertNotIn("\n", t)
        self.assertLessEqual(len(t), 100)
        self.assertTrue(t.endswith("\u2026"))
        self.assertEqual(summ.clean_title("  \u7d27\u51d1 \u6807\u9898 "), "\u7d27\u51d1 \u6807\u9898")

    def test_console_cli_entry_points(self):
        # The installed door: `shmem <subcommand>` must reach the same code and the same SHMEM_ROOT.
        env = dict(os.environ, SHMEM_ROOT=str(ROOT / "examples"))
        r = subprocess.run([sys.executable, "-m", "shmem.cli", "search", "dashboard", "-n", "2"],
                           cwd=str(ROOT), env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dashboard", r.stdout.lower())
        r = subprocess.run([sys.executable, "-m", "shmem.cli", "stats"],
                           cwd=str(ROOT), env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("stale_inbox", json.dumps(json.loads(r.stdout)))
        r = subprocess.run([sys.executable, "-m", "shmem.cli", "version"],
                           cwd=str(ROOT), env=env, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), shmem.__version__)
        r = subprocess.run([sys.executable, "-m", "shmem.cli", "nope"],
                           cwd=str(ROOT), env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown subcommand", r.stderr)

    def test_model_failure_reason_is_actionable(self):
        # Silent keyword fallback is how a misconfigured endpoint degraded memory
        # for weeks. The log has to name the failure well enough to act on.
        exc = urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)
        self.assertEqual(summ.fail_reason(exc), "HTTPError 404")
        self.assertEqual(summ.fail_reason(urllib.error.URLError("nope")), "URLError nope")
        self.assertEqual(summ.fail_reason(TimeoutError()), "TimeoutError")
        # a base URL is the actual bug we hit; the tool must say so instead of guessing
        self.assertIsNotNone(summ.endpoint_warning("http://h:8080/v1"))
        self.assertIsNone(summ.endpoint_warning("http://h:8080/v1/chat/completions"))
        self.assertIsNone(summ.endpoint_warning(""))

    def test_summarizer_refuses_unknown_args(self):
        # It is a write tool driven entirely by the environment. A stray flag must
        # not run a summarization pass against a real memory tree.
        with tempfile.TemporaryDirectory() as tree:
            env = dict(os.environ, SHMEM_ROOT=tree)
            for flag in ("--help", "--dry-run"):
                r = subprocess.run([sys.executable, str(ROOT / "tools" / "summarize-sessions.py"), flag],
                                   env=env, capture_output=True, text=True, timeout=60)
                if flag == "--help":
                    self.assertEqual(r.returncode, 0, r.stderr)
                else:
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertFalse((Path(tree) / "journal").exists())

    def test_compat_shims_still_run_the_package(self):
        # PROTOCOL.md, integrations and existing memory trees address tools/*.py.
        # They are shims now, and the door must stay open.
        env = dict(os.environ, SHMEM_ROOT=str(ROOT / "examples"))
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "memory-search.py"), "dashboard", "-n", "1"],
                           cwd=str(ROOT), env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("journal/2026-01.md", r.stdout)

    def test_packaging_keeps_zero_dependencies_and_one_version(self):
        # The repo's invariant: standard library only. pyproject must not grow deps,
        # and the version must stay single-sourced from shmem.__version__.
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        project = text.split("[project]", 1)[1].split("[tool.", 1)[0]
        # match keys, not prose: the description and comments talk about dependencies
        self.assertIsNone(re.search(r"^\s*dependencies\s*=", project, re.M))   # zero runtime deps
        self.assertIsNone(re.search(r"^\s*version\s*=", project, re.M))        # dynamic, not duplicated
        self.assertIn('shmem = "shmem.cli:main"', text)
        self.assertIn('version = { attr = "shmem.__version__" }', text)
        self.assertEqual(shmem.__version__, shmem_mod.__version__)

    def test_mcp_server_version_tracks_module(self):
        msg = '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
        env = dict(os.environ, SHMEM_ROOT=str(shmem.SM))
        r = subprocess.run(["python3", str(ROOT / "tools" / "memory-search.py"), "--mcp"],
                           input=msg, env=env, capture_output=True, text=True, timeout=15)
        reply = json.loads(r.stdout.splitlines()[0])
        self.assertEqual(reply["result"]["serverInfo"]["version"], shmem.__version__)

    def test_make_digest_caps_and_prefers_newest(self):
        jr = shmem.SM / "journal"
        jr.mkdir(parents=True)
        (shmem.SM / "INDEX.md").write_text("# Index\n\nentry page\n", encoding="utf-8")
        old = "\n".join("### auto-summary \u00b7 2026-10-%02d \u00b7 dsh \u00b7 t%d\n- fact O%d\n" % (i + 1, i, i)
                         for i in range(1, 7))
        (jr / "2026-10.md").write_text(
            "# Journal \u2014 2026\n" + old +
            "\n## 2026-10-30 08:00 private memory snapshot (auto)\nOLD-SNAP-MARKER\n", encoding="utf-8")
        (jr / "2026-11.md").write_text(
            "# Journal \u2014 2026-11\n"
            "### auto-summary \u00b7 2026-11-01 \u00b7 dsh \u00b7 n1\n- fact N1\n"
            "### auto-summary \u00b7 2026-11-02 \u00b7 dsh \u00b7 n2\n- fact N2\n"
            "\n## 2026-11-08 09:58 private memory snapshot (auto)\nNEW-SNAP-MARKER\n"
            "\n## 2026-11-09 10:00 later section\nnot a snapshot\n", encoding="utf-8")
        env = dict(os.environ, SHMEM_ROOT=str(shmem.SM), SHMEM_TZ="UTC")
        r = subprocess.run(["bash", str(ROOT / "tools" / "make-digest.sh")],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        digest = (shmem.SM / "DIGEST.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(digest.encode("utf-8")), 9000)
        self.assertTrue(digest.endswith("\n"))
        self.assertEqual(digest.count("### auto-summary"), 5)   # 2 newest + 3 most recent old
        self.assertIn("fact N1", digest)
        self.assertIn("fact O6", digest)
        self.assertNotIn("fact O1", digest)
        self.assertEqual(digest.count("private memory snapshot"), 1)
        self.assertIn("NEW-SNAP-MARKER", digest)
        self.assertNotIn("OLD-SNAP-MARKER", digest)

    def test_make_digest_budget_survives_giant_index(self):
        # A real INDEX.md can alone exceed the 9KB budget; activity and the latest
        # snapshot must still survive the cap (reserved tail budgets, old code cut
        # them silently).
        jr = shmem.SM / "journal"
        jr.mkdir(parents=True)
        filler = "\n".join("filler line %03d \u8fd9\u662f\u4e00\u6bb5\u8f83\u957f\u7684\u4e2d\u6587\u8bf4\u660e\u7528\u6765\u5403\u6389\u9884\u7b97 abcdefghijklmnop" % i
                           for i in range(200))
        (shmem.SM / "INDEX.md").write_text("# Index\n\n" + filler + "\n", encoding="utf-8")
        (jr / "2026-11.md").write_text(
            "# Journal\n### auto-summary \u00b7 2026-11-02 \u00b7 dsh \u00b7 n1\n- fact N1\n"
            "\n## 2026-11-08 09:58 private memory snapshot (auto)\nNEW-SNAP-MARKER\n", encoding="utf-8")
        env = dict(os.environ, SHMEM_ROOT=str(shmem.SM), SHMEM_TZ="UTC")
        r = subprocess.run(["bash", str(ROOT / "tools" / "make-digest.sh")],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        digest = (shmem.SM / "DIGEST.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(digest.encode("utf-8")), 9000)
        self.assertIn("fact N1", digest)
        self.assertIn("NEW-SNAP-MARKER", digest)
        self.assertTrue(digest.endswith("\n"))

    def test_search_caps_chunks_per_file(self):
        # A long journal that mentions the term forty times must not crowd the authoritative
        # facts chunks out of the window: one file takes at most 2 slots, the rest goes to
        # other files. Before the cap, the journal's repeated mentions owned the whole top
        # window (equal/near-equal BM25 scores ordered by fts rowid = index insertion order).
        jr = shmem.SM / "journal"
        jr.mkdir(parents=True)
        (shmem.SM / "facts").mkdir()
        for n in "abcdef":
            (shmem.SM / "facts" / f"{n}.md").write_text(
                f"# {n}\n\n## S\nauthoritative decision about zebra widget in file {n}\n", encoding="utf-8")
        (jr / "2026-11.md").write_text(
            "# Journal\n" + "\n".join("line %d mentions zebra widget repeatedly here\n" % i for i in range(40)),
            encoding="utf-8")
        hits = shmem.search("zebra widget", limit=6)
        self.assertLessEqual(len([h for h in hits if "2026-11.md" in h["path"]]), 2)
        distinct_facts = {h["path"] for h in hits if "/facts/" in h["path"]}
        self.assertGreaterEqual(len(distinct_facts), 4)

    def test_digest_md_not_indexed(self):
        # DIGEST.md is a derived injection copy; indexing it only returns duplicate pointers.
        (shmem.SM / "facts").mkdir()
        (shmem.SM / "facts" / "x.md").write_text("# X\n\nreal zebra-q3 decision\n", encoding="utf-8")
        (shmem.SM / "DIGEST.md").write_text("# Digest\n\nzebra-q3 digest reprint\n", encoding="utf-8")
        con = shmem.open_db()
        self.addCleanup(con.close)
        shmem.sync(con)
        paths = [r[0] for r in con.execute("SELECT path FROM files").fetchall()]
        self.assertFalse(any("DIGEST.md" in p for p in paths))
        self.assertFalse([h for h in shmem.search("zebra-q3", con=con) if "DIGEST" in h["path"]])

    def test_search_result_is_deterministic(self):
        # Ties broken by rowid drift when a journal is re-indexed (rowids change), so the
        # same query must return the same window across a rebuild, not flip between runs.
        jr = shmem.SM / "journal"
        jr.mkdir(parents=True)
        f = jr / "2026-11.md"
        f.write_text("# Journal\n\nalpha zebra note one\n\nalpha zebra note two\n\nalpha zebra note three\n", encoding="utf-8")
        first = [(h["path"], h["lines"]) for h in shmem.search("zebra", limit=3)]
        con = shmem.open_db()
        shmem.rebuild(con)          # rowids reassigned
        con.close()
        second = [(h["path"], h["lines"]) for h in shmem.search("zebra", limit=3)]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
