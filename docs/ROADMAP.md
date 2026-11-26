# shmem improvement plan (draft 2026-11-13, updated 2026-11-26)

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

- ~~**Topics layering** (`facts/<area>/<topic>.md`): promote when a real memory tree
  exceeds ~30 facts files or INDEX.md routing becomes ambiguous.~~ **Trigger fired
  2026-11-26** (fleet.md 100 KB, routing ambiguous) → promoted to item 16.
- **Vector layer scaling**: brute-force cosine in Python is fine to ~10k chunks;
  revisit (quantized blobs, two-pass filter) only when `embed` rows exceed that.
- **Windows**: stays explicitly out of scope — protocol is platform-neutral, the
  reference implementation needs `flock`; do not add a second locking model.

## Suggested sequence

`v0.3.1` = P0(1–3) → `v0.3.2` = P1(4–7) → `v0.3.x` hygiene → `v0.4.0` = P2(8–10,
PROTOCOL.md updates) → `v0.5.0` = P4 + P3 remainder. Each release: CI green, eval
recall stays green (25/25 on examples, 21/21 prod), new tests accompany every fix.

**All four priority bands are closed.** What remains is a decision, not a roadmap:
publish on PyPI or stay clone-only. The protocol is identical either way; publishing
trades a versioned artifact for a name to defend.

## v0.6.0 shipped (2026-11-26)

`shmem health` + health line as DIGEST line 2 and in the AGENTS.md mirror;
production eval corpus (`eval/queries.shmem.tsv`, 21 ground-truth queries);
summarizer `TOPIC:` titling + widened noise gates; digest facts-map tail fix;
eval probe-hygiene guard. The production tree split `fleet.md` (100 KB) into
`facts/machines/` — one file per machine — with zero-loss verification.

## Next band — v0.7.x candidates (draft 2026-11-26; every item reproduced in production)

15. **DONE v0.7.0 — diagnosis corrected.** The summarizer burned model calls on
    *active* sessions: every backfill pass reprocessed 9–10 (processed=8..11),
    because the hermes adapter's marker was a bare message-id counter, so the
    quiet guard (`SHMEM_QUIET_SECONDS`, already present) never applied to it —
    not a missing knob. *Fix shipped:* every adapter marker must lead with the
    last-activity epoch; `marker_equivalent` migrates the 75 legacy bare-id
    states without re-spending a call; `units/README.md` documents why
    "run until processed=0" cannot converge on a live host.
16. **DONE v0.7.0 — `facts/<area>/<entry>.md` is protocol.** PROTOCOL.md states
    the split rule (index file keeps routing, INDEX.md must name the area);
    `examples/` now mirrors the production shape (fleet.md=index, machines/=
    per-host files) so the CI eval itself tests area routing — recall measured
    on it, 25/25; make-digest maps any facts/ subdir generically (the old code
    hardcoded the one production area and its Chinese note).
17. **DONE v0.7.0 — cuts are visible.** Every byte-budget cut in make-digest
    prints a marker (`[…truncated — see INDEX.md]`, journal pointers for the
    tail sections); three tests pin marker-on-cut, marker-on-snapshot-cut, and
    no false marker when nothing was cut.
18. **Measured 2026-11-26: no-vector baseline 21/21 on the real tree; the
    with-vector number is BLOCKED on infrastructure.** The claim above that an
    "embedding endpoint is live" was wrong — probed: `192.168.1.45:8080` serves
    chat only (`/v1/embeddings` → not found), no ollama anywhere on the LAN.
    The A/B is one command when an endpoint exists:
    `SHMEM_EMBED_BASE_URL=… SHMEM_EMBED_MODEL=… QUERIES=eval/queries.shmem.tsv
    ROOT=/root/shared-memory bash eval/run.sh`. **Vector default stays OFF** —
    no measured gain exists yet, and the external survey argues for measuring,
    not growing, the vector layer.
19. **Git backstore for the memory tree (decision, not a feature).** The
    authoritative markdown has no history — today's fleet split needed a
    hand-written zero-loss verification script because there was no
    `git diff` to answer "did we lose a line?". `git init` + auto-commit after
    each timer run + the existing secret guard would give blame/rollback free.
    Protocol must first rule: history is *derived* like the index (disposable,
    never the entry point; secrets must never enter history — guard becomes a
    pre-commit check). Parking-lot rule says promote at the first unrecoverable
    manual edit; consider deciding early, the split was one edit away from
    needing it.

## External context (surveyed 2026-11-26)

The 2026 agent-memory discourse backs the protocol bet: memory is an
architecture *above* retrieval, and a vector database is one component, not
the system ([atlan](https://atlan.com/know/agentic-ai-memory-vs-vector-database/)).
Mem0's extract-and-consolidate layer is what the summarizer already is, Letta's
self-editing memory blocks are what the facts/ channel already is, and Zep's
temporal knowledge graph is the one shape with no flat-file equivalent —
exactly the complexity this protocol refuses
([agentscamp](https://agentscamp.com/guides/comparisons/mem0-vs-zep-vs-letta),
[dataaspirant](https://dataaspirant.com/blog/mem0-vs-letta-vs-zep/)). shmem's
differentiators in that market: zero runtime dependencies, human-auditable
markdown as the authority, and protocol-level multi-agent sharing. The
BM25+dense+RRF recipe already shipped matches current hybrid-search reference
practice ([digitalapplied](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026))
— which argues for *measuring* item 18, not growing the vector layer.

## Updated sequence

v0.7.0 shipped = items 15–17 (one commit each) + the item-18 measurement
(baseline 21/21 recorded; the vector arm awaits an embeddings endpoint —
default stays OFF). Item 19 remains a human decision with a protocol revision.
The PyPI publish decision stays open either way.
