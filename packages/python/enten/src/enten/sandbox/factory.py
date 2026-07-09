"""Sandbox provider factory."""

from __future__ import annotations

from enten.config import SandboxProvider as SandboxProviderEnum
from enten.config import Settings
from .protocol import SandboxProvider


def get_provider(settings: Settings) -> SandboxProvider:
    """Return a configured sandbox provider instance."""
    # Local imports avoid heavy SDK imports at module load time and keep the
    # provider implementations optional (E2B is not implemented until T39).
    if settings.sandbox_provider == SandboxProviderEnum.DOCKER:
        from enten.sandbox.docker import DockerSandbox

        return DockerSandbox()
    if settings.sandbox_provider == SandboxProviderEnum.E2B:
        from enten.sandbox.e2b import E2BSandbox  # type: ignore[import-not-found]

        return E2BSandbox()
    raise ValueError(f"Unsupported sandbox provider: {settings.sandbox_provider}")
