# Changelog

Notable changes per release. shmem adheres to semantic versioning; the protocol
itself evolves only through explicit `PROTOCOL.md` revisions.

## [0.8.0] - 2026-11-26

### Fixed
- **The vector build no longer starves search** (`shmem/search.py`).
  Production incident 2026-11-26: `index_file` made the per-chunk HTTP embed
  call INSIDE the open sqlite write transaction, so a `--full --vector` build
  of the real tree (1032 chunks, ~75 min on a CPU-only embed host) held the
  writer lock across ~1000 network round-trips — concurrent searches were
  starved (prod eval measured 1/21 mid-build). Embedding now happens before
  any db write for that file, and `sync()`/`rebuild()` commit per file
  (rebuild also publishes the wipe before the slow part).
- **Summarizer state commits per session** (`shmem/summarize.py`, ROADMAP
  item 20). A timeout-killed run had appended 12 journal blocks but persisted
  no markers (state was saved once at run end); the next pass re-summarized
  everything and model non-determinism made the near-duplicates miss the
  exact-text dedup — 8 duplicate blocks removed by hand. Every marker advance
  now goes through `mark_done()`, saving immediately; the journal block is
  written BEFORE the marker, so the worst remaining race is one session.
  `save_state()` became an atomic tmp+rename — truncated json from a
  mid-write kill used to load as `{}` and re-summarize the world, exactly
  the disaster the per-session save exists to prevent. Test pins the resume:
  a run killed between sessions must leave the finished session's block AND
  marker on disk, and the resume may not ask the model about it again.

### Changed
- `eval/run.sh` gains `SHMEM_VECTOR=1` to pass `--vector` at query time —
  the A/B command recorded in docs/ROADMAP.md could not actually enable
  fusion without it (fusion is a query-time flag, not an index-time one).

### Measured
- Vector layer A/B closed (ROADMAP item 18): an embeddings endpoint DOES
  exist — `192.168.1.21:8085`, llama.cpp Qwen3-Embedding-4B-Q8_0, live since
  2026-11-06 and documented in the fleet's own facts; the earlier claim here
  that "no embeddings endpoint exists on this network" was a bad probe, and
  the lesson is recorded: check the fleet facts before raw port-scanning.
  Full prod A/B (1032 chunks embedded): recall 21/21 in BOTH arms — BM25
  already saturates the real query set — ranking improves (top-1 13/21 →
  14/21, mean rank 1.75 → 1.45), but the negative probe regresses under
  fusion (vector_recall has no similarity floor). No recall gain to flip on:
  **vector default stays OFF**, now backed by measurement. If ever enabled:
  add a cosine floor to `vector_recall` and feed the embed env vars into the
  daily digest re-index (a vector-less sync silently drops embeds).

## [0.7.0] - 2026-11-26

### Fixed
- **Quiet guard now covers every adapter** (`shmem/summarize.py`). Production
  evidence: each backfill pass re-summarized 8–11 sessions — the hermes
  adapter's state marker was a bare message-id counter, so `SHMEM_QUIET_SECONDS`
  silently never applied to it (only `mtime`-prefixed markers are guarded).
  The adapter contract now requires each marker to lead with the last-activity
  epoch, `marker_equivalent()` migrates the 75 existing bare-id production
  states without spending a second model call, and `units/README.md` explains
  why "run until processed=0" cannot converge on a live host.

### Changed — protocol
- **`facts/<area>/<entry>.md` splits are formal protocol** (`PROTOCOL.md`).
  The pattern that fixed the 100 KB fleet.md incident is now written down: one
  entry per file when a topic outgrows one file, the index file keeps routing,
  and INDEX.md must name the area. The `examples/` tree mirrors the production
  shape (fleet.md = index table, `machines/<host>.md` = fact files), so the CI
  eval gates area routing itself: recall 25/25 (two new routing queries).
- **Digest truncation is visible** (`tools/make-digest.sh`). Every byte-budget
  cut now prints a marker line (`[…truncated — see INDEX.md]` for the head,
  journal pointers for activity/snapshots) instead of silently producing a
  digest that reads complete. `make-digest` also maps *any* facts/ subdirectory
  generically — the old code hardcoded the one production area.

### Measured
- Vector layer (off by default, unchanged): production eval without embeddings
  21/21; the with-vector arm is blocked on infrastructure — no embeddings
  endpoint exists on this network (probed 2026-11-26). The exact A/B command
  is recorded in docs/ROADMAP.md; the default flips only on a measured gain.

## [0.6.0] - 2026-11-26

### Added
- `shmem health` (`shmem/health.py`, `tools/health-check.py`): the daily pipeline
  self-inspects — model endpoint configured *and* live, last summarizer run,
  journal/digest freshness, inbox backlog — each an actionable line with a
  non-zero exit code. A one-line verdict is embedded as **line 2 of DIGEST.md**
  and mirrored into AGENTS.md, so agents reading "recent activity" can never
  miss a broken pipeline (a silent model-config failure had been invisible for
  weeks on this repo's own host).
- `integrations/dsh/update-agents-block.sh`: regenerates a `SHMEM MIRROR` block
  in a harness-visible AGENTS.md (protocol summary + three newest auto-summaries
  + health line) after every summarizer run. Opt out with a `shmem-mirror:
  disabled` line in that file.
- Production eval corpus: `eval/queries.shmem.tsv` — 21 ground-truth queries
  against the real fleet memory tree, run with
  `QUERIES=eval/queries.shmem.tsv ROOT=/root/shared-memory bash eval/run.sh`.
  The synthetic `examples/` suite stays the CI gate; this measures real recall.
- **The summarizer titles its sessions.** The model must emit
  `TOPIC: <≤10 words, the session's own language>` as line 1; `split_topic`
  extracts it (fullwidth colon, stray-line tolerant), falling back to the old
  session title but through `clean_title` — one line, capped at 100 chars.
  Before this, sub-agent sessions echoed whole task prompts into headings, and
  every digest and mirror inherited the noise.
- Noise gates widened: `NOISE_PREFIX` gained the working-directory boilerplate
  notice, and `clean_facts` now filters noise bullets even when the model
  re-echoes them. One class of harness boilerplate had contributed five
  identically-titled junk blocks to the production journal.
- The production memory tree grew `facts/<area>/` subdirectories
  (`facts/machines/`, one file per machine; fleet.md 100 KB → 17 KB index +
  pointers). `md_files()`/`sync()` already covered subdirectories via rglob —
  no index change was needed, which is the design working as intended.

### Fixed
- The digest facts map silently lost its tail: `head_budget` cut at a line
  boundary in alphabetical file order, so everything after `infra.md` vanished
  from DIGEST.md. The map now caps each file at 4 sections and prints a
  collapsed `machines/` overview first, where the budget cap cannot reach it.
- Eval negative probes could contaminate themselves: a maintenance-journal
  entry that *named* the probe string got indexed, and "gibberish must return
  nothing" then hit its own maintenance record. `eval/run.sh` now reports a
  negative probe as a miss when any of its tokens exists in the corpus under
  test — the lesson is pinned in code, not folklore.

## [0.5.0] - 2026-11-17

### Added
- Packaging (ROADMAP P4): `pyproject.toml` with **zero runtime dependencies** and a
  `shmem` console script (`shmem search|index|stats|serve|summarize|digest|version`).
  The version is single-sourced: setuptools reads it dynamically from
  `shmem.__version__`, and `shmem version` reports the installed distribution's
  metadata through `importlib.metadata`, falling back to the source literal in a
  git clone. CI now builds a venv, runs `pip install -e .` and exercises the
  console script, so the install door is tested rather than assumed.
- Tests now fail if `pyproject.toml` grows a runtime dependency or duplicates the
  version — the zero-dependency invariant is protocol-level, not a preference.

### Fixed
- A misconfigured summarizer endpoint is no longer silent. `SUMMARIZE_MODEL_URL` pointing
  at a base URL (`http://host:8080/v1` instead of `…/v1/chat/completions`) made every
  nightly run fall back to keyword extraction while still reporting success — the log said
  `fallback(HTTPError)`. The run now logs the HTTP status (`fallback(HTTPError 404)`) and,
  once per run, states outright that the configured URL is not a completions endpoint.
  `config.example.env` now says the URL must include the path, with an example.

### Changed
- The implementation moved from `tools/*.py` into a `shmem/` package
  (`shmem/search.py`, `shmem/summarize.py`, `shmem/cli.py`). `tools/memory-search.py`
  and `tools/summarize-sessions.py` are **compat shims**: every path PROTOCOL.md,
  the Makefile, the units and the integrations name keeps working, including memory
  trees that symlink those files. `make new` copies both directories now.
- `shmem summarize` refuses unknown arguments instead of running. It is a write tool
  configured entirely by environment; a stray `--help` used to launch a real
  summarization pass against the live memory tree — which happened, and the
  keyword-fallback output it wrote had to be rolled back.

## [0.4.0] - 2026-11-16

### Added
- CI: Python matrix 3.9 / 3.11 / 3.13 (matching the declared minimum),
  `shellcheck -S warning` over every shell script.
- `make install-units` refuses to install a user-scope timer when the user manager is
  unreachable or lingering is off, and says what to do instead. The old behaviour was a
  silent success followed by a timer that never fired (the failure mode this repo's own
  host hit).
- `--stats` reports inbox files older than three days as `stale_inbox` — an unread
  message is a delivery failure, and until now nothing made a stalled queue visible.
- `units/README.md`: user-scope vs system-scope vs launchd, how to prove it fired.

### Changed (protocol)
- **Inbox is a queue of files, not one file per recipient.** Each message is written as
  its own file under `inbox/TO-<agent>/<YYYY-MM-DD>-<slug>.md`; nothing in an inbox is
  ever edited or overwritten, and a correction is a new message. A single file per
  recipient is last-writer-wins, and the message that disappears is precisely the unread
  one — observed in production. Legacy `inbox/TO-<agent>.md` files are still read and
  cleared the same way. Enforced by `inbox_write`, pinned by tests, specified in
  `PROTOCOL.md` (channel 3) and both READMEs.

## [0.3.2] - 2026-11-16

### Fixed
- **Summarizer process chatter**: transcript echoes (`assistant: …`,
  `[model changed: …]`), echoed shell commands and agent progress talk
  ("report delivered…") are now refused by one shared gate (`fact_ok`) that both
  the model path and the keyword fallback pass through — the old fallback wrote
  raw transcript lines straight into the journal.

### Changed
- `make-digest.sh`: the 5 newest auto-summaries are taken **across** journal
  files (was 5 per file — an old month could crowd out new activity); the newest
  private-memory snapshot wins with an older-file fallback; every cut is at a
  line boundary (no split CJK glyphs); section budgets reserve the tail, so a fat
  `INDEX.md` can no longer consume the whole 9 KB and silently drop activity and
  snapshots.
- READMEs: dropped the stale "~750 lines" claim; tool tables document the new
  digest and summarizer gates.

### Added
- `__version__` as the single source of truth; the MCP handshake reports it (was
  stuck at `0.1.0`).

## [0.3.1] - 2026-11-16

### Fixed
- **Search no longer returns deleted files**: `sync()` prunes index rows for
  markdown that vanished or was renamed. Ghost results pointing at nonexistent
  paths are worse than a cache miss.
- **No more unclosed sqlite connections** on any CLI or MCP path; CI now runs the
  suite and the example searches under `-W error::ResourceWarning` so leaks fail
  the gate instead of printing a warning nobody reads.
- **Concurrent first searches no longer die** with `database is locked`:
  `open_db()` sets `busy_timeout=5000` and WAL. The index stays disposable.

## [0.3.0] - 2026-11-13

Cross-month fallback & complete snapshot slice (DSh integration):

- `update-agents-block.sh` walks earlier `journal/*.md` files when the current
  month has no private-memory snapshot yet; the `(no snapshot yet)` placeholder
  only appears when no journal holds one.
- The injected slice keeps the opening of **every** memory entry (was: first six
  lines only, hiding stale facts below entry 2), drops `§` separators and code
  fences, and stops at the closing fence or the next journal heading.
- Units template documents the optional `snapshot -> digest -> agents-block`
  chain; `SHMEM_QUIET_SECONDS` documented in `config.example.env` and both READMEs.

## [0.2.0] - 2026-10-26

Journal hygiene:

- **Quiet-session guard**: sessions written to within the last 10 minutes are
  skipped without advancing their marker, so a still-growing session is
  summarized once after it settles (`SHMEM_QUIET_SECONDS`, default 600).
- **Dedup**: a fact block identical to one already in the journal is never
  re-appended.

Both are protocol rules enforced by the reference implementation.

## [0.1.0] - 2026-10-23

First public release: protocol (`PROTOCOL.md`) + zero-dependency reference
implementation.

- Three write channels: `facts/` (rewrite), `journal/` (append-only under
  `flock`), `inbox/` (delivery).
- FTS5 search with CJK bigram tokenizer, secret guard, MCP stdio face.
- Daily session summarizer with model/keyword fallback.
- Per-agent injection integrations (DSH, hermes), systemd user timer.
- Eval recall harness (23 pairs, 100% CI gate) + stdlib unit tests.
- Opt-in MCP write tools enforcing the three channels (`--allow-write`); the
  default face stays read-only.
