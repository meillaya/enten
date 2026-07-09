"""Tests for the tree-sitter multi-language indexer."""

from __future__ import annotations

import subprocess

import pytest

from enten.indexer import Chunk, index_diff, index_repo


def _write_python_fixture(path: object, lines: int) -> None:
    """Write a deterministic Python file with *lines* total lines."""
    func_count = lines // 20
    with path.open("w", encoding="utf-8") as fh:
        fh.write('"""A generated Python fixture for indexer tests."""\n\n')
        for i in range(func_count):
            fh.write(f"def function_{i:04d}(value: int) -> int:\n")
            fh.write(f'    """Add {i} to *value*."""\n')
            fh.write("    result = value + 1\n")
            fh.write("    for _ in range(3):\n")
            fh.write("        result += 1\n")
            fh.write("    return result\n\n")


def _write_tsx_fixture(path: object) -> None:
    """Write a TSX file with two exported React components."""
    path.write_text(
        "export function Header({ title }: { title: string }) {\n"
        "  return <header>{title}</header>;\n"
        "}\n\n"
        "export const Footer = () => {\n"
        "  return <footer>goodbye</footer>;\n"
        "};\n",
        encoding="utf-8",
    )


def test_index_repo_python_deterministic(tmp_path: pytest.TempPathFactory) -> None:
    """A 1k-line Python fixture produces deterministic chunk IDs."""
    repo = tmp_path / "py_repo"
    repo.mkdir()
    _write_python_fixture(repo / "fixture.py", 1000)

    chunks = index_repo(str(repo), "python")
    assert 30 <= len(chunks) <= 200, f"expected 30-200 chunks, got {len(chunks)}"

    # Re-index an identical copy and compare IDs.
    repo2 = tmp_path / "py_repo_copy"
    repo2.mkdir()
    _write_python_fixture(repo2 / "fixture.py", 1000)
    chunks2 = index_repo(str(repo2), "python")
    assert [c.chunk_id for c in chunks] == [c.chunk_id for c in chunks2]

    # Sanity: every chunk is a pydantic Chunk with required fields.
    for chunk in chunks:
        assert isinstance(chunk, Chunk)
        assert chunk.lang == "python"
        assert chunk.scope_kind in ("function", "class", "block")
        assert chunk.start_line <= chunk.end_line
        assert chunk.start_byte < chunk.end_byte


def test_index_repo_typescript_components(tmp_path: pytest.TempPathFactory) -> None:
    """A TSX file with two exported components yields at least two chunks."""
    repo = tmp_path / "ts_repo"
    repo.mkdir()
    _write_tsx_fixture(repo / "components.tsx")

    chunks = index_repo(str(repo), "typescript")
    assert len(chunks) >= 2, f"expected >= 2 chunks, got {len(chunks)}"
    names = {s.name for c in chunks for s in c.symbols}
    assert "Header" in names or "Footer" in names


def test_index_repo_skips_oversized_file(
    tmp_path: pytest.TempPathFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A 200k-line file is skipped with a warning and produces zero chunks."""
    repo = tmp_path / "big_repo"
    repo.mkdir()
    big = repo / "huge.py"
    with big.open("w", encoding="utf-8") as fh:
        for i in range(200_000):
            fh.write(f"x = {i}\n")

    chunks = index_repo(str(repo), "python")
    captured = capsys.readouterr()

    assert chunks == []
    assert "skipping oversized file" in captured.out


def test_index_diff_only_changed_files(tmp_path: pytest.TempPathFactory) -> None:
    """index_diff returns chunks only for files changed between commits."""
    repo = tmp_path / "git_repo"
    repo.mkdir()
    (repo / "a.py").write_text("def one():\n    pass\n", encoding="utf-8")
    (repo / "b.py").write_text("def two():\n    pass\n", encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    (repo / "b.py").write_text("def two():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "head"], cwd=repo, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    chunks = index_diff(str(repo), "python", base, head)
    paths = {c.path for c in chunks}
    assert paths == {"b.py"}
