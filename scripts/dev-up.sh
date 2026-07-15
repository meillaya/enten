#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." &>/dev/null && pwd)"
COMPOSE_FILES=(-f "${REPO_ROOT}/docker-compose.yml")
PROFILES=()
SERVICES=(postgres jaeger migrate web worker)

for arg in "$@"; do
  case "${arg}" in
    --infra-only) SERVICES=(postgres jaeger) ;;
    --with-dev) COMPOSE_FILES+=(-f "${REPO_ROOT}/docker-compose.dev.yml") ;;
    --with-ollama) PROFILES+=(--profile ollama); SERVICES+=(ollama) ;;
    -h|--help)
      printf '%s\n' \
        'Usage: scripts/dev-up.sh [--infra-only] [--with-dev] [--with-ollama]' \
        'Starts the complete migrated web and worker stack by default.'
      exit 0
      ;;
    *)
      printf 'dev-up: unknown flag: %s\n' "${arg}" >&2
      exit 2
      ;;
  esac
done

if [[ " ${SERVICES[*]} " == *" worker "* ]]; then
  if [[ ! -S /var/run/docker.sock ]]; then
    printf '%s\n' 'dev-up: /var/run/docker.sock is required by the Docker verifier' >&2
    exit 2
  fi
  export DOCKER_GID="${DOCKER_GID:-$(stat -c '%g' /var/run/docker.sock)}"
  docker compose "${COMPOSE_FILES[@]}" "${PROFILES[@]}" config --format json |
    python3 -c '
import json
import os
import sys

worker = json.load(sys.stdin)["services"]["worker"]
environment = worker["environment"]
required = ("GITHUB_APP_ID", "GITHUB_WEBHOOK_SECRET")
missing = [name for name in required if not environment.get(name)]
if missing:
    raise SystemExit("dev-up: missing worker configuration: " + ", ".join(missing))
if environment["GITHUB_APP_ID"] == "1":
    raise SystemExit("dev-up: replace the placeholder GITHUB_APP_ID")
if environment["GITHUB_WEBHOOK_SECRET"] == "local-development-only":
    raise SystemExit("dev-up: replace the placeholder GITHUB_WEBHOOK_SECRET")
key_mount = next(
    volume for volume in worker["volumes"]
    if volume["target"] == "/run/secrets/github-app.pem"
)
if not os.path.isfile(key_mount["source"]):
    raise SystemExit(
        "dev-up: GITHUB_APP_PRIVATE_KEY_PATH must name a readable PEM file"
    )
'
fi

docker compose "${COMPOSE_FILES[@]}" "${PROFILES[@]}" up \
  --detach --build --wait --wait-timeout 180 "${SERVICES[@]}"
docker compose "${COMPOSE_FILES[@]}" "${PROFILES[@]}" ps
printf '%s\n' \
  'enten is ready' \
  '  Web:       http://127.0.0.1:3000/health' \
  '  Jaeger UI: http://127.0.0.1:16686' \
  '  OTLP:      127.0.0.1:14317 (gRPC), 127.0.0.1:14318 (HTTP)'
