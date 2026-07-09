#!/usr/bin/env bash
# ─── enten — acceptance-criteria healthcheck ─────────────────────────────
#
# Runs the assertions from the F1/F2/F3 acceptance criteria and exits 0
# only if every check passes. Each line is prefixed with [OK] or [FAIL].
#
# Usage:
#   bash scripts/healthcheck-all.sh
# ─────────────────────────────────────────────────────────────────────────
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

UV="${UV:-uv}"
PNPM="${PNPM:-pnpm}"
FAILED=0

check() {
  local name="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "[OK] ${name}"
  else
    echo "[FAIL] ${name}"
    FAILED=1
  fi
}

# ─── F1: Plan compliance ─────────────────────────────────────────────────

check "F1: determinism guarantee (no LLM in verification)" \
  bash -c '! grep -rE "\.(complete|stream)\(" packages/python/enten/src/enten/verification/'

check "F1: patch safety (no contents:write)" \
  bash -c '! grep -rEn "contents:\s*write" packages/ scripts/ fly.toml railway.toml 2>/dev/null'

check "F1: patch safety (no branch push in patch/)" \
  bash -c '! grep -rEn "apply_to_branch|push\([^)]*branch" packages/python/enten/src/enten/patch/'

check "F1: prompt injection defense" \
  bash -c 'grep -rE "<untrusted>" packages/python/enten/src/enten/llm/ packages/python/enten/prompts/'

check "F1: eval harness + fixture corpus" \
  bash -c 'test -f scripts/eval-threshold-check.py && [[ $(find tests/fixtures/prs -mindepth 2 -maxdepth 2 -type d 2>/dev/null | wc -l) -ge 10 ]]'

check "F1: cost tracking per PR" \
  bash -c 'grep -q "cost_usd" packages/python/enten/src/enten/queue/models.py'

check "F1: idempotency key" \
  bash -c 'grep -q "idempotency_key" packages/python/enten/src/enten/queue/models.py packages/ts/app/src/queue/jobs.ts'

check "F1: observability (OTel tracer)" \
  bash -c 'test -f packages/python/enten/src/enten/otel.py && grep -q "tracer" packages/python/enten/src/enten/worker.py'

check "F1: two LLM implementations" \
  bash -c 'test -f packages/python/enten/src/enten/llm/openai_compat.py && test -f packages/python/enten/src/enten/llm/ollama.py'

check "F1: two sandbox implementations" \
  bash -c 'test -f packages/python/enten/src/enten/sandbox/docker.py && test -f packages/python/enten/src/enten/sandbox/e2b.py'

check "F1: flaky test handler" \
  bash -c 'test -f packages/python/enten/src/enten/verification/flaky.py'

check "F1: bot ignores own PRs" \
  bash -c 'test -f packages/python/enten/src/enten/github/bot_self.py'

check "F1: TDD + eval split" \
  bash -c 'test -d packages/python/enten/tests && [[ $(find packages/python/enten/tests -name "test_*.py" | wc -l) -ge 1 ]] && test -f packages/python/enten/src/enten/eval/__init__.py'

# Must NOT Have greps from the plan compliance audit.
check "F1-MNH: no Daytona references" \
  bash -c '! grep -rEn "Daytona" packages/ scripts/ 2>/dev/null'

check "F1-MNH: no secrets in evidence files" \
  bash -c '! grep -rEn "LANGCHAIN_API_KEY|GITHUB_TOKEN|OPENAI_API_KEY" .omo/evidence/ 2>/dev/null'

check "F1-MNH: no debug prints in verification" \
  bash -c '! grep -rEn "console\.log|print\(.*Hello" packages/python/enten/src/enten/verification/'

check "F1-MNH: no over-fancy / AI-slop marker" \
  bash -c '! grep -rEn "over-fancy" packages/'

check "F1: evidence files exist (>=84)" \
  bash -c '[[ $(find .omo/evidence -maxdepth 1 -type f -name "task-*.txt" 2>/dev/null | wc -l) -ge 84 ]]'

# ─── F2: Code quality ────────────────────────────────────────────────────

check "F2: Python build" \
  bash -c "cd packages/python/enten && \"$UV\" run python -c 'from enten.cli import app; print(app)'"

check "F2: TypeScript build" \
  bash -c '"$PNPM" -r tsc --noEmit'

check "F2: Python lint" \
  bash -c '"$UV" run ruff check packages/python/enten/src/enten'

check "F2: TypeScript lint" \
  bash -c '"$PNPM" -r run lint'

check "F2: Coverage gate" \
  bash -c 'bash scripts/check-coverage.sh'

# ─── F3: Real QA ─────────────────────────────────────────────────────────

check "F3: task evidence coverage (>=42 tasks)" \
  bash -c 'for n in $(seq 1 42); do [[ $(find .omo/evidence -maxdepth 1 -type f -name "task-${n}-*.txt" 2>/dev/null | wc -l) -ge 1 ]] || exit 1; done'

check "F3: edge-case fixtures present" \
  bash -c 'test -d tests/fixtures/prs/prompt_injection && test -d tests/fixtures/prs/security && [[ $(find tests/fixtures/prs/prompt_injection tests/fixtures/prs/security -name expected_findings.json | wc -l) -ge 2 ]]'

run_eval_check() {
  cd packages/python/enten
  "$UV" run enten eval \
    --corpus tests/fixtures/prs/ \
    --report /tmp/enten-healthcheck-eval.json \
    --stub-llm
  python -c 'import json; data=json.load(open("/tmp/enten-healthcheck-eval.json")); assert len(data.get("by_kind", {})) >= 1'
}

check "F3: eval integration" run_eval_check

# ─── Final verdict ───────────────────────────────────────────────────────

if [[ "$FAILED" -eq 0 ]]; then
  echo "[OK] All checks passed"
  exit 0
fi

echo "[FAIL] One or more healthcheck assertions failed"
exit 1
