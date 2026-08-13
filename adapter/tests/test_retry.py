import asyncio
import logging

import pytest
from tenacity import wait_none

from daher_adapter.retry import (
    RetriesExhausted,
    RetryPolicy,
    TransientIntegrationError,
    run_with_retry,
)


def test_transient_failure_retries_until_success() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TransientIntegrationError("temporary")
        return "ok"

    result = asyncio.run(
        run_with_retry("send_order", operation, RetryPolicy(3, 1, 30), wait=wait_none())
    )

    assert result == "ok"
    assert attempts == 3


def test_non_transient_failure_is_not_retried() -> None:
    attempts = 0

    async def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise ValueError("invalid FHIR resource")

    with pytest.raises(ValueError, match="invalid FHIR"):
        asyncio.run(
            run_with_retry("send_order", operation, RetryPolicy(5, 1, 30), wait=wait_none())
        )
    assert attempts == 1


def test_exhausted_retry_does_not_log_exception_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def operation() -> None:
        raise TransientIntegrationError("patient-value-must-not-enter-log")

    with caplog.at_level(logging.WARNING), pytest.raises(RetriesExhausted) as error:
        asyncio.run(
            run_with_retry("send_order", operation, RetryPolicy(2, 1, 30), wait=wait_none())
        )

    assert error.value.attempts == 2
    assert "patient-value-must-not-enter-log" not in caplog.text
    assert any(
        getattr(record, "error_type", None) == "TransientIntegrationError"
        for record in caplog.records
    )
