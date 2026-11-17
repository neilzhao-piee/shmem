# Changelog

Notable changes per release. shmem adheres to semantic versioning; the protocol
itself evolves only through explicit `PROTOCOL.md` revisions.

## [Unreleased]

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
