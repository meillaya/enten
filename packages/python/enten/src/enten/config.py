"""Typed application configuration for the enten Python core.

This module is the typed source of truth for application settings read by the
Python worker and library components. Migration-only safety switches remain
owned by the migration CLI. The :class:`Settings` class is a
``pydantic_settings.BaseSettings`` model that:

* loads ``.env`` from the current working directory (override per-instance
  with ``Settings(_env_file=...)`` for tests and scripts),
* matches env var names case-insensitively against field names,
* ignores unknown env vars (``extra="ignore"``) so that sibling processes
  (devenv shell exports, OTEL SDK, etc.) can dump anything they want
  into the environment without breaking the agent,
* raises ``ValidationError`` on a bad type rather than accepting malformed
  values silently.

The constructor is intentionally logging-free. Secret fields are excluded from
``Settings.__repr__``. Callers that serialize settings must still use the
structlog / pino redaction processors defined in :mod:`enten.redaction`.

Usage::

    from enten.config import Settings

    s = Settings()  # reads .env + process env
    assert s.llm_max_iterations == 15

    # For tests, point at a temp file:
    s = Settings(_env_file="/tmp/test.env")

Adding a new application setting? Add a field here with a default, then add the
same name to ``.env.example`` at the repo root.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(str, Enum):
    """LLM backend selection.

    ``openai-compat`` covers OpenAI itself plus any vendor that speaks
    the OpenAI chat-completions wire format (Together, OpenRouter, vLLM,
    LM Studio, etc.). ``anthropic`` uses the native Messages API.
    ``ollama`` is the local-models case.
    """

    OPENAI_COMPAT = "openai-compat"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"


class ReviewGenerator(str, Enum):
    """Findings generator selected by the production worker."""

    LOCAL = "local"
    LLM = "llm"


class ProbeMode(str, Enum):
    """Whether Python probes are disabled, measured, or published."""

    OFF = "off"
    SHADOW = "shadow"
    ON = "on"


class SandboxProvider(str, Enum):
    """Where agent-generated code actually runs.

    ``docker`` uses a sibling container (default in dev). ``e2b`` is reserved
    but fails closed until an official SDK integration is approved.
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
    *require* a secret (such as the production worker) must
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
    # Shared secret used to validate ``X-Hub-Signature-256`` on inbound webhooks.
    github_webhook_secret: str | None = Field(default=None, repr=False)
    # Optional CA bundle for a GitHub API proxy or enterprise endpoint.
    github_ca_bundle: str | None = None
    # ── LLM provider ─────────────────────────────────────────────────────
    llm_provider: LLMProvider = LLMProvider.OPENAI_COMPAT
    # Findings generator used by the production worker.
    review_generator: ReviewGenerator = ReviewGenerator.LOCAL
    # Python probe mode: off, shadow (measure only), or on (publish confirmed).
    review_probes: ProbeMode = ProbeMode.OFF
    probe_command: str = "python -m pytest -q"
    probe_max: int = Field(default=3, ge=0, le=3)
    probe_timeout_seconds: int = Field(default=90, ge=1, le=600)
    # Default chat model name sent on every request.
    llm_default_model: str = "gpt-4o-mini"
    llm_max_iterations: int = 15
    # OpenAI-compatible base URL; covers OpenAI, OpenRouter, vLLM, LM Studio.
    llm_openai_base_url: str = "https://api.openai.com/v1"
    # Optional API key sent as a bearer token when the selected endpoint needs one.
    llm_openai_api_key: str | None = Field(default=None, repr=False)
    # Native Anthropic Messages API.
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_api_key: str | None = Field(default=None, repr=False)
    anthropic_version: str = "2023-06-01"
    # Ollama's local daemon; only consulted when ``llm_provider == ollama``.
    llm_ollama_base_url: str = "http://localhost:11434"

    # ── Embeddings (Py core) ───────────────────────────────────────────────
    embedding_provider: str = "openai-compat"
    embedding_base_url: str | None = None
    embedding_api_key: str | None = Field(default=None, repr=False)
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1024
    embedding_timeout: float = 30.0
    embedding_batch_size: int = 64

    # ── Sandbox provider ─────────────────────────────────────────────────
    sandbox_provider: SandboxProvider = SandboxProvider.DOCKER
    sandbox_docker_image: str | None = None
    verification_command: str = "python -m compileall -q ."

    # ── Database ─────────────────────────────────────────────────────────
    # Async SQLAlchemy URL; the checksum migration runner normalizes it at
    # the asyncpg boundary.
    database_url: str = Field(
        default="postgresql+asyncpg://enten@localhost/enten",
        repr=False,
    )

    # ── Cost guardrail ───────────────────────────────────────────
    # USD per-PR cap enforced by the LLM client.
    per_pr_cost_cap_usd: float = 0.50

    # ── Observability ────────────────────────────────────────────────────
    # OTLP gRPC endpoint for the OTel SDK.
    otel_endpoint: str = "http://localhost:4317"


__all__ = [
    "LLMProvider",
    "ProbeMode",
    "ReviewGenerator",
    "SandboxProvider",
    "Settings",
]
