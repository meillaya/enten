from __future__ import annotations

import pytest

from enten.config import SandboxProvider, Settings
from enten.sandbox.factory import get_provider


def _e2b_settings() -> Settings:
    settings = Settings()
    settings.sandbox_provider = SandboxProvider.E2B
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


@pytest.mark.parametrize(
    "image",
    [
        "enten-verifier-httpie:3.2.4",
        "enten-verifier-httpie:latest",
        "example.test/verifier",
        "example.test/verifier@sha256:not-a-digest",
    ],
)
def test_factory_rejects_mutable_docker_image_before_client_creation(
    image: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_created = False

    class DockerSandbox:
        def __init__(self, image: str | None = None) -> None:
            nonlocal client_created
            client_created = True

    monkeypatch.setattr("enten.sandbox.docker.DockerSandbox", DockerSandbox)
    settings = Settings(sandbox_docker_image=image)

    with pytest.raises(ValueError, match="immutable"):
        _ = get_provider(settings)

    assert not client_created


def test_factory_accepts_exact_docker_image_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DockerSandbox:
        def __init__(self, image: str | None = None) -> None:
            self.image = image

    monkeypatch.setattr("enten.sandbox.docker.DockerSandbox", DockerSandbox)
    image_id = "sha256:" + "b" * 64

    provider = get_provider(Settings(sandbox_docker_image=image_id))

    assert isinstance(provider, DockerSandbox)
    assert provider.image == image_id
