from __future__ import annotations

from dataclasses import dataclass

from enten.config import Settings


@dataclass(frozen=True, slots=True)
class SandboxProviderUnavailableError(RuntimeError):
    provider: str
    action: str

    def __str__(self) -> str:
        return (
            f"{self.provider} sandbox provider is unavailable because its API contract "
            f"has not been verified. {self.action}"
        )


class E2BSandbox:
    def __init__(self, settings: Settings) -> None:
        _ = settings
        raise SandboxProviderUnavailableError(
            provider="E2B",
            action="Select the Docker sandbox provider instead.",
        )


__all__ = ["E2BSandbox", "SandboxProviderUnavailableError"]
