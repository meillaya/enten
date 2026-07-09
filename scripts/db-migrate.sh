#!/usr/bin/env bash
set -euo pipefail

# Apply the initial SQL migration to the local Postgres+pgvector container.

docker compose exec -T postgres mkdir -p /workspace/migrations
docker compose cp migrations/0001_init.sql postgres:/workspace/migrations/0001_init.sql
docker compose exec -T postgres psql -U enten -d enten -f /workspace/migrations/0001_init.sql
