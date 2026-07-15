"""Docker-backed sandbox provider.

Uses the synchronous ``docker`` SDK wrapped in :func:`asyncio.to_thread` so the
rest of the codebase can stay async-first without pulling in ``aiodocker``.
Containers are created with defence-in-depth flags: read-only rootfs, no new
privileges, all capabilities dropped, no network, resource limits, and a
non-root user.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from functools import partial
from typing import cast

import docker
from anyio import CancelScope, get_cancelled_exc_class
from anyio.lowlevel import checkpoint_if_cancelled
from anyio.to_thread import run_sync
from docker.errors import ImageNotFound, NotFound
from docker.models.containers import Container

from .docker_transport import (
    DockerExecAPI,
    DockerExecOutputLimitError,
    DockerTransportError,
    extract_files_stream,
    run_exec_stream,
)
from .files import UnsafeSandboxPathError, validate_sandbox_files
from .models import ExecResult, SandboxHandle


class DockerSandboxCreationError(RuntimeError):
    def __init__(self, image: str) -> None:
        super().__init__(f"Docker created a container without an id for image {image!r}")


class DockerSandboxTimeoutLimitError(ValueError):
    def __init__(self, timeout_seconds: int, limit: int) -> None:
        super().__init__(f"timeout_seconds ({timeout_seconds}) exceeds max {limit}")


class DockerSandbox:
    """Sandbox provider backed by a local Docker daemon."""

    IMAGE: str = (
        "python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28"
    )
    WORKSPACE: str = "/workspace"
    MAX_TIMEOUT_SECONDS: int = 600
    MAX_OUTPUT_BYTES: int = 8 * 1024 * 1024
    DEFAULT_TMPFS: dict[str, str] = {"/tmp": "rw,noexec,nosuid,nodev,size=128m,mode=1777"}
    WORKSPACE_TMPFS: str = "rw,nosuid,nodev,size=512m,mode=0755,uid=1000,gid=1000"

    def __init__(
        self,
        image: str | None = None,
        network: str = "none",
        tmpfs: dict[str, str] | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        self._client: docker.DockerClient = docker.from_env()
        self.image: str = image or self.IMAGE
        self.network: str = network
        self.tmpfs: dict[str, str] = dict(tmpfs) if tmpfs is not None else dict(self.DEFAULT_TMPFS)
        self.environment: dict[str, str] = environment or {}

    async def create(self, repo_files: dict[str, str]) -> SandboxHandle:
        """Create a hardened container seeded with ``repo_files``."""
        validate_sandbox_files(repo_files)
        created_container: Container | None = None
        completed = False
        try:
            await self._ensure_image()

            container: Container | None = None
            with CancelScope(shield=True):
                container = await run_sync(
                    partial(
                        self._client.containers.run,
                        self.image,
                        command="sleep infinity",
                        entrypoint="",
                        detach=True,
                        auto_remove=True,
                        read_only=True,
                        cap_drop=["ALL"],
                        security_opt=["no-new-privileges:true"],
                        pids_limit=256,
                        mem_limit="4g",
                        nano_cpus=2_000_000_000,
                        user="1000:1000",
                        network=self.network,
                        working_dir=self.WORKSPACE,
                        tmpfs={**self.tmpfs, self.WORKSPACE: self.WORKSPACE_TMPFS},
                        environment=self.environment or None,
                        labels={"enten.sandbox": "true"},
                    )
                )
            if container is None:
                raise DockerSandboxCreationError(image=self.image)
            created_container = container
            container_id = container.id
            if container_id is None:
                raise DockerSandboxCreationError(image=self.image)
            await run_sync(_extract_files, container, repo_files, self.WORKSPACE)
            await checkpoint_if_cancelled()
            handle = SandboxHandle(
                id=container_id,
                provider="docker",
                image=self.image,
            )
            completed = True
            return handle
        finally:
            if not completed and created_container is not None:
                with CancelScope(shield=True):
                    await run_sync(partial(created_container.stop, timeout=10))

    async def run(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        timeout_seconds: int = 120,
    ) -> ExecResult:
        """Execute ``cmd`` inside the container with a wall-clock timeout."""
        if timeout_seconds > self.MAX_TIMEOUT_SECONDS:
            raise DockerSandboxTimeoutLimitError(
                timeout_seconds,
                self.MAX_TIMEOUT_SECONDS,
            )

        start = time.monotonic()
        try:
            container = await run_sync(self._client.containers.get, handle.id)
            client = container.client
            if client is None:
                raise DockerTransportError("Docker container has no client for exec")
            api = cast(DockerExecAPI, client.api)
            result = await run_sync(
                partial(
                    run_exec_stream,
                    api,
                    container_id=handle.id,
                    command=tuple(cmd),
                    timeout_seconds=timeout_seconds,
                    max_output_bytes=self.MAX_OUTPUT_BYTES,
                ),
                abandon_on_cancel=True,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            return ExecResult(
                exit_code=result.exit_code,
                stdout=_decode_output(result.stdout),
                stderr=_decode_output(result.stderr),
                duration_ms=duration_ms,
            )
        except TimeoutError:
            duration_ms = int((time.monotonic() - start) * 1000)
            await self.destroy(handle)
            return ExecResult(
                exit_code=124,
                stdout="",
                stderr="timeout",
                duration_ms=duration_ms,
            )
        except DockerExecOutputLimitError:
            with CancelScope(shield=True):
                await self.destroy(handle)
            raise
        except get_cancelled_exc_class():
            with CancelScope(shield=True):
                await self.destroy(handle)
            raise

    async def upload(self, handle: SandboxHandle, files: dict[str, str | bytes]) -> None:
        """Copy files into a running container's workspace."""
        validate_sandbox_files(files)
        container = await run_sync(self._client.containers.get, handle.id)
        await run_sync(_extract_files, container, files, self.WORKSPACE)

    async def destroy(self, handle: SandboxHandle) -> None:
        """Stop and remove the container."""
        with CancelScope(shield=True):
            try:
                container = await run_sync(
                    self._client.containers.get,
                    handle.id,
                )
            except NotFound:
                return
            await run_sync(partial(container.stop, timeout=10))

    async def _ensure_image(self) -> None:
        """Pull the configured image if it is not already present locally."""
        try:
            _ = await run_sync(self._client.images.get, self.image)
        except ImageNotFound:
            _ = await run_sync(self._client.images.pull, self.image)


def _decode_output(output: bytes) -> str:
    return output.decode("utf-8", errors="replace")


def _extract_files(
    container: Container,
    files: Mapping[str, str | bytes],
    workspace: str,
) -> None:
    client = container.client
    if client is None:
        raise DockerTransportError("Docker container has no client for archive extraction")
    container_id = container.id
    if container_id is None:
        raise DockerSandboxCreationError(image=str(container.image))
    api = cast(DockerExecAPI, client.api)
    extract_files_stream(
        api,
        container_id=container_id,
        files=files,
        workspace=workspace,
    )


__all__ = [
    "DockerExecOutputLimitError",
    "DockerSandbox",
    "DockerSandboxCreationError",
    "DockerSandboxTimeoutLimitError",
    "UnsafeSandboxPathError",
]
