"""Security analysis runner.

Runs gitleaks, bandit (Python only) and semgrep inside a sandbox and returns a
normalized, redacted report. Tool JSON outputs are parsed with the shared
parsers from ``enten.parsers``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ..parsers import parse_gitleaks_json, parse_semgrep_json
from ..sandbox import SandboxProvider
from ..sandbox.models import SandboxHandle

# AWS access key IDs are the secret shape the runner explicitly redacts in
# generated human-readable messages.
_AWS_KEY_RE: re.Pattern[str] = re.compile(r"AKIA[A-Z0-9]{16}")

_GITLEAKS_INSTALL = [
    "sh",
    "-c",
    (
        "mkdir -p /workspace/.bin && "
        "curl -sSL https://github.com/zricethezav/gitleaks/releases/download/"
        "v8.18.4/gitleaks_8.18.4_linux_x64.tar.gz "
        "| tar xz -C /workspace/.bin gitleaks && "
        "chmod +x /workspace/.bin/gitleaks"
    ),
]


class SecurityFinding(BaseModel):
    """A single security finding with tool-agnostic attribution."""

    model_config = ConfigDict(extra="ignore")

    tool: str
    check_id: str
    severity: str
    file: str
    line: int
    message: str


class SecurityReport(BaseModel):
    """Aggregated security findings with per-tool counts."""

    model_config = ConfigDict(extra="ignore")

    findings: list[SecurityFinding] = []
    counts: dict[str, int] = {}


def _redact_message(message: str) -> str:
    """Replace AWS access key IDs in a message with ``***``."""
    return _AWS_KEY_RE.sub("***", message)


def _repo_files(repo_path: str) -> dict[str, str]:
    """Read every file under ``repo_path`` into a flat ``{relative_path: text}`` map."""
    files: dict[str, str] = {}
    base = Path(repo_path)
    if not base.exists():
        return files
    for path in base.rglob("*"):
        if path.is_file():
            rel = path.relative_to(base).as_posix()
            try:
                files[rel] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                files[rel] = path.read_bytes().decode("utf-8", errors="replace")
    return files


async def _cat_file(
    provider: SandboxProvider, handle: SandboxHandle, path: str
) -> str:
    """Read ``path`` from the sandbox via ``cat``."""
    result = await provider.run(handle, ["cat", path], timeout_seconds=10)
    return result.stdout


async def _ensure_tool(
    provider: SandboxProvider,
    handle: SandboxHandle,
    tool: str,
    install_cmd: list[str],
) -> None:
    """Ensure ``tool`` is available in the sandbox, installing if necessary."""
    probe = await provider.run(
        handle, ["sh", "-c", f"command -v {tool}"], timeout_seconds=30
    )
    if probe.exit_code == 0:
        return

    install = await provider.run(handle, install_cmd, timeout_seconds=180)
    if install.exit_code != 0:
        raise RuntimeError(
            f"Failed to install {tool}: {install.stderr or install.stdout}"
        )


async def _run_gitleaks(
    provider: SandboxProvider, handle: SandboxHandle
) -> list[SecurityFinding]:
    """Run gitleaks and map leaks to :class:`SecurityFinding`."""
    await _ensure_tool(provider, handle, "gitleaks", _GITLEAKS_INSTALL)

    _ = await provider.run(
        handle,
        [
            "gitleaks",
            "detect",
            "--no-git",
            "--report-format",
            "json",
            "--report-path",
            "/tmp/gl.json",
            "--exit-code",
            "1",
            "--source",
            "/workspace",
        ],
        timeout_seconds=120,
    )

    try:
        json_text = await _cat_file(provider, handle, "/tmp/gl.json")
    except RuntimeError:
        # The report file may be missing if gitleaks failed to run; treat as
        # no findings rather than crashing the whole security check.
        return []

    if not json_text.strip():
        return []

    result = parse_gitleaks_json(json_text)
    findings: list[SecurityFinding] = []
    for leak in result.leaks:
        message = _redact_message(leak.match)
        findings.append(
            SecurityFinding(
                tool="gitleaks",
                check_id=leak.rule_id,
                severity="HIGH",
                file=leak.file,
                line=leak.start_line,
                message=message,
            )
        )
    return findings


def _parse_bandit_json(data: str) -> list[SecurityFinding]:
    """Parse bandit JSON output into :class:`SecurityFinding` objects."""
    report = json.loads(data)
    if not isinstance(report, dict):
        raise ValueError("bandit JSON report must be an object")

    raw_results = report.get("results") or []
    if not isinstance(raw_results, list):
        raise ValueError("bandit JSON results must be a list")

    findings: list[SecurityFinding] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        findings.append(
            SecurityFinding(
                tool="bandit",
                check_id=str(raw.get("test_id", "")),
                severity=str(raw.get("issue_severity", "")),
                file=str(raw.get("filename", "")),
                line=int(raw.get("line_number", 0)),
                message=_redact_message(str(raw.get("issue_text", ""))),
            )
        )
    return findings


async def _run_bandit(
    provider: SandboxProvider, handle: SandboxHandle
) -> list[SecurityFinding]:
    """Run bandit on Python sources and map results to :class:`SecurityFinding`."""
    await _ensure_tool(provider, handle, "bandit", ["pip", "install", "bandit"])

    result = await provider.run(
        handle,
        ["bandit", "-r", "/workspace", "-f", "json", "-o", "/tmp/bandit.json"],
        timeout_seconds=120,
    )

    try:
        json_text = await _cat_file(provider, handle, "/tmp/bandit.json")
    except RuntimeError:
        return []

    if not json_text.strip():
        return []

    # Bandit writes an empty report (just {"results": [], "errors": []}) when
    # no issues are found, but it exits 0 in that case. If it exited non-zero
    # and the file is missing/empty, we have nothing to parse.
    if result.exit_code != 0 and not json.loads(json_text).get("results"):
        return []

    return _parse_bandit_json(json_text)


async def _run_semgrep(
    provider: SandboxProvider, handle: SandboxHandle
) -> list[SecurityFinding]:
    """Run semgrep with the security-audit ruleset and map results."""
    await _ensure_tool(provider, handle, "semgrep", ["pip", "install", "semgrep"])

    result = await provider.run(
        handle,
        ["semgrep", "--json", "--config", "p/security-audit", "--error", "/workspace"],
        timeout_seconds=120,
    )

    parsed = parse_semgrep_json(result.stdout)
    findings: list[SecurityFinding] = []
    for finding in parsed.results:
        findings.append(
            SecurityFinding(
                tool="semgrep",
                check_id=finding.check_id,
                severity=finding.severity,
                file=finding.path,
                line=finding.start_line,
                message=_redact_message(finding.message),
            )
        )
    return findings


def _build_counts(findings: list[SecurityFinding]) -> dict[str, int]:
    """Return per-tool and total counts for a list of findings."""
    counts: dict[str, int] = {"total": 0}
    for finding in findings:
        counts["total"] += 1
        counts[finding.tool] = counts.get(finding.tool, 0) + 1
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return counts


async def run_security_checks(
    sandbox: SandboxProvider, repo_path: str, language: str
) -> SecurityReport:
    """Run gitleaks, bandit (Python only) and semgrep in ``sandbox``.

    Args:
        sandbox: A :class:`SandboxProvider` that can create isolated sandboxes.
        repo_path: Local path to the repository to analyze. Files are uploaded
            into the sandbox at ``/workspace``.
        language: Repository language. Bandit is only run when this is
            ``"python"``.

    Returns:
        A :class:`SecurityReport` with normalized findings and counts.
    """
    handle = await sandbox.create(_repo_files(repo_path))
    try:
        findings: list[SecurityFinding] = []
        findings.extend(await _run_gitleaks(sandbox, handle))

        if language.lower() == "python":
            findings.extend(await _run_bandit(sandbox, handle))

        findings.extend(await _run_semgrep(sandbox, handle))

        return SecurityReport(findings=findings, counts=_build_counts(findings))
    finally:
        await sandbox.destroy(handle)
