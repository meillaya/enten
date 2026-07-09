"""Thread-safe token-bucket rate limiter for external APIs.

The bucket uses ``time.monotonic()`` for refill calculations so wall-clock
jumps do not create bursts or stalls. It is intentionally simple: one bucket
per external API, shared across the process.
"""

from __future__ import annotations

import threading
import time


class TokenBucket:
    """Token bucket with configurable capacity and refill rate.

    The implementation is thread-safe and uses ``time.monotonic()`` for all
    timing. ``acquire`` can block until tokens are available, while
    ``nowait_acquire`` returns immediately.
    """

    def __init__(self, capacity: int, refill_rate_per_sec: float) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_rate_per_sec < 0:
            raise ValueError("refill_rate_per_sec must be non-negative")

        self._capacity = float(capacity)
        self._rate = float(refill_rate_per_sec)
        self._tokens = self._capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        if self._rate > 0:
            elapsed = now - self._last_refill
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    def acquire(
        self,
        tokens: int = 1,
        *,
        blocking: bool = True,
        timeout: float | None = None,
    ) -> bool:
        """Acquire ``tokens`` from the bucket.

        When ``blocking`` is ``True``, waits up to ``timeout`` seconds for
        enough tokens to become available. Returns ``True`` on success and
        ``False`` if the wait times out (or if ``blocking`` is ``False`` and
        no tokens are available).
        """
        if tokens <= 0:
            raise ValueError("tokens must be positive")

        if not blocking:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
                return False

        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True

                if deadline is not None and time.monotonic() >= deadline:
                    return False

                if self._rate <= 0 and deadline is None:
                    # No refill and no timeout: we would spin forever.
                    return False

                needed = tokens - self._tokens
                if self._rate > 0:
                    wait_seconds = needed / self._rate
                else:
                    remaining = deadline - time.monotonic() if deadline else 0.05
                    wait_seconds = max(0.0, min(0.05, remaining))

            time.sleep(wait_seconds)

    def nowait_acquire(self, tokens: int = 1) -> bool:
        """Try to acquire tokens without blocking."""
        return self.acquire(tokens, blocking=False)


class BucketRegistry:
    """Holds named ``TokenBucket`` instances so callers share limits."""

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def get(self, name: str, capacity: int, refill_rate: float) -> TokenBucket:
        """Return the bucket named ``name``, creating it if necessary."""
        with self._lock:
            bucket = self._buckets.get(name)
            if bucket is None:
                bucket = TokenBucket(capacity, refill_rate)
                self._buckets[name] = bucket
            return bucket


#: Process-wide registry with conservative defaults for each external API.
#: Consumers should call ``get`` with the same parameters to share a bucket.
_default_registry = BucketRegistry()
_default_registry.get("github", capacity=100, refill_rate=1.0)
_default_registry.get("openai", capacity=120, refill_rate=2.0)
_default_registry.get("anthropic", capacity=60, refill_rate=1.0)
_default_registry.get("e2b", capacity=20, refill_rate=0.5)
_default_registry.get("ollama", capacity=10, refill_rate=5.0)

__all__ = ["BucketRegistry", "TokenBucket", "_default_registry"]
