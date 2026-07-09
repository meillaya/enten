#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# scripts/deploy-staging.sh
#
# Deploy the production image to Fly.io staging.
#
# Prerequisites:
#   - flyctl installed (https://fly.io/docs/hands-on/install-flyctl/)
#   - flyctl auth required (run `flyctl auth login`)
#   - secrets set: DATABASE_URL, GITHUB_APP_ID, GITHUB_WEBHOOK_SECRET,
#     OPENAI_API_KEY, E2B_API_KEY
#
# Usage:
#   bash scripts/deploy-staging.sh
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." &> /dev/null && pwd)"

cd "$REPO_ROOT"

# flyctl auth required
fly deploy
