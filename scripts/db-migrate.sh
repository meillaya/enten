#!/usr/bin/env bash
set -euo pipefail

docker compose exec -T postgres mkdir -p /workspace/migrations

for migration in migrations/0001_init.sql migrations/0002_pgvector.sql; do
    docker compose cp "$migration" "postgres:/workspace/$migration"
    docker compose exec -T postgres psql -U enten -d enten -f "/workspace/$migration"
done
