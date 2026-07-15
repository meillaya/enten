"""Sandbox abstraction for executing untrusted code.

A :class:`SandboxProvider` creates ephemeral isolated execution environments.
Docker is the verified backend; selecting E2B fails closed.
"""

from __future__ import annotations

from .e2b import E2BSandbox, SandboxProviderUnavailableError
from .factory import get_provider
from .models import ExecResult, SandboxHandle
from .protocol import SandboxProvider

__all__ = [
    "E2BSandbox",
    "ExecResult",
    "SandboxHandle",
    "SandboxProvider",
    "SandboxProviderUnavailableError",
    "get_provider",
]
