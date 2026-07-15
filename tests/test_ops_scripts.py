from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _run_script(
    script: str,
    *,
    path: Path,
    arguments: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / script), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        env={**os.environ, "PATH": f"{path}:{os.environ['PATH']}"},
        text=True,
    )


def test_error_budget_fails_when_database_query_fails(tmp_path: Path) -> None:
    _write_executable(tmp_path / "docker", "#!/bin/sh\nexit 42\n")

    completed = _run_script("error-budget-check.sh", path=tmp_path)

    assert completed.returncode != 0


def test_error_budget_fails_without_terminal_jobs(tmp_path: Path) -> None:
    _write_executable(tmp_path / "docker", "#!/bin/sh\nprintf '0|0\\n'\n")

    completed = _run_script("error-budget-check.sh", path=tmp_path)

    assert completed.returncode != 0
    assert "no terminal jobs" in completed.stderr


def test_error_budget_counts_only_real_terminal_states(tmp_path: Path) -> None:
    _write_executable(tmp_path / "docker", "#!/bin/sh\nprintf '99|1\\n'\n")

    completed = _run_script("error-budget-check.sh", path=tmp_path)

    assert completed.returncode == 0
    assert "done=99 failed=1 total=100" in completed.stdout
    assert "errored" not in completed.stdout


def test_error_budget_fails_below_threshold(tmp_path: Path) -> None:
    _write_executable(tmp_path / "docker", "#!/bin/sh\nprintf '98|2\\n'\n")

    completed = _run_script("error-budget-check.sh", path=tmp_path)

    assert completed.returncode != 0
    assert "below threshold" in completed.stderr


def test_runtime_health_checks_running_services_database_and_edge(tmp_path: Path) -> None:
    _write_executable(
        tmp_path / "docker",
        "#!/bin/sh\n"
        'if [ "$1" = compose ] && [ "$2" = ps ]; then\n'
        "  printf 'postgres\\njaeger\\nweb\\nworker\\n'\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = compose ] && [ "$2" = exec ]; then\n'
        "  exit 0\n"
        "fi\n"
        "exit 9\n",
    )
    _write_executable(tmp_path / "curl", "#!/bin/sh\nexit 0\n")

    completed = _run_script("healthcheck-all.sh", path=tmp_path)

    assert completed.returncode == 0
    assert "runtime health passed" in completed.stdout
    assert "make check" not in (ROOT / "scripts" / "healthcheck-all.sh").read_text(encoding="utf-8")


def test_runtime_health_fails_when_worker_is_not_running(tmp_path: Path) -> None:
    _write_executable(
        tmp_path / "docker",
        "#!/bin/sh\n"
        'if [ "$1" = compose ] && [ "$2" = ps ]; then\n'
        "  printf 'postgres\\njaeger\\nweb\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 9\n",
    )
    _write_executable(tmp_path / "curl", "#!/bin/sh\nexit 0\n")

    completed = _run_script("healthcheck-all.sh", path=tmp_path)

    assert completed.returncode != 0
    assert "worker" in completed.stderr


def test_staging_deploy_refuses_unvalidated_hosted_release(tmp_path: Path) -> None:
    completed = _run_script("deploy-staging.sh", path=tmp_path)

    assert completed.returncode != 0
    assert "not a supported deployment target" in completed.stderr
    assert "fly deploy" not in (ROOT / "scripts" / "deploy-staging.sh").read_text(encoding="utf-8")


def test_staging_templates_can_be_checked_without_deploying(tmp_path: Path) -> None:
    completed = _run_script("deploy-staging.sh", path=tmp_path, arguments=("--check-templates",))

    assert completed.returncode == 0
    assert "templates are structurally consistent" in completed.stdout
