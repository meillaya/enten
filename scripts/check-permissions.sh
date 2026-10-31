#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MANIFEST="${PERMISSIONS_MANIFEST:-packages/ts/app/github-app.yml}"
if [[ ! -f "$MANIFEST" ]]; then
  printf 'FAIL: GitHub App manifest is missing: %s\n' "$MANIFEST" >&2
  exit 1
fi

mapfile -t permissions < <(
  awk '
    /^default_permissions:[[:space:]]*$/ { in_permissions=1; next }
    in_permissions && /^[^[:space:]]/ { exit }
    in_permissions && /^  [a-z_]+:[[:space:]]+[^[:space:]]+[[:space:]]*$/ {
      key=$1; value=$2; sub(/:$/, "", key); print key ":" value
    }
  ' "$MANIFEST"
)

expected=("checks:write" "contents:read" "metadata:read" "pull_requests:write")
mapfile -t actual < <(printf '%s\n' "${permissions[@]}" | sort)
mapfile -t wanted < <(printf '%s\n' "${expected[@]}" | sort)
if [[ "$(printf '%s\n' "${actual[@]}")" != "$(printf '%s\n' "${wanted[@]}")" ]]; then
  printf 'FAIL: GitHub App permissions differ from the reviewed least-privilege set\n' >&2
  printf 'Expected:\n  %s\n' "${wanted[*]}" >&2
  printf 'Actual:\n  %s\n' "${actual[*]:-none}" >&2
  exit 1
fi

printf 'Permission gate passed: checks:write, contents:read, metadata:read, pull_requests:write.\n'
