"""Tests for the Docker-backed sandbox provider.

These tests require a running Docker daemon. They are skipped automatically
when Docker is unavailable so CI nodes without Docker can still run the rest
of the suite.
"""

from __future__ import annotations

import inspect
import json
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Self, cast, final
from uuid import uuid4

import anyio
import docker
import pytest
from docker.errors import NotFound

from enten.sandbox import SandboxHandle
from enten.sandbox.docker import DockerExecOutputLimitError, DockerSandbox


def _docker_available() -> bool:
    return (
        shutil.which("docker") is not None
        and subprocess.run(
            ["docker", "info"],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


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
async def test_run_separates_stdout_and_stderr() -> None:
    async with _fresh_sandbox() as (handle, provider):
        result = await provider.run(
            handle,
            ["sh", "-c", "printf output; printf error >&2"],
        )

    assert result.stdout == "output"
    assert result.stderr == "error"


@pytest.mark.asyncio
async def test_run_destroys_container_on_combined_output_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(DockerSandbox, "MAX_OUTPUT_BYTES", 64 * 1024)
    provider = DockerSandbox()
    handle = await provider.create({})

    with pytest.raises(DockerExecOutputLimitError):
        await provider.run(
            handle,
            ["python", "-c", "import os; os.write(1, b'x' * 70000)"],
        )
    running = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"id={handle.id}"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert running.stdout.strip() == ""


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
async def test_destroy_tolerates_auto_remove_race() -> None:
    class RaceContainer:
        @staticmethod
        def stop(*, timeout: int) -> None:
            assert timeout == 10
            raise NotFound("container was already auto-removed")

    class RaceContainers:
        @staticmethod
        def get(container_id: str) -> RaceContainer:
            assert container_id == "already-removed"
            return RaceContainer()

    @final
    class RaceClient:
        containers: RaceContainers = RaceContainers()

    class RaceDockerSandbox(DockerSandbox):
        @classmethod
        def with_race_client(cls) -> Self:
            provider = cls.__new__(cls)
            provider._client = cast(docker.DockerClient, cast(object, RaceClient()))
            return provider

    await RaceDockerSandbox.with_race_client().destroy(
        SandboxHandle(id="already-removed", provider="docker", image="image")
    )


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
    assert config["Config"]["Labels"]["enten.sandbox"] == "true"


@pytest.mark.asyncio
async def test_sandbox_has_bounded_writable_tmpfs() -> None:
    async with _fresh_sandbox() as (handle, provider):
        result = await provider.run(
            handle,
            ["sh", "-c", "printf ok >/tmp/report && cat /tmp/report"],
        )

    assert result.exit_code == 0
    assert result.stdout == "ok"


@pytest.mark.asyncio
async def test_workspace_is_tmpfs_instead_of_a_host_bind() -> None:
    async with _fresh_sandbox() as (handle, _provider):
        inspected = subprocess.run(
            ["docker", "inspect", handle.id],
            capture_output=True,
            text=True,
            check=True,
        )
        config = json.loads(inspected.stdout)[0]

    assert "/workspace" in config["HostConfig"]["Tmpfs"]
    assert all(mount["Destination"] != "/workspace" for mount in config["Mounts"])


@pytest.mark.asyncio
async def test_upload_copies_files_into_container():
    async with _fresh_sandbox() as (handle, provider):
        await provider.upload(handle, {"nested/hello.txt": "world"})
        result = await provider.run(handle, ["cat", "/workspace/nested/hello.txt"])

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


@pytest.mark.asyncio
async def test_create_rejects_parent_path_escape() -> None:
    provider = DockerSandbox()
    escaped = Path("/tmp") / f"enten-escape-{uuid4().hex}"
    handle = None
    try:
        with pytest.raises(ValueError, match="relative"):
            handle = await provider.create({f"../{escaped.name}": "do not write"})
    finally:
        if handle is not None:
            await provider.destroy(handle)
        escaped.unlink(missing_ok=True)

    assert not escaped.exists()


@pytest.mark.asyncio
async def test_upload_rejects_absolute_path_escape(tmp_path: Path) -> None:
    async with _fresh_sandbox() as (handle, provider):
        escaped = tmp_path / "escaped.txt"
        with pytest.raises(ValueError, match="relative"):
            await provider.upload(handle, {str(escaped): "do not write"})

    assert not escaped.exists()


@pytest.mark.asyncio
async def test_cancelled_run_destroys_container() -> None:
    provider = DockerSandbox()
    handle = await provider.create({})

    try:
        with anyio.move_on_after(0.2) as cancel_scope:
            _ = await provider.run(handle, ["sleep", "30"])
        ps = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"id={handle.id}"],
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        await provider.destroy(handle)

    assert cancel_scope.cancel_called
    assert ps.stdout.strip() == ""


def test_default_sandbox_image_is_digest_pinned() -> None:
    assert DockerSandbox.IMAGE.startswith("python:3.12-slim@sha256:")


def test_sandbox_runtime_does_not_require_docker_cli() -> None:
    source = inspect.getsource(DockerSandbox)
    assert "subprocess.run" not in source
    assert '"docker", "exec"' not in source
    assert '"docker", "cp"' not in source
