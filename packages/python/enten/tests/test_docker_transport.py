from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from enten.sandbox.docker_transport import (
    DockerExecOutputLimitError,
    DockerTransportError,
    ExecCreated,
    ExecStatus,
    extract_files_stream,
    run_exec_stream,
)


def _frame(stream: int, content: bytes) -> bytes:
    return bytes((stream, 0, 0, 0)) + len(content).to_bytes(4, "big") + content


@dataclass(slots=True)
class _FakeSocket:
    chunks: list[bytes]
    timeout: float | None = None
    sent: bytearray = field(default_factory=bytearray)

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def recv(self, _size: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def shutdown(self, _how: int) -> None:
        return


@dataclass(slots=True)
class _FakeStream:
    _sock: _FakeSocket
    closed: bool = False

    def close(self) -> None:
        self.closed = True

    @property
    def sent(self) -> bytearray:
        return self._sock.sent


@dataclass(slots=True)
class _FakeAPI:
    stream: _FakeStream
    created_commands: list[tuple[str, ...]] = field(default_factory=list)

    def exec_create(
        self,
        container: str | None,
        cmd: tuple[str, ...],
        *,
        stdin: bool,
        stdout: bool,
        stderr: bool,
    ) -> ExecCreated:
        assert container
        assert stdin or stdout or stderr
        self.created_commands.append(cmd)
        return {"Id": "exec-1"}

    def exec_start(self, exec_id: str, *, socket: bool) -> _FakeStream:
        assert exec_id
        assert socket
        return self.stream

    def exec_inspect(self, exec_id: str) -> ExecStatus:
        assert exec_id
        return {"Running": False, "ExitCode": 7}


def test_run_exec_stream_demultiplexes_split_frames() -> None:
    payload = _frame(1, b"out") + _frame(2, b"err")
    stream = _FakeStream(_FakeSocket([payload[:5], payload[5:11], payload[11:], b""]))
    api = _FakeAPI(stream)

    result = run_exec_stream(
        api,
        container_id="container-1",
        command=("tool", "check"),
        timeout_seconds=5,
        max_output_bytes=64,
    )

    assert result.exit_code == 7
    assert result.stdout == b"out"
    assert result.stderr == b"err"
    assert stream.closed


def test_run_exec_stream_rejects_combined_output_overflow() -> None:
    payload = _frame(1, b"1234") + _frame(2, b"5678")
    stream = _FakeStream(_FakeSocket([payload, b""]))
    api = _FakeAPI(stream)

    with pytest.raises(DockerExecOutputLimitError) as caught:
        _ = run_exec_stream(
            api,
            container_id="container-1",
            command=("noisy",),
            timeout_seconds=5,
            max_output_bytes=7,
        )

    assert caught.value.actual == 8
    assert caught.value.limit == 7
    assert stream.closed


def test_run_exec_stream_rejects_oversized_declared_frame_before_body() -> None:
    header = bytes((1, 0, 0, 0)) + (1_000_000).to_bytes(4, "big")
    stream = _FakeStream(_FakeSocket([header]))
    api = _FakeAPI(stream)

    with pytest.raises(DockerExecOutputLimitError) as caught:
        _ = run_exec_stream(
            api,
            container_id="container-1",
            command=("noisy",),
            timeout_seconds=5,
            max_output_bytes=1024,
        )

    assert caught.value.actual == 1_000_000
    assert stream.closed


def test_extract_files_stream_rejects_nonzero_extractor_exit() -> None:
    stream = _FakeStream(_FakeSocket([]))
    api = _FakeAPI(stream)

    with pytest.raises(DockerTransportError, match="extraction failed"):
        extract_files_stream(
            api,
            container_id="container-1",
            files={"source.txt": "content"},
            workspace="/workspace",
        )

    assert stream.sent
    assert stream.closed
