#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." &>/dev/null && pwd)"
cd "$REPO_ROOT"

running_services=$(docker compose ps --services --status running)
for service in postgres jaeger web worker; do
  if ! grep --fixed-strings --line-regexp --quiet "$service" <<<"$running_services"; then
    printf 'runtime health: required service is not running: %s\n' "$service" >&2
    exit 1
  fi
done

docker compose exec -T postgres pg_isready -U enten -d enten >/dev/null
curl --fail --silent --show-error \
  "${ENTEN_WEB_HEALTH_URL:-http://127.0.0.1:3000/health}" >/dev/null

printf '%s\n' 'runtime health passed: postgres, jaeger, web, and worker are running; database and edge probes succeeded'
