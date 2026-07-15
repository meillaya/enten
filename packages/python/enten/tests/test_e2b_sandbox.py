from __future__ import annotations

import pytest

from enten.config import SandboxProvider, Settings
from enten.sandbox.factory import get_provider


def _e2b_settings(api_key: str = "test-e2b-key") -> Settings:
    settings = Settings()
    settings.sandbox_provider = SandboxProvider.E2B
    settings.e2b_api_key = api_key
    return settings


def test_factory_fails_closed_for_unverified_e2b_provider() -> None:
    settings = _e2b_settings()

    with pytest.raises(RuntimeError, match="E2B.*Docker") as caught:
        _ = get_provider(settings)

    assert type(caught.value).__name__ == "SandboxProviderUnavailableError"


def test_factory_passes_operator_selected_docker_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DockerSandbox:
        def __init__(self, image: str | None = None) -> None:
            self.image = image

    monkeypatch.setattr("enten.sandbox.docker.DockerSandbox", DockerSandbox)
    settings = Settings(sandbox_docker_image="example.test/verifier@sha256:" + "a" * 64)

    provider = get_provider(settings)

    assert isinstance(provider, DockerSandbox)
    assert provider.image == settings.sandbox_docker_image
