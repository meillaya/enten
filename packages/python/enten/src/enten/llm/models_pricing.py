"""Per-model token pricing used by the cost tracker.

Prices are in USD per 1_000_000 tokens. Local models are priced at zero
because the runtime cost is host-machine resources, not API spend.
"""

from __future__ import annotations

PRICING: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "llama3.1": {"input": 0.0, "output": 0.0},
}
