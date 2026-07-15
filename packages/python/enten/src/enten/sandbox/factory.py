"""Sandbox provider factory."""

from __future__ import annotations

from enten.config import SandboxProvider as SandboxProviderEnum
from enten.config import Settings

from .protocol import SandboxProvider


def get_provider(settings: Settings) -> SandboxProvider:
    """Return a configured sandbox provider instance."""
    if settings.sandbox_provider == SandboxProviderEnum.DOCKER:
        from enten.sandbox.docker import DockerSandbox

        return DockerSandbox(image=settings.sandbox_docker_image)
    if settings.sandbox_provider == SandboxProviderEnum.E2B:
        from enten.sandbox.e2b import SandboxProviderUnavailableError

        raise SandboxProviderUnavailableError(
            provider="E2B",
            action="Select the Docker sandbox provider instead.",
        )
    raise ValueError(f"Unsupported sandbox provider: {settings.sandbox_provider}")
