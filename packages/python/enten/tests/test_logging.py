"""Tests for the structured logging module.

These tests pin the contract documented in :mod:`enten.logging`:

- ``configure_logging()`` produces one JSON object per line on stdout.
- ``bind_context(...)`` flows into every subsequent log call of the
  same task (here, the same thread, since structlog's contextvars are
  ContextVar-backed and survive across event loops in a thread).
- The redaction processor wraps :func:`enten.redaction.redact_dict`
  so the rule is single-sourced (T03).
- ``new_run_id()`` returns unique ids over 1000 consecutive calls.
"""

from __future__ import annotations

import json
import re

import pytest

# Pattern from the spec — used across both test modules.
_RUN_ID_RE = re.compile(r"^pr_[a-z0-9]{12}$")


@pytest.fixture(autouse=True)
def _configure_logging():
    """Reset structlog + contextvars for every test.

    autouse=True means this runs even for tests that don't ask for it,
    because structlog's configuration is process-global and a test that
    leaks a handler from a previous run would silently double-emit.
    """
    from enten.logging import clear_context, configure_logging

    configure_logging()
    clear_context()
    yield
    clear_context()


def test_get_logger_emits_single_json_line(capsys: pytest.CaptureFixture[str]) -> None:
    """One log call produces one JSON object on stdout with the run_id."""
    from enten.logging import get_logger, new_run_id

    rid = new_run_id()
    assert _RUN_ID_RE.match(rid), rid

    get_logger("t").info("hi", run_id=rid)

    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1, f"expected exactly one log line, got: {out!r}"
    line = json.loads(out[0])
    assert line["event"] == "hi"
    assert line["run_id"] == rid
    # The basic shape must always be present so downstream regex matchers
    # can rely on the field set.
    assert line["level"] == "info"
    assert "timestamp" in line


def test_bind_context_persists_across_calls(capsys: pytest.CaptureFixture[str]) -> None:
    """bind_context() makes the bound field appear on every later log line."""
    from enten.logging import bind_context, get_logger

    bind_context(pr_number=42)
    log = get_logger("ctx")

    log.info("first")
    log.info("second", extra="ignored-but-not-evil")

    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert len(lines) == 2
    for line in lines:
        assert line["pr_number"] == 42, line


def test_redaction_processor_runs_at_log_time(capsys: pytest.CaptureFixture[str]) -> None:
    """Secret-shaped values are rewritten to '[REDACTED]' in the JSON line."""
    from enten.logging import get_logger

    log = get_logger("redact")
    log.info("login", api_key="sk-fake-123", other="fine")

    line = json.loads(capsys.readouterr().out.strip().splitlines()[0])
    assert line["api_key"] == "[REDACTED]"
    assert line["other"] == "fine"


def test_new_run_id_is_unique() -> None:
    """1000 consecutive calls produce 1000 distinct ids (entropy check)."""
    from enten.logging import new_run_id

    ids = {new_run_id() for _ in range(1000)}
    assert len(ids) == 1000, "expected 1000 distinct run ids"
    # Spot-check that every id matches the canonical pattern.
    assert all(_RUN_ID_RE.match(rid) for rid in ids)
