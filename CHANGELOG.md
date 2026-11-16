# Changelog

Notable changes per release. shmem adheres to semantic versioning; the protocol
itself evolves only through explicit `PROTOCOL.md` revisions.

## [Unreleased]

### Fixed
- **Search no longer returns deleted files**: `sync()` prunes index rows for
  markdown that vanished or was renamed. Ghost results pointing at nonexistent
  paths are worse than a cache miss.
- **No more unclosed sqlite connections** on any CLI or MCP path; CI now runs the
  suite and the example searches under `-W error::ResourceWarning` so leaks fail
  the gate instead of printing a warning nobody reads.
- **Concurrent first searches no longer die** with `database is locked`:
  `open_db()` sets `busy_timeout=5000` and WAL. The index stays disposable.
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

### Added
- `__version__` as the single source of truth; the MCP handshake reports it (was
  stuck at `0.1.0`).
- CI: Python matrix 3.9 / 3.11 / 3.13 (matching the declared minimum),
  `shellcheck -S warning` over every shell script.

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
