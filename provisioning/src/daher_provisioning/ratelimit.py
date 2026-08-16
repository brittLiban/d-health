"""Rate limiting for the provisioning API (ADR-021 non-negotiable 7).

The API is reachable from a less-trusted server across a network rather than only from a
local shell, so abuse protection is part of the contract rather than an optimisation.

A token bucket per principal: steady refill with a burst allowance, which fits provisioning
better than a fixed window. Creating five facilities in quick succession is legitimate;
creating five hundred is not, and a fixed window would either allow a damaging burst at the
boundary or refuse ordinary bursty use.

⚠️ **This is in-memory and per-process.** Two consequences that must not be glossed:

- It resets when the API restarts, so an attacker who can crash-loop the service can also
  reset their budget.
- It does not coordinate across multiple API processes; running N workers multiplies the
  effective limit by N.

Both are acceptable for a single-process deployment and both stop being acceptable the
moment it scales out. A shared backend (Redis or equivalent) is the fix, and the interface
here is deliberately narrow so that swap does not ripple.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


class RateLimitExceeded(RuntimeError):
    """The caller has exhausted its budget."""

    def __init__(self, key: str, retry_after_seconds: float) -> None:
        super().__init__(f"rate limit exceeded for {key!r}; retry after {retry_after_seconds:.1f}s")
        self.key = key
        self.retry_after_seconds = retry_after_seconds


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


@dataclass
class TokenBucketLimiter:
    """Refills ``rate_per_second`` tokens per second, capped at ``burst``."""

    rate_per_second: float = 1.0
    burst: int = 10
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if self.burst < 1:
            raise ValueError("burst must be at least 1")

    def _now(self) -> float:
        # Monotonic, not wall clock: a clock adjustment must not hand out free budget.
        return time.monotonic()

    def check(self, key: str, *, cost: float = 1.0) -> None:
        """Consume budget for one call, or raise ``RateLimitExceeded``.

        Raising rather than returning a boolean is deliberate — a limiter whose result can
        be ignored at the call site is not a limiter.
        """
        if cost <= 0:
            raise ValueError("cost must be positive")

        now = self._now()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=float(self.burst), last_refill=now)
            self._buckets[key] = bucket

        elapsed = max(0.0, now - bucket.last_refill)
        bucket.tokens = min(float(self.burst), bucket.tokens + elapsed * self.rate_per_second)
        bucket.last_refill = now

        if bucket.tokens < cost:
            deficit = cost - bucket.tokens
            raise RateLimitExceeded(key, deficit / self.rate_per_second)

        bucket.tokens -= cost

    def remaining(self, key: str) -> float:
        bucket = self._buckets.get(key)
        if bucket is None:
            return float(self.burst)
        elapsed = max(0.0, self._now() - bucket.last_refill)
        return min(float(self.burst), bucket.tokens + elapsed * self.rate_per_second)

    def reset(self, key: str) -> None:
        self._buckets.pop(key, None)
