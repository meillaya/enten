import time

import pytest

from enten.rate_limit import BucketRegistry, TokenBucket


class TestTokenBucket:
    def test_refill_restores_capacity(self) -> None:
        bucket = TokenBucket(capacity=10, refill_rate_per_sec=2.0)

        # Exhaust the bucket.
        assert bucket.acquire(10) is True
        assert bucket.nowait_acquire() is False

        # Wait long enough to refill fully at 2 tokens/sec.
        time.sleep(5.25)

        assert bucket.acquire(10) is True
        assert bucket.nowait_acquire() is False

    def test_blocks_when_empty(self) -> None:
        bucket = TokenBucket(capacity=2, refill_rate_per_sec=0.0)

        assert bucket.nowait_acquire() is True
        assert bucket.nowait_acquire() is True
        # Third acquire should time out because no tokens are refilled.
        assert bucket.acquire(timeout=0.15) is False

    def test_nowait_acquire(self) -> None:
        bucket = TokenBucket(capacity=1, refill_rate_per_sec=10.0)

        assert bucket.nowait_acquire() is True
        assert bucket.nowait_acquire() is False

        time.sleep(0.15)
        assert bucket.nowait_acquire() is True

    def test_invalid_tokens_raises(self) -> None:
        bucket = TokenBucket(capacity=5, refill_rate_per_sec=1.0)
        with pytest.raises(ValueError):
            bucket.acquire(0)
        with pytest.raises(ValueError):
            bucket.acquire(-1)


class TestBucketRegistry:
    def test_get_creates_and_reuses_bucket(self) -> None:
        registry = BucketRegistry()
        a = registry.get("api", capacity=5, refill_rate=1.0)
        b = registry.get("api", capacity=5, refill_rate=1.0)

        assert a is b
        assert a.nowait_acquire(5) is True
