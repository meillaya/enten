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
  "$UV" run --project packages/python/enten --extra test pytest \
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
allowed_skips = {
    (
        "tests.test_demo_image",
        "test_image_metadata_and_runtime_are_minimal",
        "set ENTEN_RUN_DEMO_IMAGE_INTEGRATION=1 for the image contract",
    ),
    (
        "tests.test_demo_image",
        "test_wrong_sha_or_selector_is_rejected[ENTEN_BASE_SHA-0000000000000000000000000000000000000000]",
        "set ENTEN_RUN_DEMO_IMAGE_INTEGRATION=1 for the image contract",
    ),
    (
        "tests.test_demo_image",
        "test_wrong_sha_or_selector_is_rejected[ENTEN_TEST_SELECTOR-tests/test_cli.py]",
        "set ENTEN_RUN_DEMO_IMAGE_INTEGRATION=1 for the image contract",
    ),
    (
        "tests.test_demo_image",
        "test_three_fresh_hardened_networkless_runs_pass_under_budget",
        "set ENTEN_RUN_DEMO_IMAGE_INTEGRATION=1 for the image contract",
    ),
}
skips = []
for testcase in root.iter("testcase"):
    skipped = testcase.find("skipped")
    if skipped is not None:
        skips.append(
            (
                testcase.attrib.get("classname", ""),
                testcase.attrib.get("name", ""),
                skipped.attrib.get("message", ""),
            )
        )
unexpected_skips = [skip for skip in skips if skip not in allowed_skips]
duplicate_skips = len(skips) != len(set(skips))
if tests == 0 or failures or errors or pytest_exit or unexpected_skips or duplicate_skips:
    print(
        f"FAIL: pytest exit={pytest_exit} tests={tests} failures={failures} "
        f"errors={errors} unexpected skip={unexpected_skips} duplicate_skips={duplicate_skips}",
        file=sys.stderr,
    )
    raise SystemExit(1)
print(f"JUnit gate passed: tests={tests} failures=0 errors=0 skipped={len(skips)}")
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
