"""Bounded unified-diff indexing for review prompts and inline findings."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict
from unidiff import PatchSet

MAX_DIFF_BYTES = 64 * 1024


class DiffIndex(BaseModel):
    """Added and removed line numbers for each file in a unified diff."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    added_lines: dict[str, tuple[int, ...]] = {}
    removed_lines: dict[str, tuple[int, ...]] = {}
    files: tuple[str, ...] = ()
    truncated: bool = False

    @classmethod
    def from_patch(cls, patch: str) -> DiffIndex:
        """Parse ``patch`` without letting malformed input crash the review."""
        if not patch.strip():
            return cls()
        encoded = patch.encode("utf-8")
        truncated = len(encoded) > MAX_DIFF_BYTES
        if truncated:
            patch = encoded[:MAX_DIFF_BYTES].decode("utf-8", errors="ignore")

        try:
            parsed = PatchSet.from_string(patch)
        except Exception:
            # A truncated hunk or a malformed provider response is evidence
            # that we cannot anchor a finding. Keep the review safe.
            return cls(truncated=truncated)

        added: dict[str, list[int]] = {}
        removed: dict[str, list[int]] = {}
        files: list[str] = []
        for patched_file in parsed:
            path = _target_path(patched_file)
            if path is None:
                continue
            files.append(path)
            added.setdefault(path, [])
            removed.setdefault(path, [])
            for hunk in patched_file:
                for line in hunk:
                    if line.is_added and line.target_line_no is not None:
                        added[path].append(line.target_line_no)
                    elif line.is_removed and line.source_line_no is not None:
                        removed[path].append(line.source_line_no)

        return cls(
            added_lines={path: tuple(sorted(set(lines))) for path, lines in added.items()},
            removed_lines={path: tuple(sorted(set(lines))) for path, lines in removed.items()},
            files=tuple(files),
            truncated=truncated,
        )

    def contains_added(self, path: str, line: int) -> bool:
        """Return True when ``line`` is an added line in ``path``."""
        return line in self.added_lines.get(_normalize_path(path), ())

    def changed_files(self, suffixes: frozenset[str]) -> tuple[str, ...]:
        """Return changed files with one of ``suffixes``."""
        return tuple(
            path for path in self.files if any(path.endswith(suffix) for suffix in suffixes)
        )


def _target_path(patched_file: object) -> str | None:
    target = getattr(patched_file, "target_file", None)
    if not isinstance(target, str) or target == "/dev/null":
        return None
    return _normalize_path(target)


def _normalize_path(path: str) -> str:
    return path.removeprefix("a/").removeprefix("b/")


__all__ = ["DiffIndex", "MAX_DIFF_BYTES"]
