"""Integration tests for the Semgrep static-analysis wrapper.

These tests spin up a real Docker sandbox using the ``returntocorp/semgrep``
image and run Semgrep against tiny fixture repositories. They are skipped
automatically when Docker is unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from enten.sandbox.docker import DockerSandbox
from enten.static_analysis.semgrep import SemgrepRuleError, run_semgrep

REPO_ROOT = Path(__file__).resolve().parents[4]

SEMGREP_IMAGE = "returntocorp/semgrep:latest"
SEMGREP_TMPFS = {
    "/tmp": "rw,noexec,nosuid,size=100m",
    "/home/semgrep": "rw,noexec,nosuid,size=100m,uid=1000",
}


def _docker_available() -> bool:
    return shutil.which("docker") is not None and subprocess.run(
        ["docker", "info"],
        capture_output=True,
        check=False,
    ).returncode == 0


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker daemon not available",
)


@pytest.fixture
def semgrep_provider() -> DockerSandbox:
    return DockerSandbox(
        image=SEMGREP_IMAGE,
        network="bridge",
        tmpfs=SEMGREP_TMPFS,
    )


@pytest.mark.slow
@pytest.mark.network
@pytest.mark.asyncio
async def test_python_broad_except_rule_fires(
    tmp_path,
    semgrep_provider,
):
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    (src / "bad.py").write_text(
        "try:\n    pass\nexcept:\n    pass\n",
        encoding="utf-8",
    )

    rule = REPO_ROOT / "rules/python/enten-broad-except.yaml"
    result = await run_semgrep(
        semgrep_provider,
        str(repo),
        "python",
        [str(rule)],
    )

    assert result.exit_code == 1
    broad = [f for f in result.findings if f.check_id.endswith("enten-broad-except")]
    assert len(broad) == 1
    assert broad[0].path.endswith("bad.py")
    assert broad[0].severity == "WARNING"


@pytest.mark.slow
@pytest.mark.network
@pytest.mark.asyncio
async def test_typescript_no_any_rule_fires(
    tmp_path,
    semgrep_provider,
):
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    (src / "bad.ts").write_text(
        "let x: any = 1;\n",
        encoding="utf-8",
    )

    rule = REPO_ROOT / "rules/typescript/enten-no-any.yaml"
    result = await run_semgrep(
        semgrep_provider,
        str(repo),
        "typescript",
        [str(rule)],
    )

    assert result.exit_code == 1
    no_any = [f for f in result.findings if f.check_id.endswith("enten-no-any")]
    assert len(no_any) == 1
    assert no_any[0].path.endswith("bad.ts")
    assert no_any[0].severity == "WARNING"


@pytest.mark.slow
@pytest.mark.network
@pytest.mark.asyncio
async def test_bad_rule_yaml_raises(tmp_path, semgrep_provider):
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    (src / "ok.py").write_text("print(1)\n", encoding="utf-8")

    bad_rule = repo / "bad.yaml"
    bad_rule.write_text(
        """rules:
  - id: bad
    languages: [python]
    message: "bad"
    severity: WARNING
    pattern: "bad: yaml: :"
""",
        encoding="utf-8",
    )

    with pytest.raises(SemgrepRuleError):
        await run_semgrep(
            semgrep_provider,
            str(repo),
            "python",
            [str(bad_rule)],
        )
