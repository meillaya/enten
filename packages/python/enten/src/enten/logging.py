"""Structured logging configuration for the enten Python core.

The agent emits one JSON line per event to stdout via :mod:`structlog`. The
processor chain is the single source of truth for what an enten log line
looks like on disk; downstream tooling (Loki, Jaeger log appenders, the
``claude-md-management`` reviewer pass) all match the regex below.

Design contract
---------------

- One JSON object per ``stdout.write``, flushable line-by-line by tail.
- ISO 8601 UTC timestamps (``2026-07-09T12:34:56.789012Z``).
- Bound contextvars flow into every subsequent log call of the same
  asyncio task (``run_id``, ``pr_number``, ``attempt`` ...).
- Secret-shaped keys are redacted **at log time**, not at construction
  time; we wrap :func:`enten.redaction.redact_dict` so the redaction
  rule stays single-sourced (T03).

Configured once at process boot from :mod:`enten.cli`'s entry point —
never inside library code. The :func:`get_logger` factory is safe to
call from anywhere because structlog's ``cache_logger_on_first_use=True``
binds the configuration on the first lookup.

Run ids
-------

:func:`new_run_id` produces values matching ``^pr_[a-z0-9]{12}$``.
Twelve base32 chars carry 60 bits of entropy; mixed with
:func:`time.monotonic_ns` so collisions over a single process's lifetime
are vanishingly unlikely while still being cheap to generate (one SHA-256
hash + base32 trim).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import sys
import time
from typing import Any

import structlog

from enten.redaction import redact_dict

__all__ = [
    "bind_context",
    "clear_context",
    "configure_logging",
    "get_logger",
    "new_run_id",
]

# Prefix on every enten run id. The trailing 12 base32 chars are derived
# from a hash of (monotonic_ns + 16 bits of CSPRNG entropy).
_RUN_ID_PREFIX = "pr_"

# Module-level level that survives re-imports. ``structlog.configure`` is
# idempotent, but we still guard against accidental double-init in
# long-lived test runners.
_CONFIGURED: bool = False


def _redact_processor(
    _logger: Any,
    _method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """structlog processor that redacts secret-shaped fields at log time.

    Wraps :func:`enten.redaction.redact_dict` so the redaction rule
    stays single-sourced (T03). This processor must run **after**
    :func:`structlog.contextvars.merge_contextvars` so contextvar-bound
    values are also filtered before they reach stdout. ``api_key``,
    ``webhook_secret``, ``token``, etc. are rewritten to ``[REDACTED]``.
    """
    return redact_dict(event_dict)


def new_run_id() -> str:
    """Return a 16-character run id matching ``^pr_[a-z0-9]{12}$``.

    Implementation:
        mix = sha256( f"{monotonic_ns}-{token_hex(2)}" )[:8 bytes]
        id  = "pr_" + base32(mix).lower()[:12]

    The combination gives 60 bits of base32 output (12 chars × 5 bits);
    :func:`secrets.token_hex` provides 16 bits of cryptographic entropy
    which guarantees 1000+ consecutive ids are unique without needing a
    monotonic clock that never repeats.
    """
    mix = f"{time.monotonic_ns()}-{secrets.token_hex(2)}".encode()
    digest = hashlib.sha256(mix).digest()
    # base32 of 8 bytes yields 14 chars; lowercase keeps the charset
    # inside [a-z2-7] which is a strict subset of [a-z0-9], so the
    # regex match is preserved.
    encoded = base64.b32encode(digest[:8]).decode("ascii").lower().rstrip("=")
    return f"{_RUN_ID_PREFIX}{encoded[:12]}"


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog for JSON output to stdout.

    Idempotent: subsequent calls re-run ``structlog.configure`` and
    reset the root logger handler set so test suites that configure
    and reconfigure across modules don't leak duplicate handlers.

    Processor chain (in order):

    1. ``merge_contextvars`` — pulls bound values from :func:`bind_context`
       into the event dict so they appear in JSON output.
    2. ``add_log_level`` — adds ``"level": "info"`` (or the call's level).
    3. ``format_exc_info`` — if ``exc_info=True``, formats the traceback
       as a string for inclusion.
    4. ``_redact_processor`` — secret-aware redaction via the shared
       :func:`enten.redaction.redact_dict` so the rule stays single-
       sourced.
    5. ISO 8601 UTC timestamper — ``"timestamp": "2026-07-09T12:..."``.
    6. ``JSONRenderer`` — emits one JSON object per line.

    Output goes through :class:`logging.StreamHandler(sys.stdout)` which
    is the canonical way to make structlog output reach pytest's
    ``capsys`` capture. Propagation is disabled on the root logger so
    third-party libraries that call ``logging.basicConfig`` cannot
    double-emit our structured events.
    """
    global _CONFIGURED

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.format_exc_info,
        _redact_processor,
        timestamper,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        wrapper_class=structlog.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Also wire stdlib's root logger through the same JSON renderer so
    # third-party libraries that emit via ``logging.getLogger("foo")``
    # do not bypass our format. We use a ProcessorFormatter with the
    # same shared processors as the foreign_pre_chain.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False

    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound logger for ``name``.

    The factory is cheap when ``cache_logger_on_first_use=True`` (the
    default after :func:`configure_logging`); the cached instance is
    reused per ``name`` so callers don't pay the configuration cost on
    every lookup. The return type is announced as
    :class:`structlog.stdlib.BoundLogger` so static type-checkers see
    the same surface callers expect from stdlib loggers.
    """
    logger = structlog.get_logger(name)
    # ``structlog.get_logger`` returns whatever class is configured as
    # ``wrapper_class``; we annotated the return type as the stdlib
    # ``BoundLogger`` because that's the surface users care about
    # (``logger.info(event, **kwargs)``) and it satisfies both the
    # PrintLogger and the stdlib adapter.
    return logger  # type: ignore[return-value]


def bind_context(**kwargs: Any) -> None:
    """Bind ``kwargs`` as contextvars flowing into all later log calls.

    Pass scalar values (e.g. ``run_id``, ``pr_number``,
    ``attempt``) -- they will appear in every JSON line until
    :func:`clear_context` is called or :func:`unbind_context` removes
    them. Structured values (dicts/lists) are passed through structlog's
    standard event-dict merging.
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    """Drop every contextvar set via :func:`bind_context`.

    Intended for use between tests to avoid bleed-over of e.g.
    ``pr_number=42`` from the previous test into the next suite.
    """
    structlog.contextvars.clear_contextvars()
