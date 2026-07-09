"""enten - AI engineering agent (Python core).

This is the canonical Python entry point for the enten monorepo. The package
is intentionally minimal in the scaffold (T01): a version string, a Typer
CLI stub, and a placeholder for the runtime that subsequent tasks will build
out. See ``docs/architecture.md`` for the high-level design.
"""

from __future__ import annotations

__version__ = "0.1.0"

from enten.config import Settings
from enten.redaction import REDACT_KEYS, REDACTED, redact_dict, redact_value

__all__ = [
    "REDACTED",
    "REDACT_KEYS",
    "Settings",
    "__version__",
    "redact_dict",
    "redact_value",
]
