#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." &>/dev/null && pwd)"
cd "$REPO_ROOT"

THRESHOLD="${ERROR_BUDGET_THRESHOLD:-0.99}"
if [[ ! "$THRESHOLD" =~ ^(0([.][0-9]+)?|1([.]0+)?)$ ]]; then
  printf 'error-budget: threshold must be between 0 and 1: %s\n' "$THRESHOLD" >&2
  exit 2
fi

if ! row=$(docker compose exec -T postgres psql -X -v ON_ERROR_STOP=1 -U enten -d enten -t -A -c "
  SELECT
    count(*) FILTER (WHERE state = 'done'),
    count(*) FILTER (WHERE state = 'failed')
  FROM jobs
  WHERE updated_at > now() - interval '24 hours'
    AND state IN ('done', 'failed')
"); then
  printf '%s\n' 'error-budget: database query failed' >&2
  exit 1
fi

IFS='|' read -r done_count failed_count extra <<<"$row"
if [[ -n "${extra:-}" || ! "${done_count:-}" =~ ^[0-9]+$ || ! "${failed_count:-}" =~ ^[0-9]+$ ]]; then
  printf 'error-budget: malformed database result: %q\n' "$row" >&2
  exit 1
fi

total=$((done_count + failed_count))
if ((total == 0)); then
  printf 'error-budget: no terminal jobs in last 24h; cannot evaluate threshold %s\n' "$THRESHOLD" >&2
  exit 1
fi

rate=$(awk -v done="$done_count" -v total="$total" 'BEGIN { printf "%.4f", done / total }')
printf 'error-budget: done=%s failed=%s total=%s rate=%s threshold=%s\n' \
  "$done_count" "$failed_count" "$total" "$rate" "$THRESHOLD"

if awk -v done="$done_count" -v total="$total" -v threshold="$THRESHOLD" \
  'BEGIN { exit !((done / total) >= threshold) }'; then
  exit 0
fi

printf '%s\n' 'error-budget: completion rate below threshold' >&2
exit 1
