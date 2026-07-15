#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." &>/dev/null && pwd)"
VOLUME_FLAG=""

for arg in "$@"; do
  case "${arg}" in
    --drop-data) VOLUME_FLAG="--volumes" ;;
    -h|--help)
      printf '%s\n' \
        'Usage: scripts/dev-down.sh [--drop-data]' \
        'Stops project services and preserves database/model volumes by default.'
      exit 0
      ;;
    *)
      printf 'dev-down: unknown flag: %s\n' "${arg}" >&2
      exit 2
      ;;
  esac
done

docker compose -f "${REPO_ROOT}/docker-compose.yml" --profile '*' down \
  --remove-orphans ${VOLUME_FLAG}
