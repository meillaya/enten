#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:=postgresql+asyncpg://enten:enten@localhost:${POSTGRES_HOST_PORT:-5432}/enten}"

uv run --project packages/python/enten python -m enten.migrations \
    --database-url "$DATABASE_URL" \
    --migrations migrations \
    apply --baseline-existing
