# Agents (runtime facts)

Updated: 2026-01-12

## agent-a (coding agent, on build1)

- Runs locally on `build1.home.lan` with shell + file access to `/home/dev/`.
- Auto-injection: only `~/.config/agent-a/AGENTS.md` enters context each session, so the shmem
  block is maintained there by `integrations/dsh/update-agents-block.sh` (marker-delimited).
- Reads this directory directly; no digest needed.

## agent-b (assistant agent, sandboxed)

- Runs in a browser sandbox: sees only uploaded files, **cannot** read this directory or run SSH.
- Gets content by one of: paste the `DIGEST.md` text, upload the file, or via its MCP client using
  `tools/memory-search.py --mcp`.
- Private memory lives in its own `MEMORY.md`/`USER.md`; `integrations/hermes/snapshot-memory.sh`
  copies changes into `journal/` so agent-a can see them too.

## How the two stay in sync

- **Shared**: this directory (facts/journal/inbox) — the single source of truth.
- **Inbound to a sandboxed agent**: DIGEST.md (≤9KB) or the MCP search tool.
- **Delivery**: `inbox/TO-<other>.md` + one explicit sentence. Never assume "I wrote it, so they saw it".
- **Automatic**: daily summarizer (systemd timer) pulls durable facts from both agents' recent
  sessions into `journal/`; it never rewrites `facts/`.

## Known gotchas

- The sandboxed agent has **no** access to this filesystem — uploading DIGEST.md is the only push path
  (2026-01-06, confirmed).
- If the summarizer's model endpoint is down it falls back to keyword extraction; check `.summarize.log`
  before assuming a quiet day.
