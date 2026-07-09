"""Typed application configuration for the enten Python core.

This module is the single source of truth for every environment variable
read by the Python side of the agent. The :class:`Settings` class is a
``pydantic_settings.BaseSettings`` model that:

* loads ``.env`` from the current working directory (override per-instance
  with ``Settings(_env_file=...)`` for tests and scripts),
* matches env var names case-insensitively against field names,
* ignores unknown env vars (``extra="ignore"``) so that sibling processes
  (devenv shell exports, OTEL SDK, etc.) can dump anything they want
  into the environment without breaking the agent,
* raises ``ValidationError`` on a bad type — there is no silent coercion,
  so a typo in ``MAX_PR_DIFF_TOKENS=abc`` is loud, not silent.

The constructor is intentionally logging-free. Secret values are *never*
printed, echoed, or written to ``Settings.__repr__``; redaction is the
responsibility of the structlog / pino processor chain defined in
:mod:`enten.redaction`, which is invoked at log-time, not at init-time.

Usage::

    from enten.config import Settings

    s = Settings()  # reads .env + process env
    assert s.max_pr_diff_tokens == 50000

    # For tests, point at a temp file:
    s = Settings(_env_file="/tmp/test.env")

Adding a new env var? Add a field here with a default, then add the same
name to ``.env.example`` at the repo root.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(str, Enum):
    """LLM backend selection.

    ``openai-compat`` covers OpenAI itself plus any vendor that speaks
    the OpenAI chat-completions wire format (Together, Anyscale,
    OpenRouter, vLLM, etc.). ``ollama`` is the local-models case.
    """

    OPENAI_COMPAT = "openai-compat"
    OLLAMA = "ollama"


class SandboxProvider(str, Enum):
    """Where agent-generated code actually runs.

    ``docker`` uses a sibling container (default in dev). ``e2b``
    uses the E2B cloud sandbox; it requires ``E2B_API_KEY``.
    """

    DOCKER = "docker"
    E2B = "e2b"


class Settings(BaseSettings):
    """Strongly-typed view of the enten Python environment.

    Field names are the canonical lowercase form. Pydantic-settings
    matches them against env vars case-insensitively, so
    ``github_app_id`` is read from ``GITHUB_APP_ID`` (or any case
    variant thereof).

    All fields have defaults. Secrets default to ``None`` so the
    constructor never raises when an env var is absent; callers that
    *require* a secret (e.g. the GitHub App bootstrap in T33) must
    check for ``None`` explicitly and fail with a domain-specific
    error message rather than a generic ``ValidationError``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── GitHub App identity ──────────────────────────────────────────────
    # Numeric App ID. Pydantic coerces a digit-string env var to int.
    github_app_id: int | None = None
    # Public slug shown in installation URLs (e.g. ``enten-bot``).
    github_app_slug: str = "enten-bot"
    # Filesystem path to the PEM private key downloaded from the App's
    # "Private keys" page. We never inline the key in env vars.
    github_app_private_key_path: Path | None = None
    # Shared secret used to validate ``X-Hub-Signature-256`` on inbound
    # webhooks (the TS edge in T09 does the same check).
    github_webhook_secret: str | None = None
    # The ``committer_email`` we use when pushing fix branches so the
    # agent can self-ignore its own commits in cron/diff jobs.
    committer_email: str | None = None

    # ── LLM provider ─────────────────────────────────────────────────────
    llm_provider: LLMProvider = LLMProvider.OPENAI_COMPAT
    # Default chat model name sent on every request; per-task overrides
    # live in the planner (T13), not here.
    llm_default_model: str = "gpt-4o-mini"
    # Hard cap on ReAct-style iterations per task. T27's circuit
    # breaker trips on this.
    llm_max_iterations: int = 15
    # OpenAI-compatible base URL; covers OpenAI, Together, vLLM, etc.
    llm_openai_base_url: str = "https://api.openai.com/v1"
    # API key for the OpenAI-compatible provider. Optional locally
    # (Ollama doesn't need it) but mandatory at runtime when
    # ``llm_provider == openai-compat``.
    llm_openai_api_key: str | None = None
    # Ollama's local daemon; only consulted when ``llm_provider == ollama``.
    llm_ollama_base_url: str = "http://localhost:11434"

    # ── Sandbox provider ─────────────────────────────────────────────────
    sandbox_provider: SandboxProvider = SandboxProvider.DOCKER
    # Required when ``sandbox_provider == e2b``.
    e2b_api_key: str | None = None

    # ── Database ─────────────────────────────────────────────────────────
    # Async SQLAlchemy URL; T08 wires the Alembic migrations against
    # this same DSN.
    database_url: str = "postgresql+asyncpg://enten@localhost/enten"

    # ── Cost & diff guardrails ───────────────────────────────────────────
    # Token budget for a single PR's diff; over-budget diffs are
    # rejected before they hit the LLM (T15 budget guard).
    max_pr_diff_tokens: int = 50000
    # USD per-PR cap; enforced by the T13 budget tracker.
    per_pr_cost_cap_usd: float = 0.50

    # ── Observability ────────────────────────────────────────────────────
    # OTLP gRPC endpoint for the OTel SDK (T05 wires the exporter).
    otel_endpoint: str = "http://localhost:4317"
    # Port the Prometheus ``/metrics`` exposition listens on. The TS
    # edge uses ``METRICS_PORT`` (default 9091) to avoid clashing with
    # the Python agent on a single host.
    prometheus_metrics_port: int = 9090


__all__ = [
    "LLMProvider",
    "SandboxProvider",
    "Settings",
]
