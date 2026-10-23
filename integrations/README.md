# Integrations

Per-agent glue for getting shmem content into an agent's context automatically.
Everything here is optional — an agent that simply follows the protocol (reads
`INDEX.md`, writes via the three channels) needs nothing in this directory.

## `dsh/` — DSH agent (bootstrap-file style)

`update-agents-block.sh` maintains a marker-delimited block inside the DSH bootstrap
file (`DSH_AGENTS_MD`, default `~/.dsh/AGENTS.md`) — the only file that enters a DSH
session's context automatically. Run it after each summarizer run (the example systemd
service has the `ExecStartPost` line, commented out).

The same pattern works for any agent with a bootstrap file (e.g. `CLAUDE.md`): point
`DSH_AGENTS_MD` at that file. Only the marker lines and the block content are touched.

Env: `SHMEM_ROOT`, `DSH_AGENTS_MD`, `SHMEM_TZ`.

## `hermes/` — hermes-style agent (hook style)

- `inject-shared-memory.sh` — a `pre_llm_call` hook: refreshes `DIGEST.md`, snapshots the
  agent's private memory, then injects the digest **once per session** as context.
- `snapshot-memory.sh` — appends the agent's private `MEMORY.md`/`USER.md` (dir:
  `HERMES_MEM_DIR`) to `journal/` whenever their hashes change, so the *other* agent can
  see them.

Both honor `SHMEM_ROOT`, `SHMEM_TZ`, plus `HERMES_MEM_DIR` / `SHMEM_HOOK_LABEL`.
The "once per session" stamp lives in `$TMPDIR/shmem-hook/` and self-cleans after 3 days.

## MCP (any MCP-capable host)

No glue needed:

```sh
python3 /path/to/shmem/tools/memory-search.py --mcp
```

stdio JSON-RPC; tools: `memory_search` (query → snippets with file + line numbers),
`memory_stats` (index size, vector-layer status).
