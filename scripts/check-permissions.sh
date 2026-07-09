#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# scripts/check-permissions.sh
#
# Audit GitHub App permission scope. The bot must never request write
# access to repository contents or GitHub Actions. This script scans the
# manifest, permission documentation, and deployment configuration for
# forbidden scope strings and exits non-zero if any are found.
#
# Usage:
#   bash scripts/check-permissions.sh
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

FORBIDDEN=(
    "contents: write"
    "contents.write"
    "actions: write"
)

FILES=(
    packages/ts/app/github-app.yml
    docs/permissions.md
    fly.toml
    railway.toml
    Dockerfile
)

fail=0

for file in "${FILES[@]}"; do
    if [[ ! -f "$file" ]]; then
        continue
    fi
    for pattern in "${FORBIDDEN[@]}"; do
        # Allow the patterns to appear in documentation that explicitly
        # labels them as forbidden or denied; flag them everywhere else.
        matches=$(grep -F "$pattern" "$file" 2>/dev/null | grep -Evi 'forbidden|denied|does not have|must not|why it is denied' || true)
        if [[ -n "$matches" ]]; then
            echo "FORBIDDEN: '$pattern' found in $file" >&2
            echo "$matches" >&2
            fail=1
        fi
    done
done

if [[ "$fail" -eq 1 ]]; then
    echo "Permission audit failed: forbidden scope detected." >&2
    exit 1
fi

echo "Permission audit passed: no forbidden scope found."
