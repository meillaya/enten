from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final, Literal

from typing_extensions import override

MAX_SANDBOX_FILE_COUNT: Final = 2_000
MAX_SANDBOX_FILE_BYTES: Final = 2 * 1024 * 1024
MAX_SANDBOX_TOTAL_BYTES: Final = 64 * 1024 * 1024
TRUSTED_SOURCE_MANIFEST_PATH: Final = ".enten-trusted/source-manifest.json"
MAX_TRUSTED_SOURCE_MANIFEST_BYTES: Final = 4 * 1024

_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)
_SECRET_NAMES = frozenset(
    {
        ".env",
        "credentials",
        "credentials.json",
        "id_dsa",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
    }
)
_SECRET_SUFFIXES = (".key", ".p12", ".pfx", ".pem")


@dataclass(frozen=True, slots=True)
class UnsafeSandboxPathError(ValueError):
    path: str
    reason: str

    @override
    def __str__(self) -> str:
        return f"sandbox path must be a safe relative path: {self.path!r} ({self.reason})"


@dataclass(frozen=True, slots=True)
class SandboxFileLimitError(ValueError):
    limit_name: Literal["file_count", "file_bytes", "total_bytes"]
    actual: int
    limit: int
    path: str | None = None

    @override
    def __str__(self) -> str:
        location = "" if self.path is None else f" for {self.path!r}"
        return f"sandbox {self.limit_name} limit exceeded{location}: {self.actual} > {self.limit}"


def safe_relative_path(raw_path: str) -> Path:
    posix = PurePosixPath(raw_path)
    windows = PureWindowsPath(raw_path)
    if not raw_path or posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise UnsafeSandboxPathError(path=raw_path, reason="absolute path")
    if any(part in {"", ".", ".."} for part in raw_path.split("/")):
        raise UnsafeSandboxPathError(path=raw_path, reason="parent, dot, or empty component")
    if "\\" in raw_path:
        raise UnsafeSandboxPathError(path=raw_path, reason="platform path separator")
    return Path(*posix.parts)


def write_sandbox_files(root: Path, files: Mapping[str, str | bytes]) -> None:
    validate_sandbox_files(files)
    for raw_path, content in files.items():
        target = root / safe_relative_path(raw_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            _ = target.write_bytes(content)
        else:
            _ = target.write_text(content, encoding="utf-8")


def validate_sandbox_files(files: Mapping[str, str | bytes]) -> None:
    has_manifest = TRUSTED_SOURCE_MANIFEST_PATH in files
    source_file_count = len(files) - int(has_manifest)
    if source_file_count > MAX_SANDBOX_FILE_COUNT:
        raise SandboxFileLimitError(
            limit_name="file_count",
            actual=source_file_count,
            limit=MAX_SANDBOX_FILE_COUNT,
        )
    total_bytes = 0
    for raw_path, content in files.items():
        _ = safe_relative_path(raw_path)
        size = len(content) if isinstance(content, bytes) else len(content.encode("utf-8"))
        if raw_path == TRUSTED_SOURCE_MANIFEST_PATH:
            if size > MAX_TRUSTED_SOURCE_MANIFEST_BYTES:
                raise SandboxFileLimitError(
                    limit_name="file_bytes",
                    actual=size,
                    limit=MAX_TRUSTED_SOURCE_MANIFEST_BYTES,
                    path=raw_path,
                )
            continue
        if size > MAX_SANDBOX_FILE_BYTES:
            raise SandboxFileLimitError(
                limit_name="file_bytes",
                actual=size,
                limit=MAX_SANDBOX_FILE_BYTES,
                path=raw_path,
            )
        total_bytes += size
        if total_bytes > MAX_SANDBOX_TOTAL_BYTES:
            raise SandboxFileLimitError(
                limit_name="total_bytes",
                actual=total_bytes,
                limit=MAX_SANDBOX_TOTAL_BYTES,
                path=raw_path,
            )


def _git_paths(base: Path) -> list[Path] | None:
    process = subprocess.run(
        [
            "git",
            "-C",
            str(base),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            ".",
        ],
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        return None
    return [base / os.fsdecode(item) for item in process.stdout.split(b"\0") if item]


def _walk_paths(base: Path) -> list[Path]:
    paths: list[Path] = []
    for root, directories, filenames in os.walk(base, followlinks=False):
        directories[:] = sorted(
            directory for directory in directories if directory not in _EXCLUDED_DIRECTORIES
        )
        paths.extend(Path(root) / filename for filename in sorted(filenames))
    return paths


def _is_secret_path(relative: Path) -> bool:
    name = relative.name.lower()
    return name in _SECRET_NAMES or name.startswith(".env.") or name.endswith(_SECRET_SUFFIXES)


def collect_repository_files(repo_path: str) -> dict[str, str]:
    base = Path(repo_path)
    if not base.is_dir():
        return {}

    files: dict[str, str] = {}
    total_bytes = 0
    file_count = 0
    candidates = _git_paths(base)
    for path in candidates if candidates is not None else _walk_paths(base):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(base)
        if any(part in _EXCLUDED_DIRECTORIES for part in relative.parts):
            continue
        if _is_secret_path(relative):
            continue
        file_count += 1
        if file_count > MAX_SANDBOX_FILE_COUNT:
            raise SandboxFileLimitError(
                limit_name="file_count",
                actual=file_count,
                limit=MAX_SANDBOX_FILE_COUNT,
            )
        stat_size = path.stat().st_size
        if stat_size > MAX_SANDBOX_FILE_BYTES:
            raise SandboxFileLimitError(
                limit_name="file_bytes",
                actual=stat_size,
                limit=MAX_SANDBOX_FILE_BYTES,
                path=relative.as_posix(),
            )
        if total_bytes + stat_size > MAX_SANDBOX_TOTAL_BYTES:
            raise SandboxFileLimitError(
                limit_name="total_bytes",
                actual=total_bytes + stat_size,
                limit=MAX_SANDBOX_TOTAL_BYTES,
                path=relative.as_posix(),
            )
        remaining = MAX_SANDBOX_TOTAL_BYTES - total_bytes
        read_limit = min(MAX_SANDBOX_FILE_BYTES, remaining)
        with path.open("rb") as source:
            content = source.read(read_limit + 1)
        actual_size = len(content)
        if actual_size > MAX_SANDBOX_FILE_BYTES:
            raise SandboxFileLimitError(
                limit_name="file_bytes",
                actual=actual_size,
                limit=MAX_SANDBOX_FILE_BYTES,
                path=relative.as_posix(),
            )
        if actual_size > remaining:
            raise SandboxFileLimitError(
                limit_name="total_bytes",
                actual=total_bytes + actual_size,
                limit=MAX_SANDBOX_TOTAL_BYTES,
                path=relative.as_posix(),
            )
        total_bytes += actual_size
        if b"\0" in content:
            continue
        try:
            files[relative.as_posix()] = content.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return files


__all__ = [
    "MAX_SANDBOX_FILE_BYTES",
    "MAX_SANDBOX_FILE_COUNT",
    "MAX_SANDBOX_TOTAL_BYTES",
    "MAX_TRUSTED_SOURCE_MANIFEST_BYTES",
    "SandboxFileLimitError",
    "TRUSTED_SOURCE_MANIFEST_PATH",
    "UnsafeSandboxPathError",
    "collect_repository_files",
    "safe_relative_path",
    "validate_sandbox_files",
    "write_sandbox_files",
]
