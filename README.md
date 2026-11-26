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
   don't append corrections). The scripts are a reference implementation in pure
   Python standard library and a few shell scripts. **Zero third-party
   dependencies.** Adopt the protocol with your own tools if you like.

## Layout

```
INDEX.md            entry page: structure, protocol summary, pointers
DIGEST.md           auto-generated ≤9KB self-contained digest (for agents without filesystem)
facts/              current truth, rewritten in place, one file per topic
  <area>/<entry>.md   a topic that outgrew one file splits one entry per file
                      (the production example: facts/machines/<host>.md)
journal/            append-only event stream, rotated monthly
inbox/              per-recipient delivery queues, one file per message
  TO-<other>/<YYYY-MM-DD>-<slug>.md
shmem/              the reference implementation: search.py · summarize.py · cli.py
tools/              stable entry points (compat shims into the package) + make-digest.sh
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
make new DIR=~/my-memory            # copies examples/ + tools/ + shmem/

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
| MCP | `tools/memory-search.py --mcp` (stdio; read tools `memory_search`, `memory_stats`; write tools `memory_write`, `memory_append`, `inbox_write` only with `--allow-write`) |
| nothing (sandboxed) | paste/upload `DIGEST.md` — that's the whole integration |

### Install it instead (extra door, not the front door)

```sh
pip install shmem
SHMEM_ROOT=~/my-memory shmem search "where does the dashboard run"
SHMEM_ROOT=~/my-memory shmem stats      # index size + stalled inbox deliveries
SHMEM_ROOT=~/my-memory shmem serve      # MCP stdio server, read-only by default
```

`pip install` adds no runtime dependency and no daemon: it is the same package the
clone uses, plus a console script. Cloning stays first-class — the memory tree
keeps working through `tools/`, which are shims into the package.

## The tools

| tool | what it does | deps |
|---|---|---|
| `tools/memory-search.py` | FTS5 index over the markdown; **CJK character-bigram tokenizer** (no jieba); incremental re-index by content hash; returns snippets with file + line numbers; deterministic tie-break (`path,start_line`, not rowid) + per-file cap of 2 in the result window; derived `DIGEST.md` is excluded from the index; CLI **and** MCP stdio server; vector layer (RRF fusion) auto-enabled when an embed endpoint is configured (env or `<root>/.embed.json`), embeddings content-addressed so appends pay only new chunks; MCP write tools (three channels, flock, secret guard) only with `--allow-write` | Python 3 stdlib |
| `tools/summarize-sessions.py` | daily harvest: incremental scan of agent session history → model extracts durable facts → appended to `journal/` under `flock`; quality gate (≤6 lines, no invented dates, no credential patterns, no process chatter — progress talk, transcript echoes, shell echoes); quiet guard + dedup (`SHMEM_QUIET_SECONDS`=600 — growing sessions are summarized only after they settle, identical blocks are never re-appended); keyword fallback when the model is down; pluggable session adapters (DSH + hermes included) | Python 3 stdlib |
| `tools/make-digest.sh` | builds `DIGEST.md` (≤9KB, line-boundary budget): INDEX structure slice + facts section map + 5 newest auto-summaries across journal files + newest private-memory snapshot (older-file fallback); header carries `health-check.py --oneline` | bash + awk |
| `integrations/dsh/update-agents-block.sh` | refreshes the auto-injected block in DSH's `AGENTS.md` between markers | bash + awk |
| `integrations/hermes/snapshot-memory.sh` | appends the other agent's private `MEMORY.md`/`USER.md` to the journal when they change | bash + sha256sum/shasum + flock (python3 fallbacks) |
| `eval/run.sh` | recall harness over committed synthetic fixtures (23 pairs incl. CJK + a no-match honesty case); every search change must keep it at 100% — this is the CI gate. Real-tree set too: `QUERIES=eval/queries.shmem.tsv ROOT=/root/shared-memory bash eval/run.sh` (21 pairs, one unambiguous file each) | bash + python3 |
| `tests/test_shmem.py` | unit tests for the protocol-enforcing parts: CJK bigrams, secret guard (pattern + credential values), slug rules, three write channels, read-only default face | Python 3 stdlib |
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
- **Boundaries**: all writes are bounded (≤4KB per run, ≤20 lines per inbox message, ≤9KB digest);
- **Durable delivery**: each inbox message is its own file under `TO-<agent>/` and is never
  overwritten, so an unread message cannot be erased by the next one; `--stats` lists inbox
  files older than three days as stalled deliveries;
  journal appends are serialized with `flock`. Single-host by design — for multi-host,
  run git as the sync layer (markdown was chosen partly for this).

## Design notes & honest limits

- **BM25 is enough at this scale.** For a few MB of memory, lexical search beats most
  embedding setups on precision and costs nothing. The vector layer (RRF fusion) turns
  itself on only when an embedding endpoint is configured — env vars or a
  `<root>/.embed.json` — and embeddings are content-addressed, so a journal append
  reuses every old chunk's vector and pays only for the new ones. Measured on the
  1 MB / ~1000-chunk production corpus: recall 21/21 either way; fusion moved top-1
  from 13/21 to 15/21 and mean rank 1.75 → 1.45 — a ranking win, not a recall win.
  Opt out with `--no-vector` / `SHMEM_VECTOR=0` (the eval harness pins it off for
  reproducibility).
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
