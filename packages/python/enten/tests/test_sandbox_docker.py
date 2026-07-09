"""Tests for the Docker-backed sandbox provider.

These tests require a running Docker daemon. They are skipped automatically
when Docker is unavailable so CI nodes without Docker can still run the rest
of the suite.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import asynccontextmanager

import pytest

from enten.sandbox import SandboxHandle
from enten.sandbox.docker import DockerSandbox


def _docker_available() -> bool:
    return shutil.which("docker") is not None and subprocess.run(
        ["docker", "info"],
        capture_output=True,
        check=False,
    ).returncode == 0


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker daemon not available",
)


@asynccontextmanager
async def _fresh_sandbox(repo_files: dict[str, str] | None = None):
    provider = DockerSandbox()
    handle = await provider.create(repo_files or {})
    try:
        yield handle, provider
    finally:
        await provider.destroy(handle)


@pytest.mark.asyncio
async def test_run_prints_hi():
    async with _fresh_sandbox() as (handle, provider):
        result = await provider.run(handle, ["python", "-c", "print('hi')"])

    assert result.exit_code == 0
    assert result.stdout == "hi\n"
    assert result.stderr == ""
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_run_times_out():
    async with _fresh_sandbox() as (handle, provider):
        result = await provider.run(handle, ["sleep", "10"], timeout_seconds=1)

    assert result.exit_code == 124
    assert result.stderr == "timeout"
    assert 500 <= result.duration_ms <= 3000


@pytest.mark.asyncio
async def test_destroy_removes_container():
    provider = DockerSandbox()
    handle = await provider.create({})
    await provider.destroy(handle)

    ps = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"id={handle.id}"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert ps.stdout.strip() == ""


@pytest.mark.asyncio
async def test_container_hardening_drops_capabilities():
    async with _fresh_sandbox() as (handle, _provider):
        inspect = subprocess.run(
            ["docker", "inspect", handle.id],
            capture_output=True,
            text=True,
            check=True,
        )
        config = json.loads(inspect.stdout)[0]

    cap_drop = config.get("HostConfig", {}).get("CapDrop", [])
    assert "ALL" in cap_drop


@pytest.mark.asyncio
async def test_upload_copies_files_into_container():
    async with _fresh_sandbox() as (handle, provider):
        await provider.upload(handle, {"hello.txt": "world"})
        result = await provider.run(handle, ["cat", "/workspace/hello.txt"])

    assert result.exit_code == 0
    assert result.stdout == "world"


@pytest.mark.asyncio
async def test_run_rejects_excessive_timeout():
    async with _fresh_sandbox() as (handle, provider):
        with pytest.raises(ValueError, match="exceeds max"):
            await provider.run(handle, ["true"], timeout_seconds=601)


@pytest.mark.asyncio
async def test_create_seeds_repo_files():
    async with _fresh_sandbox({"src/main.py": "print(42)"}) as (handle, provider):
        result = await provider.run(handle, ["python", "/workspace/src/main.py"])

    assert result.exit_code == 0
    assert result.stdout == "42\n"


@pytest.mark.asyncio
async def test_sandbox_handle_model():
    handle = SandboxHandle(id="abc123", provider="docker", image="python:3.12-slim")
    assert handle.id == "abc123"
    assert handle.provider == "docker"
    assert handle.image == "python:3.12-slim"
