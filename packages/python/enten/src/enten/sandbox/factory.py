"""Sandbox provider factory."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from enten.config import SandboxProvider as SandboxProviderEnum
from enten.config import Settings

from .protocol import SandboxProvider

_IMMUTABLE_IMAGE: Final = re.compile(r"^(?:sha256:[0-9a-f]{64}|[^@\s]+@sha256:[0-9a-f]{64})$")


@dataclass(frozen=True, slots=True)
class MutableDockerImageError(ValueError):
    image: str

    def __str__(self) -> str:
        return (
            "SANDBOX_DOCKER_IMAGE must be an immutable sha256 image ID or "
            f"name@sha256 digest, not {self.image!r}"
        )


def get_provider(settings: Settings) -> SandboxProvider:
    """Return a configured sandbox provider instance."""
    if settings.sandbox_provider == SandboxProviderEnum.DOCKER:
        from enten.sandbox.docker import DockerSandbox

        image = settings.sandbox_docker_image or DockerSandbox.IMAGE
        if _IMMUTABLE_IMAGE.fullmatch(image) is None:
            raise MutableDockerImageError(image=image)
        return DockerSandbox(image=image)
    if settings.sandbox_provider == SandboxProviderEnum.E2B:
        from enten.sandbox.e2b import SandboxProviderUnavailableError

        raise SandboxProviderUnavailableError(
            provider="E2B",
            action="Select the Docker sandbox provider instead.",
        )
    raise ValueError(f"Unsupported sandbox provider: {settings.sandbox_provider}")
