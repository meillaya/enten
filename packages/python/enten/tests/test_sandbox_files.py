from __future__ import annotations

from pathlib import Path

import pytest

import enten.sandbox.files as sandbox_files
from enten.sandbox.files import SandboxFileLimitError


def test_collect_rejects_file_that_exceeds_per_file_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_files, "MAX_SANDBOX_FILE_BYTES", 4)
    (tmp_path / "large.txt").write_text("12345", encoding="utf-8")

    with pytest.raises(SandboxFileLimitError) as caught:
        sandbox_files.collect_repository_files(str(tmp_path))

    assert caught.value.limit_name == "file_bytes"
    assert caught.value.path == "large.txt"
    assert caught.value.actual == 5


def test_collect_rejects_aggregate_before_reading_past_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_files, "MAX_SANDBOX_TOTAL_BYTES", 7)
    (tmp_path / "a.txt").write_text("1234", encoding="utf-8")
    (tmp_path / "b.txt").write_text("5678", encoding="utf-8")

    with pytest.raises(SandboxFileLimitError) as caught:
        sandbox_files.collect_repository_files(str(tmp_path))

    assert caught.value.limit_name == "total_bytes"
    assert caught.value.actual == 8
    assert caught.value.limit == 7


def test_collect_rejects_excessive_file_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_files, "MAX_SANDBOX_FILE_COUNT", 1)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")

    with pytest.raises(SandboxFileLimitError) as caught:
        sandbox_files.collect_repository_files(str(tmp_path))

    assert caught.value.limit_name == "file_count"
    assert caught.value.actual == 2


def test_write_validates_entire_batch_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_files, "MAX_SANDBOX_TOTAL_BYTES", 5)

    with pytest.raises(SandboxFileLimitError):
        sandbox_files.write_sandbox_files(
            tmp_path,
            {"first.txt": "123", "second.txt": "456"},
        )

    assert list(tmp_path.iterdir()) == []


def test_validate_counts_encoded_utf8_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_files, "MAX_SANDBOX_FILE_BYTES", 3)

    with pytest.raises(SandboxFileLimitError) as caught:
        sandbox_files.validate_sandbox_files({"unicode.txt": "éé"})

    assert caught.value.actual == 4


def test_validate_allows_one_reserved_manifest_beyond_source_file_cap() -> None:
    files = {f"source/{index}.txt": "" for index in range(sandbox_files.MAX_SANDBOX_FILE_COUNT)}
    files[sandbox_files.TRUSTED_SOURCE_MANIFEST_PATH] = "{}"

    sandbox_files.validate_sandbox_files(files)


def test_validate_excludes_bounded_manifest_from_source_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_files, "MAX_SANDBOX_TOTAL_BYTES", 4)

    sandbox_files.validate_sandbox_files(
        {
            "source.txt": "1234",
            sandbox_files.TRUSTED_SOURCE_MANIFEST_PATH: "{}",
        }
    )
