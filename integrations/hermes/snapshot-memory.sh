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

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$@"
  else python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$@"
  fi
}
cur=$( { sha256 "$mem"; [ -r "$usr" ] && sha256 "$usr"; } | sha256 | cut -d' ' -f1 )
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

if command -v flock >/dev/null 2>&1; then
  flock -x "$ROOT/.lock" -c "cat '$tmp' >> '$file'"
else
  # BSD/macOS has no flock(1); fcntl.flock is equivalent and python3 is already a dependency.
  python3 -c 'import fcntl,sys; lf=open(sys.argv[1],"a"); fcntl.flock(lf,fcntl.LOCK_EX); lf.write(open(sys.argv[2],"rb").read()); lf.close()' "$ROOT/.lock" "$tmp"
fi
rm -f "$tmp"
printf '%s\n' "$cur" > "$state"
echo "snapshot appended to $file"
