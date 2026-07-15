from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run_junit_gate(tmp_path: Path, junit: str) -> subprocess.CompletedProcess[str]:
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        "cat > \"$ENTEN_REPORT_DIR/pytest-junit.xml\" <<'XML'\n"
        f"{junit}\n"
        "XML\n",
        encoding="utf-8",
    )
    fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IXUSR)
    environment = {
        **os.environ,
        "ENTEN_COVERAGE_SCOPE": "python",
        "ENTEN_REPORT_DIR": str(report_dir),
        "UV": str(fake_uv),
    }
    return subprocess.run(
        ["bash", "scripts/check-coverage.sh"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_junit_gate_rejects_an_unexpected_skip(tmp_path: Path) -> None:
    junit = """\
<testsuites>
  <testsuite tests="1" failures="0" errors="0" skipped="1">
    <testcase classname="tests.test_other" name="test_unexpected">
      <skipped message="unexpected dependency" />
    </testcase>
  </testsuite>
</testsuites>"""

    completed = _run_junit_gate(tmp_path, junit)

    assert completed.returncode == 1
    assert "unexpected skip" in completed.stderr


def test_junit_gate_allows_the_four_opt_in_demo_skips(tmp_path: Path) -> None:
    base_sha_case = (
        "test_wrong_sha_or_selector_is_rejected["
        "ENTEN_BASE_SHA-0000000000000000000000000000000000000000]"
    )
    selector_case = "test_wrong_sha_or_selector_is_rejected[ENTEN_TEST_SELECTOR-tests/test_cli.py]"
    junit = f"""\
<testsuites>
  <testsuite tests="4" failures="0" errors="0" skipped="4">
    <testcase classname="tests.test_demo_image"
      name="test_image_metadata_and_runtime_are_minimal">
      <skipped message="set ENTEN_RUN_DEMO_IMAGE_INTEGRATION=1 for the image contract" />
    </testcase>
    <testcase classname="tests.test_demo_image"
      name="{base_sha_case}">
      <skipped message="set ENTEN_RUN_DEMO_IMAGE_INTEGRATION=1 for the image contract" />
    </testcase>
    <testcase classname="tests.test_demo_image"
      name="{selector_case}">
      <skipped message="set ENTEN_RUN_DEMO_IMAGE_INTEGRATION=1 for the image contract" />
    </testcase>
    <testcase classname="tests.test_demo_image"
      name="test_three_fresh_hardened_networkless_runs_pass_under_budget">
      <skipped message="set ENTEN_RUN_DEMO_IMAGE_INTEGRATION=1 for the image contract" />
    </testcase>
  </testsuite>
</testsuites>"""

    completed = _run_junit_gate(tmp_path, junit)

    assert completed.returncode == 0, completed.stderr
    assert "skipped=4" in completed.stdout
