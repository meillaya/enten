#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# scripts/check-coverage.sh
#
# Cross-language coverage gate. Runs the Python and TypeScript suites
# with their respective coverage reporters and enforces an 80% threshold
# in both. Fails fast on the first non-green run.
#
# Usage:
#   bash scripts/check-coverage.sh           # default: 80% gate
#   ENTEN_COV_THRESHOLD=90 bash scripts/check-coverage.sh
#
# Exits 0 on success, 1 on any failure.
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

THRESHOLD="${ENTEN_COV_THRESHOLD:-80}"
UV="${UV:-uv}"
PNPM="${PNPM:-pnpm}"

log() { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }
fail() { printf '\n\033[1;31mFAIL:\033[0m %s\n' "$*" >&2; exit 1; }

log "Python coverage gate (>= ${THRESHOLD}%)"
"$UV" run pytest \
    --cov=packages/python/enten/src/enten \
    --cov-branch \
    --cov-report=term-missing \
    --cov-fail-under="$THRESHOLD" \
    packages/python/enten/tests/ \
    || fail "Python coverage below ${THRESHOLD}%"

log "TypeScript coverage gate (>= ${THRESHOLD}%)"
"$PNPM" --filter @enten/app exec vitest run --coverage \
    --coverage.thresholds.lines="$THRESHOLD" \
    --coverage.thresholds.functions="$THRESHOLD" \
    --coverage.thresholds.branches="$THRESHOLD" \
    --coverage.thresholds.statements="$THRESHOLD" \
    || fail "TypeScript coverage below ${THRESHOLD}%"

log "Coverage gates passed (>= ${THRESHOLD}%)"
