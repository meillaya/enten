from __future__ import annotations

import re
from pathlib import Path

import anyio

from enten.review.run import (
    Finding,
    GeneratedFindings,
    Proposal,
    ReviewRequest,
    VerificationEvidence,
)

_SOURCE_SUFFIXES = frozenset({".py", ".pyi", ".js", ".jsx", ".ts", ".tsx"})
_EXCLUDED_PARTS = frozenset({".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv"})
_DYNAMIC_EVAL = re.compile(r"\beval\s*\(")


class LocalFindingsGenerator:
    async def generate(self, proposal: Proposal) -> GeneratedFindings:
        findings: list[Finding] = []
        for relative in proposal.files:
            source = proposal.repository_path / relative
            try:
                lines = source.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for line_number, line in enumerate(lines, start=1):
                if _DYNAMIC_EVAL.search(line):
                    findings.append(
                        Finding(
                            category="security.dynamic-evaluation",
                            severity="high",
                            message="Dynamic evaluation accepts executable input.",
                            file=relative,
                            line=line_number,
                        )
                    )
                    if len(findings) == 100:
                        return GeneratedFindings(
                            findings=tuple(findings), summary="100 bounded local findings"
                        )
        return GeneratedFindings(
            findings=tuple(findings),
            summary=f"{len(findings)} bounded local findings",
        )


class LocalVerifier:
    async def verify(self, request: ReviewRequest) -> VerificationEvidence:
        completed = await anyio.run_process(
            request.verification_command,
            cwd=request.repository_path,
            check=False,
        )
        return VerificationEvidence(
            exit_codes=(completed.returncode,),
            command=request.verification_command,
            log_paths=(),
        )


def discover_source_files(repository_path: Path) -> tuple[str, ...]:
    files: list[str] = []
    for path in sorted(repository_path.rglob("*")):
        if len(files) == 2_000:
            break
        if not path.is_file() or path.suffix not in _SOURCE_SUFFIXES:
            continue
        relative = path.relative_to(repository_path)
        if _EXCLUDED_PARTS.intersection(relative.parts):
            continue
        files.append(relative.as_posix())
    return tuple(files)


__all__ = ["LocalFindingsGenerator", "LocalVerifier", "discover_source_files"]
