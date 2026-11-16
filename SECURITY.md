# Security policy

## Threat model: local trust

shmem is deliberately a **local-trust** design. The memory tree is an ordinary
directory of markdown files readable and writable by the user who runs it. Every
agent sharing it has that user's file permissions; the shared directory does not
and cannot act as a security boundary between them. If your agents must be
isolated from each other, shmem is the wrong tool — run separate memory trees.

There is no daemon, no listening socket, and no network service. The only
outbound connections are opt-in: the summarizer's model endpoint
(`SUMMARIZE_MODEL_URL`, may be local), the optional embedding endpoint
(`SHMEM_EMBED_BASE_URL`, off by default), and whatever GitHub you push to.

## Secret guard: scope and limits

The indexer and the write channels refuse content that looks like a credential:

- obvious patterns (`password = …`, `api_key: …`, 中文口令模式) are never indexed
  or written;
- values listed in files named by `SHMEM_SECRET_FILES` are refused verbatim.

This is a **leak-resistance aid, not a vault**. Pattern guards cannot see every
secret, and markdown is meant to be read by humans and agents. Follow the
protocol rule instead: *never write secrets into the memory tree; write their
location only* (e.g. `/root/.service-creds`, mode 0600). Anything a human can
guess ("neilzhao11") is not a secret to the guard — treat the memory tree as if
it were public.

## MCP face: read-only by default

`memory-search.py --mcp` exposes only `memory_search` and `memory_stats`. The
three write tools (`memory_write`, `memory_append`, `inbox_write`) exist only
behind an explicit `--allow-write`, and when enabled they enforce the protocol
(flock serialization, slug rules, ≤20-line inbox, secret guard). Hosts that
should observe but not write can be given the default face safely.

## Scheduling and derived state

The systemd/launchd units run as your user with no elevated privileges. `.index/`,
`DIGEST.md` and `.summarize-state.json` are derived and disposable — deleting
them is always safe and never touches the markdown truth.

## Reporting a vulnerability

Open a **private** vulnerability report via GitHub's "Report a vulnerability"
(security/advisories) rather than a public issue. For this reference
implementation, expect a triage response within 14 days; the protocol itself
changes only through explicit `PROTOCOL.md` revisions.
