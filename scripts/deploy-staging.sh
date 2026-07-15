#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." &>/dev/null && pwd)"

require_template_line() {
  local file="$1"
  local expected="$2"
  if ! grep --fixed-strings --line-regexp --quiet "$expected" "${REPO_ROOT}/${file}"; then
    printf 'deploy-staging: %s is missing required topology: %s\n' "$file" "$expected" >&2
    return 1
  fi
}

check_templates() {
  require_template_line fly.toml '  release_command = "python -m enten.migrations apply --baseline-existing"'
  require_template_line fly.toml '  web = "node packages/ts/app/dist/server.js"'
  require_template_line fly.toml '  worker = "enten worker"'
  require_template_line railway.toml 'preDeployCommand = "python -m enten.migrations apply --baseline-existing"'
  require_template_line railway.toml 'startCommand = "node packages/ts/app/dist/server.js"'
  require_template_line railway.worker.toml 'startCommand = "enten worker"'
  printf '%s\n' 'deploy-staging: templates are structurally consistent; no deployment was attempted'
}

case "${1:-}" in
  --check-templates)
    check_templates
    ;;
  -h|--help)
    printf '%s\n' \
      'Usage: scripts/deploy-staging.sh --check-templates' \
      'Hosted files are topology templates, not validated deployment targets.'
    ;;
  "")
    check_templates
    printf '%s\n' \
      'deploy-staging: Fly.io and Railway are not a supported deployment target for the Docker verifier' \
      'deploy-staging: use the dedicated self-hosted Docker topology documented in docs/deploy.md' >&2
    exit 2
    ;;
  *)
    printf 'deploy-staging: unknown argument: %s\n' "$1" >&2
    exit 2
    ;;
esac
