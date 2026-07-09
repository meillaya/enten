#!/usr/bin/env bash
set -euo pipefail

# Roll back the initial migration by dropping all tables in reverse order.

docker compose exec -T postgres psql -U enten -d enten -c "
DROP TABLE IF EXISTS sandbox_runs CASCADE;
DROP TABLE IF EXISTS fixtures CASCADE;
DROP TABLE IF EXISTS llm_calls CASCADE;
DROP TABLE IF EXISTS pr_reviews CASCADE;
DROP TABLE IF EXISTS jobs CASCADE;
"
