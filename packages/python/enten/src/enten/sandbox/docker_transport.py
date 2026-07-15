from __future__ import annotations

import io
import socket
import tarfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import NotRequired, Protocol, TypedDict, cast

from typing_extensions import Buffer, override

from .files import safe_relative_path, validate_sandbox_files


@dataclass(frozen=True, slots=True)
class DockerExecOutputLimitError(RuntimeError):
    actual: int
    limit: int

    @override
    def __str__(self) -> str:
        return f"Docker exec output limit exceeded: {self.actual} > {self.limit}"


@dataclass(frozen=True, slots=True)
class DockerTransportError(RuntimeError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class ExecBytes:
    exit_code: int
    stdout: bytes
    stderr: bytes


class ExecCreated(TypedDict):
    Id: NotRequired[str]


class ExecStatus(TypedDict):
    Running: NotRequired[bool]
    ExitCode: NotRequired[int | None]


class DockerExecAPI(Protocol):
    def exec_create(
        self,
        container: str | None,
        cmd: tuple[str, ...],
        *,
        stdin: bool,
        stdout: bool,
        stderr: bool,
    ) -> ExecCreated: ...

    def exec_start(self, exec_id: str, *, socket: bool) -> _SocketStream: ...

    def exec_inspect(self, exec_id: str) -> ExecStatus: ...


class _SocketStream(Protocol):
    def close(self) -> None: ...


class _Socket(Protocol):
    def recv(self, size: int) -> bytes: ...

    def sendall(self, data: bytes) -> None: ...

    def settimeout(self, value: float) -> None: ...

    def shutdown(self, how: int) -> None: ...


class _SocketWriter(io.RawIOBase):
    def __init__(self, output: _Socket) -> None:
        self._output: _Socket = output

    @override
    def writable(self) -> bool:
        return True

    @override
    def write(self, data: Buffer) -> int:
        content = bytes(data)
        self._output.sendall(content)
        return len(content)


def run_exec_stream(
    api: DockerExecAPI,
    *,
    container_id: str,
    command: tuple[str, ...],
    timeout_seconds: int,
    max_output_bytes: int,
) -> ExecBytes:
    created = api.exec_create(
        container_id,
        command,
        stdin=False,
        stdout=True,
        stderr=True,
    )
    exec_id = _exec_id(created)
    stream = api.exec_start(exec_id, socket=True)
    raw_socket = _raw_socket(stream)
    deadline = time.monotonic() + timeout_seconds
    pending = bytearray()
    stdout = bytearray()
    stderr = bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Docker exec timed out")
            raw_socket.settimeout(min(remaining, 0.25))
            try:
                chunk = raw_socket.recv(64 * 1024)
            except TimeoutError:
                continue
            if not chunk:
                break
            pending.extend(chunk)
            _consume_frames(pending, stdout, stderr, max_output_bytes)
        if pending:
            raise DockerTransportError("Docker exec returned a truncated output frame")
        exit_code = _wait_for_exit(api, exec_id, deadline)
        return ExecBytes(exit_code=exit_code, stdout=bytes(stdout), stderr=bytes(stderr))
    finally:
        stream.close()


def extract_files_stream(
    api: DockerExecAPI,
    *,
    container_id: str,
    files: Mapping[str, str | bytes],
    workspace: str,
    timeout_seconds: int = 30,
) -> None:
    validate_sandbox_files(files)
    script = (
        "import sys,tarfile; "
        "tarfile.open(fileobj=sys.stdin.buffer,mode='r|')"
        ".extractall(sys.argv[1],filter='data')"
    )
    created = api.exec_create(
        container_id,
        ("python", "-c", script, workspace),
        stdin=True,
        stdout=False,
        stderr=False,
    )
    exec_id = _exec_id(created)
    stream = api.exec_start(exec_id, socket=True)
    raw_socket = _raw_socket(stream)
    deadline = time.monotonic() + timeout_seconds
    try:
        raw_socket.settimeout(timeout_seconds)
        writer = _SocketWriter(raw_socket)
        with tarfile.open(fileobj=writer, mode="w|") as archive:
            for raw_path, content in files.items():
                data = content if isinstance(content, bytes) else content.encode("utf-8")
                info = tarfile.TarInfo(safe_relative_path(raw_path).as_posix())
                info.size = len(data)
                info.mode = 0o644
                info.uid = 1000
                info.gid = 1000
                archive.addfile(info, io.BytesIO(data))
        raw_socket.shutdown(socket.SHUT_WR)
        exit_code = _wait_for_exit(api, exec_id, deadline)
        if exit_code != 0:
            raise DockerTransportError(
                f"sandbox archive extraction failed with exit code {exit_code}"
            )
    finally:
        stream.close()


def _consume_frames(
    pending: bytearray,
    stdout: bytearray,
    stderr: bytearray,
    limit: int,
) -> None:
    while len(pending) >= 8:
        size = int.from_bytes(pending[4:8], "big")
        if size > limit:
            raise DockerExecOutputLimitError(actual=size, limit=limit)
        if len(pending) < size + 8:
            return
        stream_type = pending[0]
        content = bytes(pending[8 : size + 8])
        del pending[: size + 8]
        actual = len(stdout) + len(stderr) + len(content)
        if actual > limit:
            raise DockerExecOutputLimitError(actual=actual, limit=limit)
        if stream_type == 1:
            stdout.extend(content)
        elif stream_type == 2:
            stderr.extend(content)
        else:
            raise DockerTransportError(f"Docker exec returned stream type {stream_type}")


def _exec_id(created: ExecCreated) -> str:
    exec_id = created.get("Id")
    if not isinstance(exec_id, str):
        raise DockerTransportError("Docker returned no exec id")
    return exec_id


def _raw_socket(stream: _SocketStream) -> _Socket:
    return cast(_Socket, getattr(stream, "_sock", stream))


def _wait_for_exit(api: DockerExecAPI, exec_id: str, deadline: float) -> int:
    while True:
        status = api.exec_inspect(exec_id)
        if status.get("Running", False) is not True:
            exit_code = status.get("ExitCode")
            if not isinstance(exit_code, int):
                raise DockerTransportError("Docker exec returned no exit code")
            return exit_code
        if time.monotonic() >= deadline:
            raise TimeoutError("Docker exec timed out")
        time.sleep(0.05)


__all__ = [
    "DockerExecAPI",
    "DockerExecOutputLimitError",
    "DockerTransportError",
    "ExecCreated",
    "ExecBytes",
    "ExecStatus",
    "extract_files_stream",
    "run_exec_stream",
]
