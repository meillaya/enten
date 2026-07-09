"""Shared sandbox data models."""

from __future__ import annotations

from pydantic import BaseModel


class SandboxHandle(BaseModel):
    """Lightweight reference to a running sandbox."""

    id: str
    provider: str
    image: str


class ExecResult(BaseModel):
    """Result of executing a command inside a sandbox."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


__all__ = [
    "ExecResult",
    "SandboxHandle",
]
