"""E2B cloud sandbox provider.

Talks to the E2B REST API directly with ``httpx`` (the official ``e2b`` SDK is
intentionally *not* used so the codebase can keep broad dependency coverage).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import httpx

from enten.config import Settings

from .models import ExecResult, SandboxHandle


class SandboxAuthenticationError(Exception):
    """Raised when E2B rejects the configured API key."""


class E2BSandbox:
    """Sandbox provider backed by the E2B cloud sandbox API."""

    BASE_URL: str = "https://api.e2b.dev"
    DEFAULT_IMAGE: str = "e2b-default"
    DEFAULT_TEMPLATE: str = "base"

    def __init__(self, settings: Settings) -> None:
        self._api_key: str | None = settings.e2b_api_key
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers=self._auth_headers(),
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0),
        )

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key or ''}"}

    async def create(self, repo_files: dict[str, str]) -> SandboxHandle:
        """Create a fresh E2B sandbox and seed it with ``repo_files``."""
        response = await self._request(
            "POST",
            "/sandboxes",
            json={"template": self.DEFAULT_TEMPLATE},
        )
        if response is None:
            raise RuntimeError("E2B create sandbox returned an empty response")
        sandbox_id: str = response["sandbox_id"]
        handle = SandboxHandle(
            id=sandbox_id,
            provider="e2b",
            image=self.DEFAULT_IMAGE,
        )
        if repo_files:
            await self.upload(handle, repo_files)
        return handle

    async def run(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        timeout_seconds: int = 120,
    ) -> ExecResult:
        """Run ``cmd`` inside the E2B sandbox and capture the result."""
        start = time.monotonic()
        response = await self._request(
            "POST",
            f"/sandboxes/{handle.id}/execute",
            json={
                "command": " ".join(cmd),
                "timeout": timeout_seconds,
            },
            timeout=httpx.Timeout(
                connect=10.0,
                read=timeout_seconds + 10.0,
                write=10.0,
                pool=10.0,
            ),
        )
        if response is None:
            raise RuntimeError("E2B execute command returned an empty response")
        duration_ms = int((time.monotonic() - start) * 1000)
        return ExecResult(
            exit_code=int(response.get("exit_code", -1)),
            stdout=str(response.get("stdout", "")),
            stderr=str(response.get("stderr", "")),
            duration_ms=duration_ms,
        )

    async def upload(
        self,
        handle: SandboxHandle,
        files: Mapping[str, str | bytes],
    ) -> None:
        """Upload ``files`` into the running E2B sandbox."""
        multipart: dict[str, Any] = {}
        for path, content in files.items():
            multipart[path] = (
                path,
                content.encode("utf-8") if isinstance(content, str) else content,
            )

        _ = await self._request(
            "POST",
            f"/sandboxes/{handle.id}/files",
            files=multipart,
        )

    async def destroy(self, handle: SandboxHandle) -> None:
        """Delete the E2B sandbox."""
        try:
            _ = await self._request("DELETE", f"/sandboxes/{handle.id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return
            raise

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> dict[str, Any] | None:
        """Make an authenticated E2B API request and decode JSON."""
        try:
            response = await self._client.request(
                method,
                path,
                json=json,
                files=files,
                timeout=timeout,
            )
            _ = response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise SandboxAuthenticationError(
                    "E2B authentication failed: check your API key at https://e2b.dev "
                    + "and ensure Settings().e2b_api_key is set."
                ) from exc
            raise

        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def close(self) -> None:
        """Close the underlying HTTP client (useful in tests)."""
        await self._client.aclose()
