from __future__ import annotations

import pytest

import health


class _HealthyResponse:
    status = 200

    @staticmethod
    def read() -> bytes:
        return b'{"status":"ok"}'


class _CapturingConnection:
    observed_port: int | None = None

    def __init__(self, _host: str, port: int, *, timeout: int) -> None:
        del timeout
        type(self).observed_port = port

    def request(self, _method: str, _path: str) -> None:
        return None

    def getresponse(self) -> _HealthyResponse:
        return _HealthyResponse()

    def close(self) -> None:
        return None


def test_health_probe_defaults_to_edge_metrics_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("METRICS_PORT", raising=False)
    monkeypatch.setattr(health.http.client, "HTTPConnection", _CapturingConnection)

    assert health.main() == 0
    assert _CapturingConnection.observed_port == 9091
