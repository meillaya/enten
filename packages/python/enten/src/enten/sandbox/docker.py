"""Docker-backed sandbox provider.

Uses the synchronous ``docker`` SDK wrapped in :func:`asyncio.to_thread` so the
rest of the codebase can stay async-first without pulling in ``aiodocker``.
Containers are created with defence-in-depth flags: read-only rootfs, no new
privileges, all capabilities dropped, no network, resource limits, and a
non-root user.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import docker
from docker.errors import ImageNotFound, NotFound

from .models import ExecResult, SandboxHandle


class DockerSandbox:
    """Sandbox provider backed by a local Docker daemon."""

    IMAGE: str = "python:3.12-slim"
    WORKSPACE: str = "/workspace"
    MAX_TIMEOUT_SECONDS: int = 600

    def __init__(self, image: str | None = None, network: str = "none") -> None:
        self._client: docker.DockerClient = docker.from_env()
        self.image: str = image or self.IMAGE
        self.network: str = network

    async def create(self, repo_files: dict[str, str]) -> SandboxHandle:
        """Create a hardened container seeded with ``repo_files``."""
        tmpdir = Path(tempfile.mkdtemp(prefix="enten-sandbox-"))
        try:
            for rel_path, content in repo_files.items():
                target = tmpdir / rel_path
                _ = target.parent.mkdir(parents=True, exist_ok=True)
                _ = target.write_text(content, encoding="utf-8")

            await self._ensure_image()

            container = await asyncio.to_thread(
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
                volumes={str(tmpdir): {"bind": self.WORKSPACE, "mode": "rw"}},
                working_dir=self.WORKSPACE,
            )
            return SandboxHandle(
                id=container.id,
                provider="docker",
                image=self.image,
            )
        except Exception:
            shutil.rmtree(tmpdir, ignore_errors=True)
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

        exec_cmd = ["docker", "exec", handle.id, *cmd]
        start = time.monotonic()
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                exec_cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            return ExecResult(
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            await self.destroy(handle)
            return ExecResult(
                exit_code=124,
                stdout=exc.stdout.decode("utf-8", errors="replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or ""),
                stderr="timeout",
                duration_ms=duration_ms,
            )

    async def upload(self, handle: SandboxHandle, files: dict[str, str | bytes]) -> None:
        """Copy files into a running container's workspace."""
        tmpdir = Path(tempfile.mkdtemp(prefix="enten-upload-"))
        try:
            for rel_path, content in files.items():
                target = tmpdir / rel_path
                _ = target.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, bytes):
                    _ = target.write_bytes(content)
                else:
                    _ = target.write_text(content, encoding="utf-8")

            cp_cmd = [
                "docker",
                "cp",
                f"{tmpdir}/.",
                f"{handle.id}:{self.WORKSPACE}/",
            ]
            proc = await asyncio.to_thread(
                subprocess.run,
                cp_cmd,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"docker cp failed: {proc.stderr.strip() or proc.stdout.strip()}"
                )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def destroy(self, handle: SandboxHandle) -> None:
        """Stop and remove the container."""
        try:
            container = await asyncio.to_thread(
                self._client.containers.get,
                handle.id,
            )
        except NotFound:
            return

        await asyncio.to_thread(container.stop, timeout=10)

    async def _ensure_image(self) -> None:
        """Pull the configured image if it is not already present locally."""
        try:
            _ = await asyncio.to_thread(self._client.images.get, self.image)
        except ImageNotFound:
            _ = await asyncio.to_thread(self._client.images.pull, self.image)
