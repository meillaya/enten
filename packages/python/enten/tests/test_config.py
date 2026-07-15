"""Tests for ``enten.config`` and ``enten.redaction``.

These tests deliberately avoid touching ``process.env`` so they can
run in parallel with anything else without leaking state. The pydantic
``_env_file=`` hook is the right seam to inject a temp env file per
test; the redaction helpers are pure functions and need no env at all.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from enten.config import LLMProvider, SandboxProvider, Settings
from enten.redaction import REDACT_KEYS, REDACTED, redact_dict, redact_value


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for field_name in Settings.model_fields:
        monkeypatch.delenv(field_name.upper(), raising=False)


# ── Settings: defaults ──────────────────────────────────────────────────
def test_settings_returns_documented_defaults() -> None:
    """``Settings()`` with no env file applies the documented defaults.

    The acceptance criterion in the plan spells out
    ``max_pr_diff_tokens == 50000``; we also pin a few siblings so a
    future accidental default change trips a loud test failure rather
    than silently rewriting the agent's behaviour.
    """
    s = Settings(_env_file=None)
    assert s.max_pr_diff_tokens == 50000
    assert s.per_pr_cost_cap_usd == 0.50
    assert s.prometheus_metrics_port == 9090
    assert s.llm_default_model == "gpt-4o-mini"
    assert s.llm_max_iterations == 15
    assert s.github_app_slug == "enten-bot"
    assert s.llm_provider == LLMProvider.OPENAI_COMPAT
    assert s.sandbox_provider == SandboxProvider.DOCKER
    assert s.sandbox_docker_image is None
    assert s.verification_command == "python -m compileall -q ."
    # Secrets default to None rather than raising, so the agent boots
    # in environments that don't have them yet.
    assert s.github_webhook_secret is None
    assert s.llm_openai_api_key is None
    assert s.e2b_api_key is None


# ── Settings: env file override ─────────────────────────────────────────
def test_settings_loads_custom_env_file(tmp_path: Path) -> None:
    """``Settings(_env_file=...)`` reads from the supplied path.

    Mirrors QA scenario 1 in the plan verbatim: copy ``.env.example``
    into a temp file, override one value, assert the model picks it
    up. We use ``max_pr_diff_tokens`` because it is an int — a string
    from the env file must be coerced to an int at the boundary, not
    left as a str.
    """
    env_file = tmp_path / "test.env"
    env_file.write_text(
        textwrap.dedent(
            """
            MAX_PR_DIFF_TOKENS=99999
            LLM_PROVIDER=ollama
            GITHUB_WEBHOOK_SECRET=topsecret
            """
        ).strip()
    )

    s = Settings(_env_file=str(env_file))

    assert s.max_pr_diff_tokens == 99999
    assert s.llm_provider == LLMProvider.OLLAMA
    assert s.github_webhook_secret == "topsecret"
    # An unrelated field still falls back to the model default.
    assert s.per_pr_cost_cap_usd == 0.50


# ── Settings: missing required does not raise ───────────────────────────
def test_settings_tolerates_missing_required_field() -> None:
    """A 'required' field like ``GITHUB_APP_ID`` left unset yields ``None``,
    not ``ValidationError``.

    The plan is explicit: *all* fields have defaults so the
    constructor is side-effect-free. Domain code that *truly* needs
    the App ID (T33's bootstrap) is responsible for asserting
    ``is not None`` and emitting a domain-specific error.
    """
    s = Settings(_env_file=None)
    assert s.github_app_id is None
    assert s.github_app_private_key_path is None
    assert s.committer_email is None


# ── Settings: bad type raises ───────────────────────────────────────────
def test_settings_raises_validation_error_on_bad_type(tmp_path: Path) -> None:
    """A non-integer ``MAX_PR_DIFF_TOKENS`` triggers ``ValidationError``,
    never silent coercion.

    The plan's "Do not coerce types silently" rule is the loudest
    part of T03. We pass ``"not-an-int"`` and expect pydantic to
    raise — if this test ever flips to a successful parse, a
    regression has introduced string-to-int coercion and the agent
    will start miscounting PR diffs at runtime.
    """
    env_file = tmp_path / "bad.env"
    env_file.write_text("MAX_PR_DIFF_TOKENS=not-an-int\n")

    with pytest.raises(ValidationError):
        Settings(_env_file=str(env_file))


# ── redaction: redact_value on a secret-shaped key ──────────────────────
def test_redact_value_redacts_secret_key() -> None:
    """``redact_value("api_key", "sk-fake")`` returns ``[REDACTED]``."""
    assert redact_value("api_key", "sk-fake") == REDACTED
    # Spelling variants covered by the same regex.
    assert redact_value("apikey", "sk-fake") == REDACTED
    assert redact_value("API_KEY", "sk-fake") == REDACTED


# ── redaction: redact_value on a safe key ───────────────────────────────
def test_redact_value_passes_through_safe_key() -> None:
    """``redact_value("safe_field", "value")`` returns the value unchanged."""
    assert redact_value("safe_field", "value") == "value"
    # None is also passed through (replacing a missing secret with a
    # sentinel would be a category error).
    assert redact_value("safe_field", None) is None


# ── redaction: redact_dict recursion ────────────────────────────────────
def test_redact_dict_recurses_through_nested_structures() -> None:
    """``redact_dict`` walks nested dicts (and list elements) so a deep
    config payload gets the same redaction as a flat one.

    We also assert that the input is *not* mutated — callers may pass
    in ``Settings().model_dump()`` and expect the original to remain
    intact for downstream non-logging uses.
    """
    payload = {
        "api_key": "sk-fake",
        "token": "ghp_fake",
        "outer": {
            "webhook_secret": "whsec_fake",
            "inner": {
                "password": "hunter2",
                "innocent": 42,
            },
        },
        "messages": [
            {"role": "user", "api_key": "sk-fake-2"},
            {"role": "assistant", "content": "hi"},
        ],
    }
    redacted = redact_dict(payload)

    assert redacted["api_key"] == REDACTED
    assert redacted["token"] == REDACTED
    assert redacted["outer"]["webhook_secret"] == REDACTED
    assert redacted["outer"]["inner"]["password"] == REDACTED
    assert redacted["outer"]["inner"]["innocent"] == 42
    assert redacted["messages"][0]["api_key"] == REDACTED
    assert redacted["messages"][1]["content"] == "hi"
    # Original is untouched.
    assert payload["api_key"] == "sk-fake"
    assert payload["outer"]["inner"]["innocent"] == 42


# ── redaction: pattern sanity check ─────────────────────────────────────
def test_redact_keys_pattern_matches_documented_names() -> None:
    """Spot-check the regex against the names the plan enumerates.

    This is more of a documentation test than a coverage test: if a
    future edit to ``REDACT_KEYS`` accidentally drops ``password`` or
    ``webhook_secret``, the test fails before any secret leaks into a
    production log line.
    """
    for key in (
        "api_key",
        "apikey",
        "API_KEY",
        "token",
        "secret",
        "private_key",
        "privatekey",
        "password",
        "webhook_secret",
        "webhooksecret",
        "web_hook_secret",
    ):
        assert REDACT_KEYS.search(key), f"REDACT_KEYS missed: {key!r}"
