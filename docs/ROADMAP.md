# shmem improvement plan (draft, 2026-11-13)

Research basis: full read of `PROTOCOL.md`, all tools (`memory-search.py`,
`summarize-sessions.py`, `make-digest.sh`, integrations, units, Makefile, CI), the
test suite and eval, plus two reproduction tests against `examples/` and ~5 weeks of
production operation (a two-agent shared memory under a daily summarizer timer).

Every item below is either reproduced in code or observed in production. Items are
small on purpose: each one is a single reviewable commit.

## P0 — correctness (target: v0.3.1)

1. **Deleted files keep matching in search (confirmed).** `sync()` only re-indexes
   files that exist and changed; docs of removed/renamed files are never pruned, so
   results keep pointing at paths that no longer exist. Repro: index `examples/`,
   delete `journal/2026-01.md`, search still returns its chunks.
   *Fix:* in `sync()`, delete docs/fts/embed rows whose `path` is no longer in
   `md_files()`; add a regression test.
2. **Unclosed sqlite connections** on the CLI paths (`--stats` and friends leak
   `ResourceWarning: unclosed database`). *Fix:* context-manage the connection in
   `main()` and `mcp_loop()`; gate CI with `python -W error::ResourceWarning`.
3. **Concurrent indexing can raise `database is locked`.** Two agents searching at
   once both call `sync()` on the same fresh file with sqlite defaults.
   *Fix:* `PRAGMA busy_timeout=5000` + WAL in `open_db()`; index remains disposable,
   correctness unchanged.

## P1 — output quality (target: v0.3.2)

4. **Summarizer noise gate is too narrow.** The journal accumulated process chatter:
   "report delivered to the parent agent", raw `assistant:`/`user:` echoes, harness
   notices, shell-command echoes. `clean_facts()` filters only bullets/secrets/bad
   years; `NOISE_PREFIX` covers just the runtime-context block.
   *Fix:* drop transcript echoes (`^(user|assistant):`), process-chatter patterns
   (delivered/received/acknowledged reports, continuation notices), lines under ~6
   words with no entity; add fixture-based tests for the gate.
5. **Digest byte-truncation cuts mid-word/mid-CJK** (`head -c 9000`).
   *Fix:* truncate at line boundaries under the 9 KB budget.
6. **"Recent activity" in the digest scans every journal file** (up to 5 per file),
   so an old month can crowd out the newest entries.
   *Fix:* take the newest N across files by file order, newest file first.
7. **Version drift.** MCP `serverInfo` says `0.1.0` while releases are at `0.3.0`;
   README quotes an LOC figure that goes stale.
   *Fix:* single `__version__` constant; serverInfo and tests read it; drop the LOC
   claim from README (or compute in docs generation).

## P2 — protocol hardening (target: v0.4.0, "durable delivery")

8. ✅ **Inbox overwrite semantics lose messages (observed in production).** One file per
   recipient + overwrite mode means a second sender can clobber an unread message
   before the recipient ACKs it — this actually happened: an unread notice was
   replaced by a later one, and only the journal copy preserved the fact.
   *Fix (protocol-level, shipped in v0.4.0):* one file per message —
   `inbox/TO-<agent>/<YYYY-MM-DD>-<slug>.md`, each still ≤20 lines. ACK stays "move
   content to journal, delete the file". Back-compatible: legacy flat files are still
   read and cleared.
   *Plan correction:* the sketch said "refuses to overwrite **unless `overwrite: true`**".
   That flag was implemented and then removed before release — the filename is derived
   from the message's first line, so a corrected draft points at a *different* file and
   "overwrite" could never name the message you meant. Delivery is append-only; a
   correction is a new message.
9. ✅ **`make install-units` silently installs a dead timer when user lingering is off**
   (observed: user-scope units existed for weeks, never fired; the live job was
   system-scope). *Fix:* pre-check `loginctl show-user -p Linger` and
   `$XDG_RUNTIME_DIR` reachability with an actionable error, and add a
   `units/README.md` describing the system-scope install as the fallback.
10. ✅ **Undelivered-inbox visibility.** `--stats` additionally reports inbox files
    older than 3 days as `stale_delivery` candidates — an unread inbox is a delivery
    failure by protocol, so the tool should say so.

## P3 — engineering hygiene (continuous)

11. ✅ `CHANGELOG.md` (three released versions, no file), `SECURITY.md` (local-trust
    threat model, secret-guard scope and limits, MCP read-only default),
    `CONTRIBUTING.md` (definition of done: `make test` + `eval/run.sh` + CI; protocol
    changes update `PROTOCOL.md` + implementation + tests + both READMEs in one
    commit).
12. ✅ CI: Python matrix 3.9 / 3.11 / 3.13 (declare the minimum in README), `shellcheck`
    for every `*.sh`, `python -W error::ResourceWarning` gate (after P0-2).
13. ✅ Tests today cover protocol rules well (13 tests) but not retrieval regression.
    Add: stale-docs prune (P0-1), noise gate fixtures (P1-4), digest budget boundary,
    tokenizer edge cases (single CJK char, mixed scripts).
    Shipped: `test_sync_prunes_deleted_files`, `test_clean_facts_drops_process_chatter`,
    `test_heuristic_gates_and_normalizes_fallback_lines`, both `test_make_digest_*`
    budget tests, `test_cjk_bigrams` (single CJK char + mixed scripts). 26 tests total.

## P4 — packaging (target: v0.5.0)

14. ✅ `pyproject.toml`, zero runtime dependencies, console script `shmem = tools.memory_search:main`
    (module move behind a compat shim), `importlib.metadata` version. Keep the
    git-clone-and-run workflow first-class — pip install is an extra door, not the
    front door. Check the `shmem` name availability on PyPI before cutting the tag.
    Shipped in v0.5.0. Two notes against the sketch: the package is `shmem`, not
    `tools.*` (a top-level `tools` package would collide with anything else in
    `site-packages`), so the entry point is `shmem.cli:main` and the old paths survive
    as compat shims — `tools/` stays the documented front door. `shmem` was free on
    PyPI (HTTP 404, checked 2026-11-17); **publishing stays a human decision** — the
    repo only proves the wheel builds and the console script runs.

## Parking lot (with the trigger that promotes it)

- **Topics layering** (`facts/<area>/<topic>.md`): promote when a real memory tree
  exceeds ~30 facts files or INDEX.md routing becomes ambiguous.
- **Vector layer scaling**: brute-force cosine in Python is fine to ~10k chunks;
  revisit (quantized blobs, two-pass filter) only when `embed` rows exceed that.
- **Windows**: stays explicitly out of scope — protocol is platform-neutral, the
  reference implementation needs `flock`; do not add a second locking model.

## Suggested sequence

`v0.3.1` = P0(1–3) → `v0.3.2` = P1(4–7) → `v0.3.x` hygiene → `v0.4.0` = P2(8–10,
PROTOCOL.md updates) → `v0.5.0` = P4 + P3 remainder. Each release: CI green, eval
recall stays 23/23, new tests accompany every fix.

**All four priority bands are closed.** What remains is a decision, not a roadmap:
publish on PyPI or stay clone-only. The protocol is identical either way; publishing
trades a versioned artifact for a name to defend.
