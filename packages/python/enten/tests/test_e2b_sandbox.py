"""Tests for the E2B-backed sandbox provider.

All E2B HTTP calls are mocked with ``respx``; no live E2B account is required.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from enten.config import SandboxProvider, Settings
from enten.sandbox.e2b import E2BSandbox, SandboxAuthenticationError
from enten.sandbox.factory import get_provider


def _e2b_settings(api_key: str = "test-e2b-key") -> Settings:
    settings = Settings()
    settings.sandbox_provider = SandboxProvider.E2B
    settings.e2b_api_key = api_key
    return settings


@respx.mock(assert_all_mocked=True)
async def test_create_run_destroy_lifecycle(respx_mock: respx.MockRouter) -> None:
    """A full create → run → destroy flow succeeds against mocked E2B API."""
    sandbox_id = "sbx-123"
    _ = respx_mock.post(path="/sandboxes").mock(
        return_value=Response(201, json={"sandbox_id": sandbox_id})
    )
    _ = respx_mock.post(path=f"/sandboxes/{sandbox_id}/execute").mock(
        return_value=Response(
            200,
            json={
                "exit_code": 0,
                "stdout": "hello",
                "stderr": "",
            },
        )
    )
    _ = respx_mock.delete(path=f"/sandboxes/{sandbox_id}").mock(return_value=Response(204))

    provider = E2BSandbox(_e2b_settings())
    try:
        handle = await provider.create({})
        _ = handle
        assert handle.id == sandbox_id
        assert handle.provider == "e2b"
        assert handle.image == "e2b-default"

        result = await provider.run(handle, ["echo", "hello"])
        assert result.exit_code == 0
        assert result.stdout == "hello"
        assert result.stderr == ""
        assert result.duration_ms >= 0

        await provider.destroy(handle)
    finally:
        await provider.close()


def test_factory_returns_e2b_instance() -> None:
    """``get_provider`` selects ``E2BSandbox`` when the settings ask for ``e2b``."""
    settings = _e2b_settings()
    provider = get_provider(settings)
    assert isinstance(provider, E2BSandbox)


@respx.mock(assert_all_mocked=True)
async def test_401_raises_actionable_authentication_error(
    respx_mock: respx.MockRouter,
) -> None:
    """A 401 from E2B surfaces a domain-specific, actionable auth error."""
    _ = respx_mock.post(path="/sandboxes").mock(
        return_value=Response(401, json={"code": 401, "message": "Unauthorized"})
    )

    provider = E2BSandbox(_e2b_settings())
    with pytest.raises(
        SandboxAuthenticationError,
        match=r"e2b\.dev.*Settings\(\)\.e2b_api_key",
    ):
        _ = await provider.create({})
    await provider.close()
