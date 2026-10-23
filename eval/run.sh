#!/bin/bash
# Eval harness: query<TAB>expected-file pairs, recall must be 100%.
# Ground truth against examples/ (synthetic fixtures committed to the repo).
# The point: search "improvements" must be measured, not felt. Fails CI on any miss.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
pass=0; fail=0
while IFS=$'\t' read -r q expected; do
  [ -n "$q" ] || continue
  out=$(SHMEM_ROOT=examples python3 tools/memory-search.py "$q" --json -n 8)
  if printf '%s' "$out" | grep -qF "$expected"; then
    pass=$((pass+1))
  else
    fail=$((fail+1))
    printf 'MISS: %s -> expected %s\n' "$q" "$expected"
  fi
done < eval/queries.tsv
printf 'eval recall: %d/%d\n' "$pass" "$((pass+fail))"
[ "$fail" -eq 0 ]
