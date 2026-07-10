"""Prometheus metrics exposition for the enten Python core.

The module creates the counters and histograms used by the agent and starts
an optional HTTP server on ``/metrics`` so Prometheus (or any compatible
scraper) can pull them. Metrics are intentionally mirrored on the TS side
in ``packages/ts/app/src/metrics.ts`` so that both halves of the monorepo
export a consistent surface.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram, start_http_server

#: Total number of PR reviews processed by the agent, labelled by result.
PR_REVIEWS_TOTAL = Counter(
    "pr_reviews_total",
    "Total number of PR reviews processed",
    ["result"],
)

#: Duration of outbound LLM calls, labelled by model name.
LLM_CALL_DURATION_SECONDS = Histogram(
    "llm_call_duration_seconds",
    "Duration of outbound LLM calls in seconds",
    ["model"],
)

#: Duration of sandbox executions, labelled by sandbox provider.
SANDBOX_EXEC_DURATION_SECONDS = Histogram(
    "sandbox_exec_duration_seconds",
    "Duration of sandbox executions in seconds",
    ["sandbox"],
)

#: End-to-end duration of a PR review (queue to result).
PR_REVIEW_DURATION_SECONDS = Histogram(
    "pr_review_duration_seconds",
    "End-to-end duration of a PR review in seconds",
)


def start_metrics_server(port: int = 9090) -> None:
    """Start a Prometheus text-format exposition server on the given port.

    The server runs in a background thread and serves the default registry
    on ``http://<host>:<port>/metrics``. It is safe to call this function
    only once per process; subsequent calls will attempt to bind the same
    port and will raise unless a different port is supplied.
    """
    start_http_server(port)


def record_pr_review(result: str) -> None:
    """Increment the ``pr_reviews_total`` counter for ``result``.

    ``result`` should be one of ``passed``, ``failed``, or ``errored`` so
    that the TS and Python metrics surfaces stay aligned.
    """
    PR_REVIEWS_TOTAL.labels(result=result).inc()


def record_llm_call(model: str, duration: float) -> None:
    """Record the duration of an LLM call, labelled by ``model``."""
    LLM_CALL_DURATION_SECONDS.labels(model=model).observe(duration)


def record_sandbox_exec(sandbox: str, duration: float) -> None:
    """Record the duration of a sandbox execution, labelled by ``sandbox``."""
    SANDBOX_EXEC_DURATION_SECONDS.labels(sandbox=sandbox).observe(duration)
