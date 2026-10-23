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

journal=$(ls -1 "$ROOT"/journal/*.md 2>/dev/null | sort | tail -1)
[ -n "$journal" ] || exit 0

snap=$(awk '/^## .*private memory snapshot/{block=$0; keep=1; next} keep{block=block"\n"$0} END{print block}' \
  "$journal" | awk 'NF' | cut -c1-160 | head -6)
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
