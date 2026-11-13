#!/bin/bash
# DSH integration: regenerate the auto-injected block inside the DSH AGENTS.md file.
#
# Why this exists: the only file that enters a DSH agent's context automatically in
# every session is AGENTS.md itself. So a small "here is what happened elsewhere"
# block is maintained inside AGENTS.md between marker comments — the hand-written
# instructions above the markers are never touched.
#
# Run it after each summarize run (see units/shmem-summarize.service ExecStartPost).
set -uo pipefail
export TZ="${SHMEM_TZ:-UTC}"

ROOT="${SHMEM_ROOT:-.}"
AGENTS="${DSH_AGENTS_MD:-$HOME/.dsh/AGENTS.md}"
MARK_BEGIN='<!-- BEGIN AUTO-INJECTED (shmem-summarizer) -->'
MARK_END='<!-- END AUTO-INJECTED (shmem-summarizer) -->'

journals=$(ls -1 "$ROOT"/journal/*.md 2>/dev/null | sort)
[ -n "$journals" ] || exit 0
journal=$(printf '%s\n' "$journals" | tail -1)   # newest file: drives "recent activity"

# Snapshot lookup walks newest -> oldest: private-memory snapshots are appended only
# when an agent's memory file changes, so a fresh month's journal may not have one yet.
# A real snapshot from the previous journal beats the "(no snapshot yet)" placeholder;
# the placeholder is used only when no journal file holds any snapshot.
#
# The slice keeps the opening of every memory entry, not only the first two: stale
# facts tend to hide further down the list, and an entry that never reaches AGENTS.md
# is invisible to the agent reading it. The "§" separator lines are dropped (they cost
# a line each) and each entry is clipped, so the slice stays a pointer, not a copy.
snap=""
while IFS= read -r f; do
  snap=$(awk '
    /^## .*private memory snapshot/ { block = $0; keep = 1; fence = 0; next }
    keep && fence >= 2 { keep = 0 }
    keep && /^## / { keep = 0 }
    keep {
      block = block "\n" $0
      if ($0 ~ /^```/) fence++
    }
    END { print block }
  ' "$f" | awk 'NF' | awk '$0 != "§" && $0 !~ /^```/' | cut -c1-140 | head -12)
  [ -n "$snap" ] && break
done < <(printf '%s\n' "$journals" | sort -r)
recent=$(awk '
  { lines[NR] = $0 }
  /^### auto-summary/ { hdr[NR] = 1 }
  END {
    c = 0
    for (i = NR; i >= 1 && c < 3; i--) if (hdr[i]) order[++c] = i
    for (j = c; j >= 1; j--) {
      i = order[j]
      print lines[i]
      for (k = i + 1; k <= NR; k++) if (lines[k] != "") { print lines[k]; break }
    }
  }
' "$journal")

tmp=$(mktemp)
{
  echo "$MARK_BEGIN"
  echo
  echo "## Auto-injected shmem block (generated $(date '+%F %H:%M %Z'); full details in $ROOT/INDEX.md and facts/)"
  echo
  echo "### Other agents' memory (latest snapshot)"
  printf '%s\n' "${snap:-"(no snapshot yet)"}"
  echo
  echo "### Recent activity"
  printf '%s\n' "${recent:-"(no auto-summary yet)"}"
  echo "$MARK_END"
} > "$tmp"

[ -r "$AGENTS" ] || : > "$AGENTS"

if grep -qF "$MARK_BEGIN" "$AGENTS"; then
  awk -v b="$MARK_BEGIN" -v e="$MARK_END" -v f="$tmp" '
    $0 == b { while ((getline line < f) > 0) print line; skip = 1; next }
    $0 == e { skip = 0; next }
    !skip { print }
  ' "$AGENTS" > "${AGENTS}.new"
else
  { cat "$AGENTS"; echo; cat "$tmp"; } > "${AGENTS}.new"
fi

mv "${AGENTS}.new" "$AGENTS"
rm -f "$tmp"
echo "AGENTS.md: $(wc -c < "$AGENTS") bytes"
