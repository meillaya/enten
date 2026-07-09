#!/usr/bin/env bash
# ─── enten — tear the local dev stack down ─────────────────────────────
# Usage:
#   scripts/dev-down.sh            # drop the named pgdata volume too
#   scripts/dev-down.sh --keep-data  # keep the volume; fast restart
#
# By default we use `docker compose down -v` so a `dev-up.sh` after a
# `dev-down.sh` starts from a clean Postgres. Use `--keep-data` when
# you want to iterate without re-seeding fixtures (T08+ will populate
# the volume with Alembic migrations and fixture data).
#
# All active profiles are torn down — `app` and `ollama` (when started
# with --with-app / --with-ollama in dev-up.sh) are stopped alongside
# postgres + jaeger.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." &> /dev/null && pwd)"

COMPOSE_FILES=(-f "${REPO_ROOT}/docker-compose.yml")
VOLUME_FLAG="-v"

for arg in "$@"; do
  case "${arg}" in
    --keep-data)  VOLUME_FLAG="" ;;
    -h|--help)
      sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo "dev-down: unknown flag: ${arg}" >&2
      exit 2
      ;;
  esac
done

echo "dev-down: stopping all services (drop volume: ${VOLUME_FLAG:-no})"
docker compose "${COMPOSE_FILES[@]}" down ${VOLUME_FLAG}

echo "dev-down: done"
