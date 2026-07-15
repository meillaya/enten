from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

import anyio
from anyio.to_thread import run_sync

from enten.review.run import ReviewRequest, VerificationEvidence
from enten.sandbox.files import collect_repository_files
from enten.sandbox.protocol import SandboxProvider


class UnvalidatedRemoteCheckoutError(RuntimeError):
    pass


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
        pull_ref = f"refs/pull/{request.pr_number}/head"
        remote_ref = await _git(
            "-c",
            "credential.helper=",
            "ls-remote",
            "--exit-code",
            public_url,
            pull_ref,
        )
        remote_sha, separator, returned_ref = remote_ref.partition("\t")
        if separator != "\t" or remote_sha != request.head_sha or returned_ref != pull_ref:
            raise UnvalidatedRemoteCheckoutError(
                "checkout does not match the public pull request ref"
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
        handle = await self._provider.create(files)
        try:
            result = await self._provider.run(
                handle,
                list(request.verification_command),
            )
        finally:
            with anyio.CancelScope(shield=True):
                await self._provider.destroy(handle)
        return VerificationEvidence(
            exit_codes=(result.exit_code,),
            command=request.verification_command,
            log_paths=(),
        )


@asynccontextmanager
async def public_pull_request_checkout(
    *,
    repository_full_name: str,
    pr_number: int,
    head_sha: str,
) -> AsyncIterator[Path]:
    checkout = Path(tempfile.mkdtemp(prefix="enten-public-pr-"))
    public_url = f"https://github.com/{repository_full_name}.git"
    pull_ref = f"refs/pull/{pr_number}/head"
    try:
        await _git("init", "--quiet", str(checkout))
        await _git("-C", str(checkout), "remote", "add", "origin", public_url)
        await _git(
            "-c",
            "credential.helper=",
            "-c",
            "protocol.file.allow=never",
            "-C",
            str(checkout),
            "fetch",
            "--quiet",
            "--depth=1",
            "--no-tags",
            public_url,
            pull_ref,
        )
        await _git("-C", str(checkout), "checkout", "--quiet", "--detach", "FETCH_HEAD")
        actual_sha = await _git("-C", str(checkout), "rev-parse", "HEAD")
        if actual_sha != head_sha:
            raise UnvalidatedRemoteCheckoutError(
                f"fetched public pull request SHA {actual_sha!r} does not match {head_sha!r}"
            )
        yield checkout
    finally:
        with anyio.CancelScope(shield=True):
            await run_sync(shutil.rmtree, checkout, True)


async def _git(*arguments: str) -> str:
    path = os.environ.get("PATH", "/usr/bin:/bin")
    environment = {
        "GIT_ASKPASS": "/bin/false",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "PATH": path,
    }
    with anyio.fail_after(30):
        completed = await anyio.run_process(
            ("git", *arguments),
            env=environment,
            check=False,
        )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise UnvalidatedRemoteCheckoutError(stderr or "public checkout validation failed")
    return completed.stdout.decode("utf-8", errors="strict").strip()


__all__ = [
    "CheckoutValidator",
    "FailClosedCheckoutValidator",
    "GitPublicCheckoutValidator",
    "SandboxVerifier",
    "UnvalidatedRemoteCheckoutError",
    "public_pull_request_checkout",
]
