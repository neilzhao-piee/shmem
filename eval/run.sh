#!/bin/bash
# Eval harness: query<TAB>expected-file pairs, recall must be 100%.
# Ground truth against examples/ (synthetic fixtures committed to the repo).
# The point: search "improvements" must be measured, not felt. Fails CI on any miss.
#
# Against a REAL memory tree (the vector-layer trigger is "measured Chinese recall
# loss", not a feeling):  QUERIES=eval/queries.shmem.tsv ROOT=/root/shared-memory bash eval/run.sh
# Expected files must be unambiguous in that tree — one query, one right file.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
QUERIES="${QUERIES:-eval/queries.tsv}"
MEMROOT="${ROOT:-examples}"
# SHMEM_VECTOR=1 fuses the vector layer (endpoint from env or <root>/.embed.json;
# tools/memory-search.py --full then embeds automatically when one is configured).
# The harness PINS the default to --no-vector so recall numbers stay reproducible
# regardless of what config discovery would auto-enable on a real tree.
VECTOR_ARGS=(--no-vector)
[ "${SHMEM_VECTOR:-0}" = 1 ] && VECTOR_ARGS=(--vector)
pass=0; fail=0
while IFS=$'\t' read -r q expected; do
  [ -n "$q" ] || continue
  if [ "$expected" = "[]" ]; then
    # A negative probe only proves something if its tokens are absent from the
    # corpus under test: a journal entry that *names* the probe self-contaminates
    # the check (observed 2026-11-26 — the probe hit its own maintenance record).
    contaminated=""
    for tok in $q; do
      if grep -rilIF --include='*.md' -- "$tok" "$MEMROOT" >/dev/null 2>&1; then
        contaminated="$contaminated $tok"
      fi
    done
    if [ -n "$contaminated" ]; then
      fail=$((fail+1))
      printf 'PROBE-CONTAMINATED: %s -> token(s) present in corpus:%s\n' "$q" "$contaminated"
      continue
    fi
  fi
  out=$(SHMEM_ROOT="$MEMROOT" python3 tools/memory-search.py "$q" --json -n 8 ${VECTOR_ARGS[@]+"${VECTOR_ARGS[@]}"})
  if printf '%s' "$out" | grep -qF "$expected"; then
    pass=$((pass+1))
  else
    fail=$((fail+1))
    printf 'MISS: %s -> expected %s\n' "$q" "$expected"
  fi
done < "$QUERIES"
printf 'eval recall: %d/%d\n' "$pass" "$((pass+fail))"
[ "$fail" -eq 0 ]
