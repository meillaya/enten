"""Secret-aware redaction helpers for the enten Python core.

This module is intentionally tiny and free of third-party imports so the
structlog processor chain and explicit diagnostic serializers can share one
recursive Python redaction rule. The TypeScript edge separately maintains
explicit pino paths in ``packages/ts/app/src/logging.ts``.

Pattern source: the plan's T03 deliverable,
``(?i)(api_?key|token|secret|private_?key|password|webhook_?_?secret)``.
The ``_?`` groups tolerate the common spelling variants
(``api_key`` vs ``apikey``; ``webhook_secret`` vs ``webhooksecret``).

Usage::

    from enten.redaction import redact_dict, redact_value

    redact_value("api_key", "sk-fake")   # → "[REDACTED]"
    redact_value("safe_field", "value")  # → "value"
    redact_dict({"api_key": "sk", "x": 1})
    # → {"api_key": "[REDACTED]", "x": 1}
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# Match common secret-shaped key names. Case-insensitive; tolerates
# optional underscores inside compound words so both ``api_key`` and
# ``apikey`` are caught, and so are ``webhook_secret`` /
# ``webhooksecret`` / ``web_hook_secret``.
REDACT_KEYS: re.Pattern[str] = re.compile(
    r"(?i)(api_?key|token|secret|private_?key|password|webhook_?_?secret)"
)

# The literal string substituted in place of any redacted value. Kept
# in one place so log consumers can grep for it during incident review.
REDACTED: str = "[REDACTED]"


def _matches(key: str) -> bool:
    """Return ``True`` if ``key`` looks like a secret-bearing field name."""
    return REDACT_KEYS.search(key) is not None


def redact_value(key: str, value: Any) -> Any:
    """Return ``[REDACTED]`` when ``key`` matches :data:`REDACT_KEYS`.

    Non-matching keys are returned unchanged. ``None`` values are
    passed through untouched so that ``settings.api_key is None`` is
    preserved through a logging pass (a redaction layer must never
    convert a missing secret into a present-looking sentinel).
    """
    if _matches(key):
        return REDACTED
    return value


def redact_dict(d: Mapping[str, Any]) -> dict[str, Any]:
    """Return a new dict with secret-shaped values replaced.

    Recurses into nested ``dict`` instances. Lists and tuples are
    walked element-by-element so a ``messages=[{"role": ..., ...}]``
    payload gets the same treatment as a top-level dict.

    The input is *not* mutated; the returned dict is a deep copy of the
    scalar structure with redacted leaves. This matters for
    ``Settings().model_dump()`` callers that want to pass the
    structure to a logger while keeping the original ``Settings``
    object intact.
    """
    out: dict[str, Any] = {}
    for key, value in d.items():
        if _matches(key):
            out[key] = REDACTED
            continue
        if isinstance(value, Mapping):
            out[key] = redact_dict(value)
        elif isinstance(value, (list, tuple)):
            out[key] = type(value)(  # preserve list vs tuple
                redact_dict(v) if isinstance(v, Mapping) else v for v in value
            )
        else:
            out[key] = value
    return out


__all__ = [
    "REDACTED",
    "REDACT_KEYS",
    "redact_dict",
    "redact_value",
]
