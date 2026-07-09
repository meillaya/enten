#!/usr/bin/env bash
# ─── enten — 24-hour job completion error budget ─────────────────────────
#
# Reads the last 24 hours of ``jobs`` rows from Postgres and computes the
# completion rate as ``done / (done + failed + errored)``. Exits non-zero
# when the rate falls below the configurable threshold.
#
# Env:
#   ERROR_BUDGET_THRESHOLD  default 0.99 (99%)
#
# Usage:
#   bash scripts/error-budget-check.sh
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

THRESHOLD="${ERROR_BUDGET_THRESHOLD:-0.99}"

rows=$(docker compose exec -T postgres psql -U enten -d enten -t -A -c "
  SELECT state, count(*)
  FROM jobs
  WHERE created_at > now() - interval '24 hours'
  GROUP BY state
" 2>/dev/null || true)

declare -A counts
counts=([done]=0 [failed]=0 [errored]=0)

while IFS='|' read -r state count; do
  [[ -z "$state" ]] && continue
  # Trim whitespace produced by psql -A.
  state="${state// /}"
  count="${count// /}"
  counts[$state]="${count:-0}"
done <<< "$rows"

done_count="${counts[done]:-0}"
failed_count="${counts[failed]:-0}"
errored_count="${counts[errored]:-0}"
total=$((done_count + failed_count + errored_count))

if [[ "$total" -eq 0 ]]; then
  echo "error-budget: no jobs in last 24h (threshold ${THRESHOLD})"
  exit 0
fi

rate=$(awk -v done="$done_count" -v total="$total" 'BEGIN { printf "%.4f", done / total }')
ok=$(awk -v rate="$rate" -v threshold="$THRESHOLD" 'BEGIN { print (rate >= threshold) ? 1 : 0 }')

echo "error-budget: done=${done_count} failed=${failed_count} errored=${errored_count} total=${total} rate=${rate} threshold=${THRESHOLD}"

if [[ "$ok" -eq 1 ]]; then
  exit 0
fi

echo "error-budget: completion rate below threshold" >&2
exit 1
