"""Sandbox provider protocol."""

from __future__ import annotations

from typing import Protocol

from .models import ExecResult, SandboxHandle


class SandboxProvider(Protocol):
    """Async protocol for sandbox lifecycle management."""

    async def create(self, repo_files: dict[str, str]) -> SandboxHandle:
        """Create a fresh sandbox seeded with ``repo_files``."""
        ...

    async def run(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        timeout_seconds: int = 120,
    ) -> ExecResult:
        """Run ``cmd`` inside the sandbox referenced by ``handle``."""
        ...

    async def upload(self, handle: SandboxHandle, files: dict[str, str | bytes]) -> None:
        """Copy additional files into a running sandbox."""
        ...

    async def destroy(self, handle: SandboxHandle) -> None:
        """Stop and remove the sandbox."""
        ...


__all__ = ["SandboxProvider"]
