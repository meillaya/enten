from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final, Literal, Protocol, assert_never

from anyio.to_thread import run_sync

from enten.review.run import ReviewRequest, Verdict, VerificationEvidence
from enten.sandbox.files import TRUSTED_SOURCE_MANIFEST_PATH, collect_repository_files
from enten.sandbox.models import ExecResult, SandboxHandle
from enten.sandbox.protocol import SandboxProvider
from enten.verification.flaky import run_with_flaky_check

from .public_checkout import (
    FETCH_PACK_MAX_BYTES,
    UnvalidatedRemoteCheckoutError,
    _git,
    public_pull_request_checkout,
)

SOURCE_MANIFEST_PATH: Final = TRUSTED_SOURCE_MANIFEST_PATH
_SOURCE_DIGEST_DOMAIN: Final = b"enten-normalized-source-v1\0"


@dataclass(frozen=True, slots=True)
class SandboxVerificationError(RuntimeError):
    detail: str

    def __str__(self) -> str:
        return self.detail


class CheckoutValidator(Protocol):
    async def validate(self, request: ReviewRequest) -> None: ...


class FailClosedCheckoutValidator:
    async def validate(self, request: ReviewRequest) -> None:
        raise UnvalidatedRemoteCheckoutError(
            f"remote checkout validation was not configured for {request.repository_id}"
        )


class GitPublicCheckoutValidator:
    async def validate(self, request: ReviewRequest) -> None:
        if request.local_repository or not request.public_repository:
            raise UnvalidatedRemoteCheckoutError("remote review requires a public checkout")
        full_name = request.repository_full_name
        if full_name is None:
            raise UnvalidatedRemoteCheckoutError("remote review is missing repository full_name")

        head = await _git(
            "-C",
            str(request.repository_path),
            "rev-parse",
            "HEAD",
        )
        if head != request.head_sha:
            raise UnvalidatedRemoteCheckoutError(
                f"checkout HEAD {head!r} does not match requested SHA {request.head_sha!r}"
            )

        origin = await _git(
            "-C",
            str(request.repository_path),
            "remote",
            "get-url",
            "origin",
        )
        public_url = f"https://github.com/{full_name}.git"
        if origin.rstrip("/") not in {public_url, public_url.removesuffix(".git")}:
            raise UnvalidatedRemoteCheckoutError(
                f"checkout origin {origin!r} is not the canonical public GitHub URL"
            )


class SandboxVerifier:
    def __init__(self, provider: SandboxProvider) -> None:
        self._provider: SandboxProvider = provider

    async def verify(self, request: ReviewRequest) -> VerificationEvidence:
        files = await run_sync(
            collect_repository_files,
            str(request.repository_path),
        )
        if not files:
            raise UnvalidatedRemoteCheckoutError("validated checkout contains no reviewable files")
        transported_files = _add_source_manifest(request, files)

        async def run_command(
            handle: SandboxHandle,
            command: tuple[str, ...],
        ) -> ExecResult:
            return await self._provider.run(handle, list(command))

        summary = await run_with_flaky_check(
            self._provider,
            run_command,
            request.verification_command,
            runs=3,
            repo_files=transported_files,
        )
        verdict = _verification_verdict(summary.outcome)
        return VerificationEvidence(
            exit_codes=tuple(run.exit_code for run in summary.runs),
            command=request.verification_command,
            log_paths=(),
            verdict=verdict,
            infrastructure_detail=(
                "sandbox verification infrastructure failed" if verdict == "error" else None
            ),
        )


def _add_source_manifest(
    request: ReviewRequest,
    files: dict[str, str],
) -> dict[str, str]:
    if SOURCE_MANIFEST_PATH in files:
        raise UnvalidatedRemoteCheckoutError("checkout contains the reserved source manifest path")
    if request.repository_full_name is None:
        raise UnvalidatedRemoteCheckoutError("remote review is missing repository full_name")
    if not request.repository_id.isdecimal():
        raise UnvalidatedRemoteCheckoutError("remote repository id must be a decimal string")
    manifest = {
        "head_sha": request.head_sha,
        "repository_full_name": request.repository_full_name,
        "repository_id": request.repository_id,
        "source_digest": _normalized_source_digest(files),
        "version": 1,
    }
    payload = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {**files, SOURCE_MANIFEST_PATH: payload}


def _normalized_source_digest(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    digest.update(_SOURCE_DIGEST_DOMAIN)
    for path, content in sorted(files.items()):
        path_bytes = path.encode("utf-8")
        content_bytes = content.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content_bytes).to_bytes(8, "big"))
        digest.update(content_bytes)
    return digest.hexdigest()


def _verification_verdict(
    outcome: Literal["pass", "flaky", "fail", "error"],
) -> Verdict:
    match outcome:
        case "pass":
            return "pass"
        case "flaky":
            return "flaky"
        case "fail":
            return "fail"
        case "error":
            return "error"
        case unreachable:
            assert_never(unreachable)


__all__ = [
    "CheckoutValidator",
    "FETCH_PACK_MAX_BYTES",
    "FailClosedCheckoutValidator",
    "GitPublicCheckoutValidator",
    "SOURCE_MANIFEST_PATH",
    "SandboxVerifier",
    "SandboxVerificationError",
    "UnvalidatedRemoteCheckoutError",
    "public_pull_request_checkout",
]
