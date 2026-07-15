from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

_GENERATED_PARTS = frozenset(
    {
        ".enten",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "htmlcov",
        "node_modules",
        "out",
    }
)


def local_content_sha(repository: Path, store_path: Path | None = None) -> str:
    digest = hashlib.sha256()
    digest.update(b"enten.local-content-sha.v1\0")
    excluded_store = _store_relative_to_repository(repository, store_path)
    for relative in _local_content_paths(repository):
        if _GENERATED_PARTS.intersection(relative.parts) or relative == excluded_store:
            continue
        source = repository / relative
        if source.is_symlink() or not source.is_file():
            continue
        relative_bytes = relative.as_posix().encode()
        content = source.read_bytes()
        digest.update(b"path\0")
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update(b"content\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _local_content_paths(repository: Path) -> tuple[Path, ...]:
    try:
        completed = subprocess.run(
            (
                "git",
                "-C",
                str(repository),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        completed = None
    if completed is not None and completed.returncode == 0:
        return tuple(sorted(Path(os.fsdecode(raw)) for raw in completed.stdout.split(b"\0") if raw))
    return tuple(sorted(path.relative_to(repository) for path in repository.rglob("*")))


def _store_relative_to_repository(
    repository: Path,
    store_path: Path | None,
) -> Path | None:
    if store_path is None:
        return None
    try:
        return store_path.resolve().relative_to(repository.resolve())
    except ValueError:
        return None
