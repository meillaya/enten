#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

THRESHOLD="${ENTEN_COV_THRESHOLD:-80}"
SCOPE="${ENTEN_COVERAGE_SCOPE:-all}"
UV="${UV:-uv}"
PNPM="${PNPM:-pnpm}"
REPORT_DIR="${ENTEN_REPORT_DIR:-/tmp/enten-coverage}"
mkdir -p "$REPORT_DIR"
read -r -a PYTEST_PATHS <<< "${ENTEN_PYTEST_PATHS:-tests packages/python/enten/tests}"

run_python() {
  local junit="$REPORT_DIR/pytest-junit.xml"
  set +e
  "$UV" run --project packages/python/enten pytest \
    --junitxml="$junit" \
    --cov=packages/python/enten/src/enten \
    --cov-branch \
    --cov-report=term-missing \
    --cov-report="xml:$REPORT_DIR/python-coverage.xml" \
    --cov-fail-under="$THRESHOLD" \
    "${PYTEST_PATHS[@]}"
  local pytest_exit=$?
  set -e

  python - "$junit" "$pytest_exit" <<'PY'
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

report = Path(sys.argv[1])
pytest_exit = int(sys.argv[2])
if not report.is_file():
    print(f"FAIL: pytest did not produce JUnit XML: {report}", file=sys.stderr)
    raise SystemExit(1)
root = ET.parse(report).getroot()
suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
tests = sum(int(suite.attrib.get("tests", "0")) for suite in suites)
failures = sum(int(suite.attrib.get("failures", "0")) for suite in suites)
errors = sum(int(suite.attrib.get("errors", "0")) for suite in suites)
if tests == 0 or failures or errors or pytest_exit:
    print(
        f"FAIL: pytest exit={pytest_exit} tests={tests} failures={failures} errors={errors}",
        file=sys.stderr,
    )
    raise SystemExit(1)
print(f"JUnit gate passed: tests={tests} failures=0 errors=0")
PY
}

run_typescript() {
  "$PNPM" --filter @enten/app exec vitest run --coverage \
    --coverage.thresholds.lines="$THRESHOLD" \
    --coverage.thresholds.functions="$THRESHOLD" \
    --coverage.thresholds.branches="$THRESHOLD" \
    --coverage.thresholds.statements="$THRESHOLD"
}

case "$SCOPE" in
  all)
    run_python
    run_typescript
    ;;
  python)
    run_python
    ;;
  typescript)
    run_typescript
    ;;
  *)
    printf 'FAIL: unknown ENTEN_COVERAGE_SCOPE=%s\n' "$SCOPE" >&2
    exit 2
    ;;
esac
