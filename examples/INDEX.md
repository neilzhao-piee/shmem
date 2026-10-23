# Shared Memory Entry (agent-a + agent-b)

> Read this page first, then only the parts you need. **Do not** append to `MEMORY.md` — it is a stub pointing here.

## Structure

```
INDEX.md              this page: structure, read/write protocol, pointers
DIGEST.md             self-contained digest (auto-generated, ≤9KB): for agents that cannot read local files
facts/                current truth, **rewrite-in-place**: only "true right now", edit in place when it changes
  fleet.md            machines / network / services
  agents.md           the two agents' runtimes and service chain
journal/2026-01.md    event stream, **append-only**, rotated monthly
inbox/                drop zone: TO-agent-b.md (for agent-b), TO-agent-a.md (for agent-a)
tools/                generator scripts (see the shmem repo): make-digest.sh, memory-search.py, ...
```

## When to read what

- Background before working → the matching `facts/<topic>.md` (usually 1 file, at most 3).
- "How was this decided before / why" → search first (`tools/memory-search.py`, CJK ok), then read the hit snippet by line number. Do **not** read whole journal files.
- For a sandboxed agent that cannot read local files → paste/upload `DIGEST.md` (≤9KB, self-contained).

## How to write (three channels — do not mix them)

1. **A fact changed** → edit the matching section in `facts/<topic>.md` in place. Bump `Updated: YYYY-MM-DD` at the top of the file.
2. **Something happened today** → append a section to `journal/<YYYY-MM>.md` (serialize with `flock`):
   ```sh
   flock -x /path/to/shmem/.lock sh -c 'cat >> journal/2026-01.md' <<'EOF'
   ## 2026-01-12 backup job moved
   ...one-line conclusion + commit/path + how it was verified...
   EOF
   ```
   One section = "what changed / why / how verified / what is still open". No long logs.
3. **Notify the other agent** → write to `inbox/TO-<other>.md` (only the file **for** the other agent). Content: one-line conclusion + pointer + what you need from them, ≤20 lines. When they have handled it, they move it into the journal and clear the file.

## Hard rules

- **Never write passwords/keys here** — locations only (e.g. `/path/to/.env`).
- Facts carry a date and a source (commit/path/script). No "probably".
- Stale facts are deleted or revised in place — do not append a "correction" to override an old one.
- Both agents may edit `facts/`, but only sections they verified themselves; large changes go through `inbox/` first.

## Pointers

- Live service state: run `scripts/fleet-status.sh` — do not trust prose for liveness.
