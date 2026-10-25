# The shmem Protocol

This is the actual contribution of this project. The Python and shell scripts are a
reference implementation; **the protocol is what you adopt**, and it works with any
agent that can read and write files.

## Three invariants

1. **Markdown is the single source of truth.** Everything an agent "remembers" lives in
   plain `.md` files: human-readable, git-friendly, diffable, auditable. Any index, cache,
   or snapshot is *derived* and disposable — delete it and it rebuilds.
2. **Agents do not share context; they share a directory.** Context windows are frozen per
   session and truncated per read. A shared file system plus a write discipline beats any
   attempt to sync conversation state.
3. **Delivery is explicit.** No agent automatically sees another agent's writes. A write
   only counts when it is *delivered* (inbox file + one explicit sentence, or an injection
   hook fired). Never assume "I wrote it, so they saw it".

## Platform scope

POSIX only (Linux and macOS): `fcntl.flock`, `flock(1)`, and systemd/launchd are part of
the reference implementation. **Windows is out of scope** — the write tools refuse to run
there rather than improvise a second locking model. The protocol itself is
platform-neutral; only this reference implementation carries this scope.

## Directory layout

```
INDEX.md            entry page: structure, protocol summary, pointers (read this first)
DIGEST.md           auto-generated self-contained digest, ≤9KB, for agents that cannot read files
facts/              current truth, rewritten in place
  <topic>.md        one file per topic (fleet, a project, the agents themselves, ...)
journal/            append-only event stream, rotated monthly
  <YYYY-MM>.md
inbox/              per-recipient delivery files
  TO-<other-agent>.md
tools/              the shmem scripts (index/search, digest, summarizer)
.index/             derived FTS5 index (git-ignored, disposable)
```

## Write protocol — three channels, do not mix them

### Channel 1 — facts (rewrite in place)

Use when **a fact changed**. Edit the matching section of `facts/<topic>.md` in place; bump
the `Updated: YYYY-MM-DD` line at the top of the file. Facts contain only what is true
*right now*. If a fact is stale, delete or revise it — never append a "correction" below
the old value. Appended corrections are exactly how these files bloat until nobody reads them.

### Channel 2 — journal (append only)

Use for **what happened today**. Append a dated section to `journal/<YYYY-MM>.md`, serialized
with `flock` (any number of writers, no interleaving):

```sh
flock -x /path/to/shmem/.lock sh -c 'cat >> journal/2026-01.md' <<'EOF'
## 2026-01-12 backup job moved
one-line conclusion + commit/path + how it was verified + what is still open
EOF
```

One section must say: what changed, why, how it was verified, what is still open. No long logs,
no stack traces — pointers to them instead. Journals grow forever; that is fine, because nobody
reads them whole (see read protocol).

### Channel 3 — inbox (delivery)

Use to **notify the other agent(s)**. Write to `inbox/TO-<other>.md` — only the file *for*
the other agent, never one for yourself. Content budget: **≤20 lines**, structured as

1. one-line conclusion,
2. pointer (file + section, or commit),
3. what you need from them.

The recipient handles it, moves the content into `journal/`, and clears the file. An inbox
file that stays unread is a failure of delivery — if it sits there for a while, re-send.

## Read protocol

- **Before working**: read `INDEX.md`, then the one (at most three) `facts/` files relevant
  to the task.
- **"How was this decided / why?"**: search, don't scroll. `tools/memory-search.py "query"`
  returns snippets with file + line numbers; read only the hit range. Full-journal reads are
  the anti-pattern the journal layout exists to prevent.
- **Live state is not memory**: deployment versions, data sizes, health — run the status
  script for it, don't trust prose. Memory records *decisions and facts*, not *state*.
- **Sandboxed agent** (no filesystem): it gets `DIGEST.md` pasted/uploaded, or the MCP face
  of the search tool. That is all — and it is enough.

## Hard rules

1. **Never write passwords or keys.** Locations only (`/path/to/.env`). The reference
   implementation enforces this twice: the summarizer's prompt forbids it, the search index
   *skips lines* that look like `password = <value>` or contain values from configured
   credential files. Defense in depth, but the rule is for the writers first.
2. **Facts carry a date and a source** (commit hash, file path, script name). "Probably X"
   is not a fact; either verify it or write it in the journal as an open question.
3. **Revise, don't override.** Stale content is deleted or edited in place.
4. **Ownership**: any agent may edit `facts/`, but only sections it verified itself. A large
   or cross-cutting change goes through `inbox/` first, so the other agent can object before
   it lands.
5. **Budgets**: digest ≤9KB, inbox ≤20 lines, auto-summary ≤6 lines per session, ≤4KB per
   summarizer run. Small numbers on purpose — if a delivery doesn't fit, it is not a
   delivery, it is a document, and documents belong in facts/.

## Per-agent injection (optional, recommended)

Each agent needs its own door back into the shared memory, because no agent's context is
shared automatically:

- **Agent with a bootstrap file** (e.g. DSH's `AGENTS.md`): a small block is maintained
  inside that file between marker comments (`<!-- BEGIN/END AUTO-INJECTED -->`), refreshed
  by a script after each summarizer run. Hand-written instructions outside the markers are
  never touched. See `integrations/dsh/update-agents-block.sh`.
- **Agent with a hook** (e.g. hermes `pre_llm_call`): the hook refreshes `DIGEST.md` and
  injects it **once per session**. See `integrations/hermes/inject-shared-memory.sh`.
- **Agent with MCP**: point its MCP client at `tools/memory-search.py --mcp`; read tools
  `memory_search` and `memory_stats`. Write tools `memory_write` (facts rewrite),
  `memory_append` (journal append under `.lock`) and `inbox_write` (≤ 20 lines) are
  exposed only with `--allow-write`; they enforce the three channels, the secret guard
  (refuses, never writes), and slug rules, then re-index the changed file.
- **Sandboxed agent**: paste/upload the digest. That's the whole integration.

## Automation (the summarizer)

Conversation history is the largest source of durable facts, so a scheduled job
(`units/shmem-summarize.timer`) runs `tools/summarize-sessions.py` daily:

- **Incremental**: remembers per-session markers (`.summarize-state.json`); only changed
  sessions are processed; at most 12 sessions and 4KB of journal growth per run.
- **Model-first, fallback always**: asks a (typically local) model to extract durable facts;
  if the model is unreachable it falls back to deterministic keyword extraction, so the job
  degrades instead of dying.
- **Quality gate**: only `- `-prefixed bullet lines survive; lines inventing dates outside
  the session's year are dropped; lines containing credential patterns are dropped; at most
  6 lines per session.
- **Quiet-session guard + dedup**: a session written to within the last 10 minutes is
  skipped without advancing its marker (summarized once it has settled), and a fact block
  identical to one already in the journal is never re-appended.
- **Discipline boundary**: summaries go to `journal/` **only**. The summarizer never touches
  `facts/` — curated truth is still written by an agent, deliberately.

## Why it is shaped this way

- Per-agent context is **frozen at session start** (a fixed character budget) and read
  paths truncate long lines/files. A single big memory file fails both: the tail is never
  seen. Splitting into small facts + per-month journal + search layer makes truncation a
  non-issue.
- The two channels that *change* (facts, journal) have different shapes on purpose:
  rewrite-in-place keeps facts small and current; append-only keeps the journal an honest
  timeline that is cheap to grow and searchable, not readable, end to end.
- The inbox exists because the naive "both agents read the same file" model breaks in
  practice: agents start, stop, and resume at random times. Explicit delivery with a
  recipient-side ACK (move-to-journal) is the only mechanism that survives that.
