#!/bin/bash
# hermes integration: pre_llm_call hook that injects the shmem digest ONCE per session.
#
# Contract (hermes shell hooks): JSON payload on stdin, optional {"context": "..."} JSON on stdout.
# Event-driven "aggregation": before injecting, refresh DIGEST.md and snapshot the agent's
# private memory into the journal, so no extra cron entry is needed.
set -uo pipefail
export TZ="${SHMEM_TZ:-UTC}"

ROOT="${SHMEM_ROOT:-.}"
LABEL="${SHMEM_HOOK_LABEL:-Shared memory (auto-injected): the digest below is shared with the other agents; the full entries live in the shmem root (INDEX.md, facts/, journal/) — read the files directly when you need detail.}"

payload=$(cat 2>/dev/null || true)
sid=$(printf '%s' "$payload" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)
sid=$(printf '%s' "${sid:-default}" | tr -c 'A-Za-z0-9._-' '_' | cut -c1-80)

log="$ROOT/.hook.log"
printf '%s event=%s sid=%s TMPDIR=%s\n' "$(date '+%FT%T')" \
  "$(printf '%s' "$payload" | sed -n 's/.*"hook_event_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')" \
  "$sid" "${TMPDIR:-}" >> "$log" 2>/dev/null

state_dir="${TMPDIR:-/tmp}/shmem-hook"
mkdir -p "$state_dir" 2>/dev/null
find "$state_dir" -type f -mtime +3 -delete 2>/dev/null

stamp="$state_dir/$sid.stamp"
if [ -e "$stamp" ]; then
  exit 0
fi
touch "$stamp" 2>/dev/null

# --- aggregation step (best effort, bounded) ---
here=$(cd "$(dirname "$0")" && pwd)
[ -x "$here/snapshot-memory.sh" ] && timeout 5 bash "$here/snapshot-memory.sh" >/dev/null 2>&1
[ -x "$ROOT/tools/make-digest.sh" ] && timeout 5 bash "$ROOT/tools/make-digest.sh" >/dev/null 2>&1

digest="$ROOT/DIGEST.md"
[ -r "$digest" ] || exit 0
body=$(head -c 9000 "$digest")

PY=$(command -v python3 2>/dev/null)
[ -n "${PY:-}" ] || exit 0

printf '%s' "$body" | "$PY" -c '
import json, sys
label = sys.argv[1]
print(json.dumps({"context": label + "\n\n" + sys.stdin.read()}))
' "$LABEL"
exit 0
