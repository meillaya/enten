from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

import anyio
from anyio.to_thread import run_sync

from enten.sandbox.files import (
    MAX_SANDBOX_FILE_BYTES,
    MAX_SANDBOX_FILE_COUNT,
    MAX_SANDBOX_TOTAL_BYTES,
    safe_relative_path,
)

FETCH_PACK_MAX_BYTES: Final = 128 * 1024 * 1024


class UnvalidatedRemoteCheckoutError(RuntimeError):
    pass


@asynccontextmanager
async def public_pull_request_checkout(
    *,
    repository_full_name: str,
    pr_number: int,
    head_sha: str,
) -> AsyncIterator[Path]:
    checkout = Path(tempfile.mkdtemp(prefix="enten-public-pr-"))
    public_url = f"https://github.com/{repository_full_name}.git"
    # Fetch the exact requested SHA. A PR ref moves when the author pushes,
    # so fetching it would make a legitimate retry fail after a new commit.
    _ = pr_number
    try:
        await _git("init", "--quiet", str(checkout))
        await _git("-C", str(checkout), "remote", "add", "origin", public_url)
        await _limited_git(
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
            head_sha,
        )
        pack_bytes = await run_sync(_pack_size_bytes, checkout)
        if pack_bytes > FETCH_PACK_MAX_BYTES:
            raise UnvalidatedRemoteCheckoutError(
                f"fetch pack bytes {pack_bytes} exceed {FETCH_PACK_MAX_BYTES}"
            )
        tree_listing = await _git(
            "-C",
            str(checkout),
            "ls-tree",
            "-lr",
            "-z",
            "FETCH_HEAD",
        )
        _validate_tree_quota(tree_listing)
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
    return await _run_command(("git", *arguments))


async def _limited_git(*arguments: str) -> str:
    prlimit = shutil.which("prlimit")
    if prlimit is None:
        raise UnvalidatedRemoteCheckoutError("prlimit is required for bounded public fetches")
    return await _run_command(
        (
            prlimit,
            f"--fsize={FETCH_PACK_MAX_BYTES}",
            "--",
            "git",
            *arguments,
        )
    )


async def _run_command(command: tuple[str, ...]) -> str:
    environment = {
        "GIT_ASKPASS": "/bin/false",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    with anyio.fail_after(30):
        completed = await anyio.run_process(command, env=environment, check=False)
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise UnvalidatedRemoteCheckoutError(stderr or "public checkout validation failed")
    return completed.stdout.decode("utf-8", errors="strict").strip()


def _pack_size_bytes(checkout: Path) -> int:
    pack_directory = checkout / ".git" / "objects" / "pack"
    if not pack_directory.is_dir():
        return 0
    return sum(path.stat().st_size for path in pack_directory.iterdir() if path.is_file())


def _validate_tree_quota(listing: str) -> None:
    total_bytes = 0
    file_count = 0
    for record in listing.split("\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition("\t")
        fields = metadata.split()
        if separator != "\t" or len(fields) != 4:
            raise UnvalidatedRemoteCheckoutError("git returned a malformed tree entry")
        mode, object_type, _object_id, size_text = fields
        if object_type != "blob" or mode not in {"100644", "100755"}:
            raise UnvalidatedRemoteCheckoutError(
                f"checkout contains unsupported tree entry {raw_path!r}"
            )
        _ = safe_relative_path(raw_path)
        try:
            size = int(size_text)
        except ValueError as exc:
            raise UnvalidatedRemoteCheckoutError("git returned an invalid blob size") from exc
        if size < 0:
            raise UnvalidatedRemoteCheckoutError("git returned a negative blob size")
        file_count += 1
        if file_count > MAX_SANDBOX_FILE_COUNT:
            raise UnvalidatedRemoteCheckoutError(
                f"tracked file count {file_count} exceeds {MAX_SANDBOX_FILE_COUNT}"
            )
        if size > MAX_SANDBOX_FILE_BYTES:
            raise UnvalidatedRemoteCheckoutError(
                f"blob size {size} exceeds {MAX_SANDBOX_FILE_BYTES} for {raw_path!r}"
            )
        total_bytes += size
        if total_bytes > MAX_SANDBOX_TOTAL_BYTES:
            raise UnvalidatedRemoteCheckoutError(
                f"expanded bytes {total_bytes} exceed {MAX_SANDBOX_TOTAL_BYTES}"
            )


__all__ = [
    "FETCH_PACK_MAX_BYTES",
    "UnvalidatedRemoteCheckoutError",
    "public_pull_request_checkout",
]
