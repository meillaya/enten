from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

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

    def __str__(self) -> str:
        return f"sandbox path must be a safe relative path: {self.path!r} ({self.reason})"


def safe_relative_path(raw_path: str) -> Path:
    posix = PurePosixPath(raw_path)
    windows = PureWindowsPath(raw_path)
    if not raw_path or posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise UnsafeSandboxPathError(path=raw_path, reason="absolute path")
    if any(part in {"", ".", ".."} for part in posix.parts):
        raise UnsafeSandboxPathError(path=raw_path, reason="parent or empty component")
    if "\\" in raw_path:
        raise UnsafeSandboxPathError(path=raw_path, reason="platform path separator")
    return Path(*posix.parts)


def write_sandbox_files(root: Path, files: Mapping[str, str | bytes]) -> None:
    for raw_path, content in files.items():
        target = root / safe_relative_path(raw_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            _ = target.write_bytes(content)
        else:
            _ = target.write_text(content, encoding="utf-8")


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
    candidates = _git_paths(base)
    for path in candidates if candidates is not None else _walk_paths(base):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(base)
        if any(part in _EXCLUDED_DIRECTORIES for part in relative.parts):
            continue
        if _is_secret_path(relative):
            continue
        content = path.read_bytes()
        if b"\0" in content:
            continue
        try:
            files[relative.as_posix()] = content.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return files


__all__ = [
    "UnsafeSandboxPathError",
    "collect_repository_files",
    "safe_relative_path",
    "write_sandbox_files",
]
