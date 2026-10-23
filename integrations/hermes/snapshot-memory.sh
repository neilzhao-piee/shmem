#!/bin/bash
# hermes integration: snapshot an agent's private memory files into the shared journal
# whenever they change. Journal is append-only; facts/ stays curated by hand.
#
# HERMES_MEM_DIR points at the directory holding the private memory files
# (by default MEMORY.md and USER.md, the hermes convention).
set -uo pipefail
export TZ="${SHMEM_TZ:-UTC}"

MEM_DIR="${HERMES_MEM_DIR:-$HOME/.hermes/memories}"
ROOT="${SHMEM_ROOT:-.}"

mem="$MEM_DIR/MEMORY.md"
usr="$MEM_DIR/USER.md"
[ -r "$mem" ] || exit 0

cur=$( { sha256sum "$mem"; [ -r "$usr" ] && sha256sum "$usr"; } | sha256sum | cut -d' ' -f1 )
state="$ROOT/.agent-mem.sha"
old=$(cat "$state" 2>/dev/null || true)
if [ "$cur" = "$old" ]; then
  exit 0
fi

month=$(date '+%Y-%m')
file="$ROOT/journal/${month}.md"
tmp=$(mktemp)
{
  echo
  echo "## $(date '+%F %H:%M') private memory snapshot (auto; source ${mem} / ${usr})"
  echo
  echo '```'
  echo "[MEMORY.md]"
  cat "$mem"
  echo
  echo "[USER.md]"
  cat "$usr" 2>/dev/null
  echo '```'
} > "$tmp"

flock -x "$ROOT/.lock" -c "cat '$tmp' >> '$file'"
rm -f "$tmp"
printf '%s\n' "$cur" > "$state"
echo "snapshot appended to $file"
