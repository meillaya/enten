"""Docker-backed sandbox provider.

Uses the synchronous ``docker`` SDK wrapped in :func:`asyncio.to_thread` so the
rest of the codebase can stay async-first without pulling in ``aiodocker``.
Containers are created with defence-in-depth flags: read-only rootfs, no new
privileges, all capabilities dropped, no network, resource limits, and a
non-root user.
"""

from __future__ import annotations

import io
import socket
import tarfile
import time
from collections.abc import Mapping
from functools import partial
from typing import Protocol, cast

import docker
from anyio import CancelScope, fail_after, get_cancelled_exc_class
from anyio.lowlevel import checkpoint_if_cancelled
from anyio.to_thread import run_sync
from docker.errors import ImageNotFound, NotFound
from docker.models.containers import Container

from .files import UnsafeSandboxPathError, safe_relative_path
from .models import ExecResult, SandboxHandle


class DockerSandboxCreationError(RuntimeError):
    def __init__(self, image: str) -> None:
        super().__init__(f"Docker created a container without an id for image {image!r}")


class _SocketStream(Protocol):
    def close(self) -> None: ...


class _DockerExecAPI(Protocol):
    def exec_create(
        self,
        container: str | None,
        cmd: tuple[str, ...],
        *,
        stdin: bool,
        stdout: bool,
        stderr: bool,
    ) -> dict[str, object]: ...

    def exec_start(self, exec_id: str, *, socket: bool) -> _SocketStream: ...

    def exec_inspect(self, exec_id: str) -> dict[str, object]: ...


class DockerSandbox:
    """Sandbox provider backed by a local Docker daemon."""

    IMAGE: str = (
        "python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28"
    )
    WORKSPACE: str = "/workspace"
    MAX_TIMEOUT_SECONDS: int = 600
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
        repository_archive = _archive(repo_files)
        created_container: Container | None = None
        try:
            await self._ensure_image()

            container: Container | None = None
            with CancelScope(shield=True):
                container = await run_sync(
                    partial(
                        self._client.containers.run,
                        self.image,
                        command="sleep infinity",
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
            await run_sync(_extract_archive, container, repository_archive)
            await checkpoint_if_cancelled()
            return SandboxHandle(
                id=container_id,
                provider="docker",
                image=self.image,
            )
        except BaseException:
            if created_container is not None:
                with CancelScope(shield=True):
                    await run_sync(partial(created_container.stop, timeout=10))
            raise

    async def run(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        timeout_seconds: int = 120,
    ) -> ExecResult:
        """Execute ``cmd`` inside the container with a wall-clock timeout."""
        if timeout_seconds > self.MAX_TIMEOUT_SECONDS:
            raise ValueError(
                f"timeout_seconds ({timeout_seconds}) exceeds max {self.MAX_TIMEOUT_SECONDS}"
            )

        start = time.monotonic()
        try:
            container = await run_sync(self._client.containers.get, handle.id)
            with fail_after(timeout_seconds):
                result = await run_sync(
                    partial(container.exec_run, cmd, demux=True),
                    abandon_on_cancel=True,
                )
            duration_ms = int((time.monotonic() - start) * 1000)
            exit_code = result.exit_code
            if exit_code is None:
                raise RuntimeError("Docker exec returned no exit code")
            output = cast(bytes | tuple[bytes | None, bytes | None] | None, result.output)
            stdout_bytes, stderr_bytes = _output_pair(output)
            return ExecResult(
                exit_code=exit_code,
                stdout=_decode_output(stdout_bytes),
                stderr=_decode_output(stderr_bytes),
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
        except get_cancelled_exc_class():
            with CancelScope(shield=True):
                await self.destroy(handle)
            raise

    async def upload(self, handle: SandboxHandle, files: dict[str, str | bytes]) -> None:
        """Copy files into a running container's workspace."""
        archive = _archive(files)
        container = await run_sync(self._client.containers.get, handle.id)
        await run_sync(_extract_archive, container, archive)

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


def _decode_output(output: bytes | None) -> str:
    return "" if output is None else output.decode("utf-8", errors="replace")


def _output_pair(
    output: bytes | tuple[bytes | None, bytes | None] | None,
) -> tuple[bytes | None, bytes | None]:
    return output if isinstance(output, tuple) else (output, None)


def _archive(files: Mapping[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for raw_path, content in files.items():
            data = content if isinstance(content, bytes) else content.encode("utf-8")
            info = tarfile.TarInfo(safe_relative_path(raw_path).as_posix())
            info.size = len(data)
            info.mode = 0o644
            info.uid = 1000
            info.gid = 1000
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _extract_archive(container: Container, archive: bytes) -> None:
    script = "".join(
        (
            "import sys,tarfile; ",
            "tarfile.open(fileobj=sys.stdin.buffer,mode='r|')",
            ".extractall('/workspace',filter='data')",
        )
    )
    command = ("python", "-c", script)
    client = container.client
    if client is None:
        raise RuntimeError("Docker container has no client for archive extraction")
    api = cast(_DockerExecAPI, cast(object, client.api))
    created = api.exec_create(
        container.id,
        command,
        stdin=True,
        stdout=False,
        stderr=False,
    )
    exec_id = created.get("Id")
    if not isinstance(exec_id, str):
        raise RuntimeError("Docker returned no exec id for sandbox archive extraction")
    stream = api.exec_start(exec_id, socket=True)
    raw_socket = cast(socket.socket, vars(stream)["_sock"])
    try:
        raw_socket.sendall(archive)
        raw_socket.shutdown(socket.SHUT_WR)
        deadline = time.monotonic() + 30
        while True:
            status = api.exec_inspect(exec_id)
            if status.get("Running", False) is not True:
                if status.get("ExitCode") != 0:
                    raise RuntimeError("sandbox archive extraction failed")
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("sandbox archive extraction timed out")
            time.sleep(0.05)
    finally:
        stream.close()


__all__ = ["DockerSandbox", "DockerSandboxCreationError", "UnsafeSandboxPathError"]
