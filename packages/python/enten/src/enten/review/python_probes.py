"""Python-only probe generation policy and execution runners."""

from __future__ import annotations

import ast
import shlex
import subprocess
import time
import uuid
from functools import partial
from pathlib import Path
from typing import Protocol

from anyio.to_thread import run_sync

from enten.review.run import (
    Finding,
    FindingEvidence,
    GeneratedFindings,
    Hypothesis,
    HypothesisSet,
    ProbeEvidence,
    ProbeResult,
    Proposal,
)
from enten.sandbox.files import collect_repository_files, validate_sandbox_files
from enten.sandbox.models import SandboxHandle
from enten.sandbox.protocol import SandboxProvider

MAX_PROBE_BYTES = 20_000
MAX_PROBE_OUTPUT = 4_000
DEFAULT_PROBE_COMMAND = ("python", "-m", "pytest", "-q")
_FORBIDDEN_CALLS = frozenset(
    {
        "socket.",
        "requests.",
        "httpx.",
        "urllib.",
        "http.client.",
        "subprocess.",
        "os.system",
        "os.popen",
        "os._exit",
        "sys.exit",
    }
)


class ProbePolicyError(ValueError):
    """A generated probe is unsafe, malformed, or not target-specific."""


class ProbeRunner(Protocol):
    """Execute generated probes against a repository."""

    async def run(self, hypotheses: HypothesisSet, proposal: Proposal) -> ProbeEvidence: ...


def validate_probe_source(source: str, target_file: str) -> str | None:
    """Return a rejection reason, or None when a probe is acceptable to run."""
    if not source.strip():
        return "empty probe"
    if len(source.encode("utf-8")) > MAX_PROBE_BYTES:
        return "probe is too large"
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return f"probe does not parse: {exc.msg}"

    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    if not any(node.name.startswith("test_") for node in functions):
        return "probe has no test_ function"
    asserts = [node for node in ast.walk(tree) if isinstance(node, ast.Assert)]
    if not asserts:
        return "probe has no assertion"
    if all(
        isinstance(node.test, ast.Constant) and node.test.value is False for node in asserts
    ):
        return "probe only asserts False"
    if _has_forbidden_call(tree):
        return "probe uses a forbidden call"
    if not _references_target(source, target_file):
        return "probe does not reference the cited file"
    return None


def _has_forbidden_call(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if any(
                name == prefix.rstrip(".") or name.startswith(prefix)
                for prefix in _FORBIDDEN_CALLS
            ):
                return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"socket", "requests", "httpx", "subprocess"}:
                    return True
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in {"socket", "requests", "httpx", "subprocess"}:
                return True
    return False


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_call_name(node.value)}.{node.attr}"
    return ""


def _references_target(source: str, target_file: str) -> bool:
    target = Path(target_file)
    stem = target.stem
    if not stem:
        return False
    return stem in source or target_file in source


def build_findings(hypotheses: HypothesisSet, probes: ProbeEvidence) -> GeneratedFindings:
    """Keep only confirmed probes and attach their executable evidence."""
    by_id = {hypothesis.id: hypothesis for hypothesis in hypotheses.hypotheses}
    findings: list[Finding] = []
    for result in probes.results:
        if result.status != "confirmed":
            continue
        hypothesis = by_id.get(result.hypothesis_id)
        if hypothesis is None:
            continue
        findings.append(
            Finding(
                category=hypothesis.category,
                severity=hypothesis.severity,
                message=hypothesis.message,
                file=hypothesis.file,
                line=hypothesis.line,
                start_line=hypothesis.line,
                evidence=finding_evidence(
                    hypothesis,
                    result,
                    command=DEFAULT_PROBE_COMMAND,
                    sandbox="docker",
                ),
            )
        )
    return GeneratedFindings(
        findings=tuple(findings),
        summary=f"{len(findings)} confirmed findings",
        tokens_in=hypotheses.tokens_in,
        tokens_out=hypotheses.tokens_out,
        cost_usd=hypotheses.cost_usd,
    )


def probe_path(hypothesis_id: str, run_token: str | None = None) -> str:
    """Return a server-controlled repository-relative probe path."""
    safe = "".join(
        character
        for character in hypothesis_id
        if character.isalnum() or character in "-_"
    )
    if not safe:
        safe = "probe"
    if run_token is None:
        return f".enten-probes/{safe}_test.py"
    safe_token = "".join(
        character for character in run_token if character.isalnum() or character in "-_"
    )
    return f".enten-probes/{safe_token}/{safe}_test.py"


def classify_probe(
    hypothesis: Hypothesis,
    *,
    exit_code: int | None,
    output: str,
    duration_ms: int,
) -> ProbeResult:
    """Map a pytest result to confirmed, rejected, or inconclusive."""
    excerpt = output[-MAX_PROBE_OUTPUT:]
    if exit_code is None:
        status = "inconclusive"
    elif exit_code == 0 or exit_code in {2, 3, 4, 5}:
        status = "rejected"
    elif exit_code == 1:
        status = (
            "confirmed"
            if _references_target(hypothesis.test_source, hypothesis.file)
            or _output_references(hypothesis, output)
            else "rejected"
        )
    else:
        status = "inconclusive"
    return ProbeResult(
        hypothesis_id=hypothesis.id,
        status=status,
        exit_code=exit_code,
        duration_ms=duration_ms,
        output_excerpt=excerpt,
        test_sha256=hypothesis.test_sha256,
    )


def _output_references(hypothesis: Hypothesis, output: str) -> bool:
    target = Path(hypothesis.file)
    return hypothesis.file in output or (target.stem and target.stem in output)


def finding_evidence(
    hypothesis: Hypothesis,
    result: ProbeResult,
    *,
    command: tuple[str, ...],
    sandbox: str,
) -> FindingEvidence:
    return FindingEvidence(
        probe_command=command,
        test_source=hypothesis.test_source,
        exit_code=result.exit_code if result.exit_code is not None else -1,
        output_excerpt=result.output_excerpt,
        test_sha256=hypothesis.test_sha256,
        sandbox=sandbox,
    )


class LocalProbeRunner:
    """Run probes in the caller's local checkout for tests and the CLI."""

    def __init__(
        self,
        command: tuple[str, ...] = DEFAULT_PROBE_COMMAND,
        timeout_seconds: int = 90,
    ) -> None:
        self._command = command
        self._timeout_seconds = timeout_seconds

    async def run(self, hypotheses: HypothesisSet, proposal: Proposal) -> ProbeEvidence:
        results: list[ProbeResult] = []
        run_token = uuid.uuid4().hex
        probe_dir = proposal.repository_path / ".enten-probes" / run_token
        await run_sync(partial(probe_dir.mkdir, parents=True, exist_ok=True))
        try:
            for hypothesis in hypotheses.hypotheses:
                results.append(await self._run_one(hypothesis, proposal, run_token))
        finally:
            await run_sync(_remove_tree, probe_dir)
            await run_sync(_remove_empty_dir, probe_dir.parent)
        return ProbeEvidence(results=tuple(results))

    async def _run_one(
        self,
        hypothesis: Hypothesis,
        proposal: Proposal,
        run_token: str,
    ) -> ProbeResult:
        relative = probe_path(hypothesis.id, run_token)
        target = proposal.repository_path / relative
        await run_sync(partial(target.write_text, hypothesis.test_source, encoding="utf-8"))
        started = time.monotonic()
        try:
            completed = await run_sync(
                partial(
                    subprocess.run,
                    [*self._command, relative],
                    cwd=proposal.repository_path,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_seconds,
                    check=False,
                )
            )
            output = f"{completed.stdout}\n{completed.stderr}"
            return classify_probe(
                hypothesis,
                exit_code=completed.returncode,
                output=output,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except subprocess.TimeoutExpired:
            return classify_probe(
                hypothesis,
                exit_code=None,
                output="probe timed out",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        finally:
            await run_sync(partial(target.unlink, missing_ok=True))


class SandboxProbeRunner:
    """Run probes in the configured networkless Docker sandbox."""

    def __init__(
        self,
        provider: SandboxProvider,
        command: tuple[str, ...] = DEFAULT_PROBE_COMMAND,
        timeout_seconds: int = 90,
    ) -> None:
        self._provider = provider
        self._command = command
        self._timeout_seconds = timeout_seconds

    async def run(self, hypotheses: HypothesisSet, proposal: Proposal) -> ProbeEvidence:
        files = await run_sync(collect_repository_files, str(proposal.repository_path))
        validate_sandbox_files(files)
        results: list[ProbeResult] = []
        run_token = uuid.uuid4().hex
        handle = await self._provider.create(files)
        try:
            for hypothesis in hypotheses.hypotheses:
                results.append(await self._run_one(handle, hypothesis, proposal, run_token))
        finally:
            await self._provider.destroy(handle)
        return ProbeEvidence(results=tuple(results))

    async def _run_one(
        self,
        handle: SandboxHandle,
        hypothesis: Hypothesis,
        proposal: Proposal,
        run_token: str,
    ) -> ProbeResult:
        _ = proposal
        relative = probe_path(hypothesis.id, run_token)
        await self._provider.upload(handle, {relative: hypothesis.test_source})
        started = time.monotonic()
        try:
            result = await self._provider.run(
                handle,
                [*self._command, relative],
                timeout_seconds=self._timeout_seconds,
            )
            output = f"{result.stdout}\n{result.stderr}"
            return classify_probe(
                hypothesis,
                exit_code=result.exit_code,
                output=output,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception:
            return classify_probe(
                hypothesis,
                exit_code=None,
                output="sandbox probe failed",
                duration_ms=int((time.monotonic() - started) * 1000),
            )


def parse_probe_command(raw: str) -> tuple[str, ...]:
    command = tuple(shlex.split(raw))
    if not command:
        raise ValueError("probe command cannot be empty")
    return command


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _remove_empty_dir(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        return


__all__ = [
    "DEFAULT_PROBE_COMMAND",
    "LocalProbeRunner",
    "ProbePolicyError",
    "ProbeRunner",
    "SandboxProbeRunner",
    "build_findings",
    "classify_probe",
    "finding_evidence",
    "parse_probe_command",
    "probe_path",
    "validate_probe_source",
]
