"""Semgrep static-analysis wrapper.

Runs Semgrep inside a sandbox, parses the JSON output via
:func:`enten.parsers.parse_semgrep_json`, and surfaces findings as plain
Pydantic models. Custom rules are uploaded into the sandbox before execution
so they never leave the local repo.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from enten.parsers import parse_semgrep_json
from enten.sandbox.files import collect_repository_files
from enten.sandbox.protocol import SandboxProvider


class SemgrepFinding(BaseModel):
    """A single Semgrep finding."""

    model_config = ConfigDict(extra="ignore")

    check_id: str
    path: str
    start_line: int
    end_line: int
    message: str
    severity: str


class SemgrepResult(BaseModel):
    """Result of a Semgrep invocation."""

    model_config = ConfigDict(extra="ignore")

    exit_code: int
    findings: list[SemgrepFinding] = []
    errors: list[str] = []


class SemgrepRuleError(Exception):
    """Raised when a custom Semgrep rule file is invalid YAML or fails validation."""


def _repo_files(repo_path: str) -> dict[str, str]:
    """Read every file under ``repo_path`` into a flat ``{relative_path: text}`` map."""
    return collect_repository_files(repo_path)


def _is_rule_error(error: str) -> bool:
    """Return True if a Semgrep error object indicates an invalid rule/config."""
    return error == "invalid_rule"


async def run_semgrep(
    sandbox: SandboxProvider,
    repo_path: str,
    language: str,
    custom_rule_paths: list[str] | None = None,
) -> SemgrepResult:
    """Run ``semgrep --json --config auto`` with optional custom rules in a sandbox.

    ``language`` is accepted for API symmetry but is not passed as ``--lang``;
    Semgrep infers languages from the rules and target files.

    Custom rules are uploaded to ``/workspace/.enten-rules/`` inside the sandbox
    and referenced by absolute path. If a rule path is relative, it is resolved
    against ``repo_path``.
    """
    _ = language  # Reserved for callers; Semgrep selects languages from rules.
    custom_rule_paths = custom_rule_paths or []

    handle = await sandbox.create(_repo_files(repo_path))
    try:
        uploaded: dict[str, str | bytes] = {}
        sandbox_rule_paths: list[str] = []
        for rule_path in custom_rule_paths:
            rule_file = Path(rule_path)
            if not rule_file.is_absolute():
                rule_file = Path(repo_path) / rule_file

            content = rule_file.read_text(encoding="utf-8")
            sandbox_rel = f".enten-rules/{rule_file.name}"
            uploaded[sandbox_rel] = content
            sandbox_rule_paths.append(f"/workspace/{sandbox_rel}")

        if uploaded:
            await sandbox.upload(handle, uploaded)

        cmd = ["semgrep", "--json", "--config", "auto", "--error"]
        for rule_path in sandbox_rule_paths:
            cmd.extend(["--config", rule_path])
        cmd.append("/workspace")

        exec_result = await sandbox.run(handle, cmd, timeout_seconds=120)
        parsed = parse_semgrep_json(exec_result.stdout)

        for error in parsed.errors:
            if _is_rule_error(error):
                raise SemgrepRuleError(str(error))

        findings = [SemgrepFinding.model_validate(f.model_dump()) for f in parsed.results]
        return SemgrepResult(
            exit_code=exec_result.exit_code,
            findings=findings,
            errors=list(parsed.errors),
        )
    finally:
        await sandbox.destroy(handle)
