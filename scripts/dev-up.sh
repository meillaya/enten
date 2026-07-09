#!/usr/bin/env bash
# ─── enten — bring the local dev stack up ──────────────────────────────
# Usage:
#   scripts/dev-up.sh              # default: just Postgres + Jaeger
#   scripts/dev-up.sh --with-dev   # also apply the dev overlay (bind mounts)
#   scripts/dev-up.sh --with-app   # bring up Postgres + Jaeger + the `app`
#                                  # placeholder under the `app` profile
#   scripts/dev-up.sh --with-ollama  # add Ollama for local LLM runs
#
# All variants block until the requested services report `healthy` from
# their docker-compose healthchecks (`docker compose up --wait` does the
# polling; the per-service start_period gives Postgres + Jaeger room
# to boot). Exits non-zero if any service fails to become healthy.
#
# Cold start is ~10 s for Jaeger and ~15 s for Postgres on a Linux
# host with warm image caches. First pull of the pgvector image adds
# ~30 s; rerun the script and the warm cache is used.

set -euo pipefail

# Resolve the repo root so the script works regardless of cwd.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." &> /dev/null && pwd)"

# Override files: base is mandatory; dev/app/ollama are opt-in via flags.
COMPOSE_FILES=(-f "${REPO_ROOT}/docker-compose.yml")
USE_DEV=0
USE_APP=0
USE_OLLAMA=0

for arg in "$@"; do
  case "${arg}" in
    --with-dev)    USE_DEV=1 ;;
    --with-app)    USE_APP=1 ;;
    --with-ollama) USE_OLLAMA=1 ;;
    -h|--help)
      sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo "dev-up: unknown flag: ${arg}" >&2
      echo "dev-up: try --with-dev, --with-app, or --with-ollama" >&2
      exit 2
      ;;
  esac
done

if [[ "${USE_DEV}" -eq 1 ]]; then
  COMPOSE_FILES+=(-f "${REPO_ROOT}/docker-compose.dev.yml")
fi

# `--profile <name>` activates services gated behind that profile. We
# always pass `--profile` for non-default services; multiple --profile
# flags are supported.
PROFILES=()
if [[ "${USE_APP}"    -eq 1 ]]; then PROFILES+=(--profile app); fi
if [[ "${USE_OLLAMA}" -eq 1 ]]; then PROFILES+=(--profile ollama); fi

# Default service set: Postgres + Jaeger. When --with-app is passed we
# also ask for the `app` service explicitly; the profile flag just
# makes it eligible to start.
SERVICES=(postgres jaeger)
if [[ "${USE_APP}" -eq 1 ]]; then SERVICES+=(app); fi

echo "dev-up: starting ${SERVICES[*]} (dev=${USE_DEV}, app=${USE_APP}, ollama=${USE_OLLAMA})"

# `docker compose up -d --wait` blocks until each requested service's
# healthcheck reports healthy OR the global timeout elapses. The default
# timeout is unbounded; we set a sane 120 s cap so a stuck image pull
# fails fast instead of hanging the dev loop.
docker compose \
  "${COMPOSE_FILES[@]}" \
  "${PROFILES[@]}" \
  up -d --wait --wait-timeout 120 \
  "${SERVICES[@]}"

# Final post-condition check — also surfaces per-service state in the
# dev's terminal so a `docker compose ps` follow-up is rarely needed.
docker compose "${COMPOSE_FILES[@]}" "${PROFILES[@]}" ps --format json \
  | jq -rs '.[] | "  \(.Name): \(.State) (\(.Health))"'

echo "dev-up: ready"
echo "  Postgres:  postgresql://enten:enten@localhost:5432/enten"
echo "  Jaeger UI: http://localhost:16686"
echo "  OTLP:      http://localhost:4317 (gRPC)  http://localhost:4318 (HTTP)"
