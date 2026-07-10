"""Focused coverage tests for F1 Plan Compliance Audit remediation.

These tests exercise modules that were under-covered without spinning up
external services, so the whole file runs in milliseconds.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from enten.cli import app
from enten.config import Settings
from enten.embeddings import OpenAICompatEmbeddings, get_embeddings
from enten.eval import EvalMetrics, EvalReport
from enten.worker import clean_stale_jobs

runner = CliRunner()


# ─── cli.py ─────────────────────────────────────────────────────────────


def test_cli_worker_invokes_worker_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``worker`` subcommand dispatches to ``enten.worker.main``."""
    main_mock = AsyncMock()
    monkeypatch.setattr("enten.worker.main", main_mock)

    result = runner.invoke(app, ["worker"])
    assert result.exit_code == 0, result.output
    main_mock.assert_awaited_once_with(poll_interval=5)


def test_cli_worker_accepts_poll_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker accepts a positional poll-interval argument."""
    main_mock = AsyncMock()
    monkeypatch.setattr("enten.worker.main", main_mock)

    result = runner.invoke(app, ["worker", "--poll-interval", "10"])
    assert result.exit_code == 0, result.output
    main_mock.assert_awaited_once_with(poll_interval=10)


def _fake_report() -> EvalReport:
    return EvalReport(
        n_fixtures=1,
        by_kind={
            "bug": EvalMetrics(
                precision=0.5,
                recall=0.5,
                false_positive_rate=0.5,
                count=1,
                n_correct=1,
                n_missed=1,
                n_hallucinated=1,
            )
        },
        cost={"fixture_count": 1},
        latency={"total_ms": 10, "mean_ms": 10, "per_fixture": {}},
        determinism={"passed": True},
        baseline=None,
    )


def test_cli_eval_writes_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The ``eval`` subcommand writes the JSON report and exits 0."""
    report = _fake_report()
    monkeypatch.setattr("enten.eval.run_eval", AsyncMock(return_value=report))
    monkeypatch.setattr("enten.eval.write_baseline", MagicMock())

    corpus = tmp_path / "corpus"
    report_path = tmp_path / "report.json"
    result = runner.invoke(app, ["eval", "--corpus", str(corpus), "--report", str(report_path)])

    assert result.exit_code == 0, result.output
    assert report_path.exists()
    assert "Wrote eval report" in result.output


def test_cli_eval_baseline_update(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``--baseline-update`` writes the baseline file."""
    report = _fake_report()
    baseline_path = tmp_path / "baseline.json"
    monkeypatch.setattr("enten.eval.run_eval", AsyncMock(return_value=report))
    monkeypatch.setattr("enten.eval.write_baseline", MagicMock(return_value=baseline_path))

    result = runner.invoke(
        app,
        [
            "eval",
            "--corpus",
            str(tmp_path / "corpus"),
            "--report",
            str(tmp_path / "report.json"),
            "--baseline-update",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Updated baseline" in result.output


def test_cli_eval_threshold_below_precision_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--threshold`` exits 2 when aggregate precision is too low."""
    report = _fake_report()
    monkeypatch.setattr("enten.eval.run_eval", AsyncMock(return_value=report))
    monkeypatch.setattr("enten.eval.write_baseline", MagicMock())

    result = runner.invoke(
        app,
        [
            "eval",
            "--corpus",
            str(tmp_path / "corpus"),
            "--report",
            str(tmp_path / "report.json"),
            "--threshold",
            "0.9",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "below threshold" in result.output


def test_cli_eval_handles_eval_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An ``EvalError`` from the harness exits 1."""
    from enten.eval import EvalError

    async def _raise(*_args: object, **_kwargs: object) -> EvalReport:
        raise EvalError("corpus missing")

    monkeypatch.setattr("enten.eval.run_eval", _raise)

    result = runner.invoke(app, ["eval", "--corpus", str(tmp_path), "--report", str(tmp_path / "r.json")])
    assert result.exit_code == 1, result.output
    assert "Eval error" in result.output


# ─── worker.py ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clean_stale_jobs_returns_fetch_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """``clean_stale_jobs`` returns the number of rows returned by the DB."""
    rows = [{"run_id": "a"}, {"run_id": "b"}]
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows)
    monkeypatch.setattr("enten.worker._get_pool", lambda: pool)

    count = await clean_stale_jobs(threshold_minutes=10)

    assert count == 2
    pool.fetch.assert_awaited_once()
    call_args = pool.fetch.await_args
    assert call_args is not None
    assert call_args.args[2] == 10


@pytest.mark.asyncio
async def test_clean_stale_jobs_uses_default_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default threshold is 15 minutes."""
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    monkeypatch.setattr("enten.worker._get_pool", lambda: pool)

    count = await clean_stale_jobs()

    assert count == 0
    assert pool.fetch.await_args.args[2] == 15


# ─── indexer/__main__.py ────────────────────────────────────────────────


def test_indexer_module_entry_point_calls_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """``python -m enten.indexer`` invokes the Typer app."""
    app_mock = MagicMock()
    monkeypatch.setattr("enten.indexer.cli.app", app_mock)
    monkeypatch.setattr(sys, "argv", ["enten.indexer"])

    _ = runpy.run_module("enten.indexer", run_name="__main__", alter_sys=True)

    app_mock.assert_called_once_with()


# ─── embeddings/_factory.py ─────────────────────────────────────────────


def test_get_embeddings_uses_dedicated_settings() -> None:
    """The factory builds an OpenAI-compatible provider from embedding settings."""
    settings = Settings.model_validate(
        {
            "embedding_base_url": "http://embeddings.test/v1",
            "embedding_api_key": "embed-key",
            "embedding_model": "text-embedding-test",
            "embedding_dimension": 256,
            "embedding_timeout": 5.0,
        }
    )

    provider = get_embeddings(settings)

    assert isinstance(provider, OpenAICompatEmbeddings)
    assert provider.base_url == "http://embeddings.test/v1"
    assert provider.api_key == "embed-key"
    assert provider.model == "text-embedding-test"
    assert provider.dimension == 256


def test_get_embeddings_falls_back_to_llm_settings() -> None:
    """When embedding settings are absent, the factory falls back to LLM URLs."""
    settings = Settings.model_validate(
        {
            "embedding_base_url": None,
            "embedding_api_key": None,
            "llm_openai_base_url": "http://llm.test/v1",
            "llm_openai_api_key": "llm-key",
        }
    )

    provider = get_embeddings(settings)

    assert isinstance(provider, OpenAICompatEmbeddings)
    assert provider.base_url == "http://llm.test/v1"
    assert provider.api_key == "llm-key"


def test_get_embeddings_requires_base_url() -> None:
    """A missing base_url raises a clear ValueError."""
    settings = Settings.model_validate(
        {
            "embedding_base_url": None,
            "llm_openai_base_url": "",
        }
    )

    with pytest.raises(ValueError, match="embedding base_url is required"):
        _ = get_embeddings(settings)


def test_get_embeddings_rejects_unknown_provider() -> None:
    """An unsupported provider name raises ValueError."""
    settings = Settings.model_validate(
        {
            "embedding_provider": "unknown",
            "embedding_base_url": "http://x",
        }
    )

    with pytest.raises(ValueError, match="unknown embedding provider"):
        _ = get_embeddings(settings)
