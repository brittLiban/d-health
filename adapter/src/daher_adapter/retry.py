from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)
from tenacity.wait import wait_base

logger = logging.getLogger("daher_adapter.retry")


class TransientIntegrationError(RuntimeError):
    """An unreachable or temporarily unavailable integration endpoint."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    multiplier_seconds: float
    max_seconds: float

    def __post_init__(self) -> None:
        if self.max_attempts <= 0 or self.multiplier_seconds <= 0 or self.max_seconds <= 0:
            raise ValueError("retry policy values must be greater than zero")


class RetriesExhausted(RuntimeError):
    def __init__(self, operation: str, attempts: int, last_error: Exception | None) -> None:
        self.operation = operation
        self.attempts = attempts
        self.last_error = last_error
        self.error_type = type(last_error).__name__ if last_error is not None else "unknown"
        super().__init__(
            f"operation {operation!r} exhausted {attempts} attempts ({self.error_type})"
        )


async def run_with_retry[T](
    operation_name: str,
    operation: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    wait: wait_base | None = None,
) -> T:
    retrying = AsyncRetrying(
        stop=stop_after_attempt(policy.max_attempts),
        wait=wait
        or wait_random_exponential(multiplier=policy.multiplier_seconds, max=policy.max_seconds),
        retry=retry_if_exception_type(TransientIntegrationError),
        before_sleep=lambda state: _log_retry(operation_name, state),
        reraise=False,
    )
    try:
        async for attempt in retrying:
            with attempt:
                return await operation()
    except RetryError as error:
        last_error = error.last_attempt.exception()
        raise RetriesExhausted(operation_name, policy.max_attempts, last_error) from None
    raise RuntimeError("retry loop ended without a result")


def _log_retry(operation_name: str, state: RetryCallState) -> None:
    error = state.outcome.exception() if state.outcome is not None else None
    logger.warning(
        "integration_retry",
        extra={
            "operation": operation_name,
            "attempt": state.attempt_number,
            "error_type": type(error).__name__ if error is not None else "unknown",
        },
    )
