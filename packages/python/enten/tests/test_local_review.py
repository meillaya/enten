from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from enten.cli import _local_content_sha
from enten.review.local import LocalFindingsGenerator, discover_source_files
from enten.review.run import Proposal


def test_discovery_skips_symlinked_source_without_following_host_target(
    tmp_path: Path,
) -> None:
    # Given
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "safe.py").write_text("value = 1\n", encoding="utf-8")
    host_source = tmp_path / "host.py"
    host_source.write_text('eval("host input")\n', encoding="utf-8")
    (repository / "linked.py").symlink_to(host_source)

    # When
    discovered = discover_source_files(repository)

    # Then
    assert discovered == ("safe.py",)


@pytest.mark.anyio
async def test_local_generator_skips_source_replaced_by_symlink(tmp_path: Path) -> None:
    # Given
    repository = tmp_path / "repository"
    repository.mkdir()
    host_source = tmp_path / "host.py"
    host_source.write_text('eval("host input")\n', encoding="utf-8")
    (repository / "reviewed.py").symlink_to(host_source)
    proposal = Proposal(
        repository_path=repository,
        files=("reviewed.py",),
        verification_command=("python", "-c", "pass"),
    )

    # When
    generated = await LocalFindingsGenerator().generate(proposal)

    # Then
    assert generated.findings == ()


def test_local_content_sha_ignores_gitignored_generated_state(tmp_path: Path) -> None:
    # Given
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(
        ("git", "init", "--quiet", str(repository)),
        check=True,
        capture_output=True,
    )
    (repository / ".gitignore").write_text("generated/\n", encoding="utf-8")
    (repository / "app.py").write_text("value = 1\n", encoding="utf-8")
    generated = repository / "generated"
    generated.mkdir()
    artifact = generated / "result.json"
    artifact.write_text('{"run": 1}\n', encoding="utf-8")
    initial = _local_content_sha(repository)

    # When
    artifact.write_text('{"run": 2}\n', encoding="utf-8")

    # Then
    assert _local_content_sha(repository) == initial
