"""Sandbox abstraction for executing untrusted code.

A :class:`SandboxProvider` creates isolated execution environments (currently
Docker containers; E2B is planned for T39). Each sandbox is ephemeral: files
are uploaded at creation time, commands are run on demand, and the whole
environment is destroyed when no longer needed.
"""

from __future__ import annotations

from .factory import get_provider
from .models import ExecResult, SandboxHandle
from .protocol import SandboxProvider


__all__ = [
    "ExecResult",
    "SandboxHandle",
    "SandboxProvider",
    "get_provider",
]
