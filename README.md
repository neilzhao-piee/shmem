# shmem

> **Shared memory for agents. Markdown is the truth; agents just share a directory.**
> No daemon, no vector database, no cloud — one folder, one protocol, one search script.

[English](README.md) · [中文](README.zh.md)

![ci](https://github.com/neilzhao-piee/shmem/actions/workflows/ci.yml/badge.svg)

You run several AI agents. They each have amnesia. The usual fix — a memory service
with a vector index — adds a daemon, an API, a database, and a second source of truth
that can drift from what the agents actually believe.

shmem is the other way around: **the memory is a directory of markdown files** that the
agents already know how to read and write. On top of that directory sits a thin,
zero-dependency retrieval layer (SQLite FTS5, CJK-aware) and a daily job that harvests
durable facts from the agents' conversation history into the journal. That's it.

## Why shmem (the three differentiators)

| | typical "agent memory" | shmem |
|---|---|---|
| Storage | vector DB / hosted service | plain markdown, git-friendly, human-auditable |
| Who it serves | one assistant | **N agents sharing one directory** |
| Core idea | better retrieval | **a write discipline the agents actually follow** |

1. **File-first.** Markdown is the single source of truth. The FTS5 index is a derived
   cache — delete it and it rebuilds in milliseconds. You can `git diff` your agents'
   memory, read it with `cat`, and hand it to another agent without any API.
2. **Multi-agent by design.** The protocol is built around *delivery* (per-recipient
   inbox files, explicit ACK), per-agent injection (bootstrap-file block, hook, MCP, or
   pasted digest), and the hard fact that no agent's context is ever shared automatically.
3. **Protocol is the product.** The real contribution is [`PROTOCOL.md`](PROTOCOL.md) —
   three write channels (facts / journal / inbox), a read protocol (search, don't
   scroll), and hard rules (never write secrets; facts carry dates and sources; revise,
   don't append corrections). The scripts are a reference implementation in ~750 lines
   of pure Python standard library and a few shell scripts. **Zero third-party
   dependencies.** Adopt the protocol with your own tools if you like.

## Layout

```
INDEX.md            entry page: structure, protocol summary, pointers
DIGEST.md           auto-generated ≤9KB self-contained digest (for agents without filesystem)
facts/              current truth, rewritten in place, one file per topic
journal/            append-only event stream, rotated monthly
inbox/              per-recipient delivery files (TO-<other>.md)
tools/              memory-search.py · summarize-sessions.py · make-digest.sh
integrations/       per-agent glue: dsh/ (AGENTS.md block) · hermes/ (hook + private-memory snapshot)
units/              systemd user timer for the daily summarizer
examples/           a synthetic memory tree you can `make new` from
PROTOCOL.md         the protocol itself — read this first
```

## Quickstart

Runs on Linux and macOS: Python 3.9+ (stdlib only) and sqlite3 with FTS5 (Apple's system sqlite3 already includes FTS5). On macOS, the Makefile needs GNU make (`brew install make`, then `gmake`), and scheduling uses a launchd agent instead of the systemd timer (`units/launchd.example.plist`).

```sh
git clone <this repo> && cd shmem

# 1. create a memory directory
make new DIR=~/my-memory            # copies examples/ + tools/

# 2. search it (works immediately; index builds on first query)
SHMEM_ROOT=~/my-memory make search Q="where does the dashboard run"
SHMEM_ROOT=~/my-memory make search Q="为什么改成周日凌晨跑备份"   # Chinese works too

# 3. regenerate the digest for sandboxed agents
make digest DIR=~/my-memory

# 4. (optional) daily fact-harvesting from agent conversation history
cp config.example.env /etc/shmem.env   # set SUMMARIZE_MODEL_URL etc.
make install-units DIR=~/my-memory     # systemd user timer, runs 05:30 local
```

Point each agent at the directory in its instructions ("before real work, read
`INDEX.md`, then the relevant `facts/` files; search before reading the journal"), and
wire its injection door:

| your agent has… | wire it with |
|---|---|
| a bootstrap file (DSH `AGENTS.md`, Claude `CLAUDE.md`, …) | `integrations/dsh/update-agents-block.sh` — maintains a marker-delimited block in the file |
| a session hook (hermes `pre_llm_call`) | `integrations/hermes/inject-shared-memory.sh` — injects the digest once per session |
| MCP | `tools/memory-search.py --mcp` (stdio; tools `memory_search`, `memory_stats`) |
| nothing (sandboxed) | paste/upload `DIGEST.md` — that's the whole integration |

## The tools

| tool | what it does | deps |
|---|---|---|
| `tools/memory-search.py` | FTS5 index over the markdown; **CJK character-bigram tokenizer** (no jieba); incremental re-index by content hash; returns snippets with file + line numbers; CLI **and** MCP stdio server; optional vector layer (RRF fusion, off by default) | Python 3 stdlib |
| `tools/summarize-sessions.py` | daily harvest: incremental scan of agent session history → model extracts durable facts → appended to `journal/` under `flock`; quality gate (≤6 lines, no invented dates, no credential patterns); keyword fallback when the model is down; pluggable session adapters (DSH + hermes included) | Python 3 stdlib |
| `tools/make-digest.sh` | builds `DIGEST.md` (≤9KB): INDEX head + facts sketch + newest auto-summaries + latest private-memory snapshot | bash + coreutils (`tac`) |
| `integrations/dsh/update-agents-block.sh` | refreshes the auto-injected block in DSH's `AGENTS.md` between markers | bash + awk |
| `integrations/hermes/snapshot-memory.sh` | appends the other agent's private `MEMORY.md`/`USER.md` to the journal when they change | bash + sha256sum + flock |
| `integrations/hermes/inject-shared-memory.sh` | hermes `pre_llm_call` hook: refresh digest, snapshot memory, inject once per session | bash + python3 |

Configuration lives in environment variables (see [`config.example.env`](config.example.env)) —
nothing is hardcoded.

## Security model

- **Writers**: the protocol forbids credentials in memory (locations only) — rule 1 in
  the protocol, enforced by the summarizer's prompt.
- **Indexer**: lines matching obvious secret patterns (`password = …`) are never indexed,
  and lines containing values from files listed in `SHMEM_SECRET_FILES` are skipped.
- **Summarizer**: output passes a line-level gate (bullet form, no out-of-year dates,
  no credential patterns) before it touches the journal.
- **Boundaries**: all writes are bounded (≤4KB per run, ≤20-line inboxes, ≤9KB digest);
  journal appends are serialized with `flock`. Single-host by design — for multi-host,
  run git as the sync layer (markdown was chosen partly for this).

## Design notes & honest limits

- **BM25 is enough at this scale.** For a few MB of memory, lexical search beats most
  embedding setups on precision and costs nothing. The vector layer exists and is off by
  default; turn it on if the corpus grows past ~1 MB and CJK phrase recall measurably
  degrades.
- **The journal is searchable, not readable.** Nobody reads it whole; that's the point.
  If you find yourself scrolling, your `facts/` file is missing an entry.
- **Single machine.** `flock` serializes writers on one host. Cross-host sharing works
  via git (the files are markdown on purpose), not via any shmem feature.
- **The summarizer is lossy on purpose.** It only keeps what a local model certifies as
  still-true; anything it misses stays in the session history. It never writes to
  `facts/` — curated truth is deliberately human/agent-written.
- **Not a database.** Concurrency model = file locks + discipline, not transactions.
  At the scale of "a few agents, one machine", that is a feature.

## Origin

shmem was built to keep two long-running agents — a coding agent (DSH) and an assistant
agent (hermes) — sharing one memory on one machine, and it has been running in that
configuration since 2026-10. The DSH- and hermes-specific glue lives in `integrations/`
and is clearly labeled; everything else is agent-agnostic.

[![](https://img.shields.io/badge/powered_by-dsh-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](https://github.com/deepseek-ai/deepseek-harness)

## License

MIT — see [LICENSE](LICENSE).
