"""
Tests for RetryPolicy — Architecture §13-14.

Validates:
  - Successful operation returns immediately
  - Retryable errors trigger retries
  - Non-retryable errors stop immediately
  - Exponential backoff with jitter
  - Idempotency check prevents duplicate side effects
  - Max attempts exhaustion
"""

import pytest
from services.retry_policy import RetryPolicy, RetryConfig, OperationType, retry
from core.errors import (
    KanyozaError,
    ErrorCode,
    DatabaseUnavailableError,
    TokenExpiredError,
)


class TestRetryPolicySuccess:
    """Test that successful operations don't retry."""

    def test_success_returns_immediately(self):
        policy = RetryPolicy(RetryConfig(max_attempts=3))
        call_count = 0

        def operation():
            nonlocal call_count
            call_count += 1
            return "success"

        result = policy.execute(operation, operation_name="test")

        assert result.success is True
        assert result.value == "success"
        assert result.attempts == 1
        assert call_count == 1  # Only called once

    def test_success_after_one_retry(self):
        policy = RetryPolicy(RetryConfig(
            max_attempts=3,
            base_delay_seconds=0.01,  # Tiny delay for fast tests
            jitter=False,
        ))
        call_count = 0

        def operation():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("temporary failure")
            return "recovered"

        result = policy.execute(operation, operation_name="test")

        assert result.success is True
        assert result.value == "recovered"
        assert result.attempts == 2
        assert call_count == 2


class TestRetryPolicyFailure:
    """Test failure behavior."""

    def test_non_retryable_error_stops_immediately(self):
        policy = RetryPolicy(RetryConfig(max_attempts=3))
        call_count = 0

        def operation():
            nonlocal call_count
            call_count += 1
            raise TokenExpiredError(message="Token expired")

        result = policy.execute(operation, operation_name="test")

        assert result.success is False
        assert isinstance(result.error, TokenExpiredError)
        assert result.attempts == 1  # Stopped after first try
        assert call_count == 1

    def test_exhausted_attempts_returns_failure(self):
        policy = RetryPolicy(RetryConfig(
            max_attempts=3,
            base_delay_seconds=0.01,
            jitter=False,
        ))
        call_count = 0

        def operation():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("always failing")

        result = policy.execute(operation, operation_name="test")

        assert result.success is False
        assert result.attempts == 3
        assert call_count == 3
        assert result.exhausted is True


class TestRetryPolicyIdempotency:
    """Architecture §14 — Duplicate Side Effect Protection."""

    def test_idempotency_check_prevents_retry(self):
        """If idempotency check returns True, assume success without retrying."""
        policy = RetryPolicy(RetryConfig(
            max_attempts=3,
            base_delay_seconds=0.01,
            jitter=False,
        ))
        call_count = 0

        def operation():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("network timeout")

        check_count = 0

        def idempotency_check():
            nonlocal check_count
            check_count += 1
            return True  # Previous attempt actually succeeded

        result = policy.execute(
            operation,
            idempotency_check=idempotency_check,
            operation_name="facebook_publish",
        )

        assert result.success is True
        assert call_count == 1  # Called once, failed
        assert check_count == 1  # Check confirmed it actually worked

    def test_idempotency_check_false_proceeds_with_retry(self):
        """If check returns False, retry the operation."""
        policy = RetryPolicy(RetryConfig(
            max_attempts=2,
            base_delay_seconds=0.01,
            jitter=False,
        ))

        def operation():
            raise ConnectionError("timeout")

        def idempotency_check():
            return False  # Previous attempt definitely failed

        result = policy.execute(
            operation,
            idempotency_check=idempotency_check,
            operation_name="facebook_publish",
        )

        assert result.success is False
        assert result.attempts == 2  # Tried both times


class TestRetryPolicyOperationTypes:
    """Test default configs for different operation types."""

    def test_ai_validation_never_retries(self):
        """Same input → same output. No point retrying."""
        config = RetryConfig.for_operation(OperationType.AI_VALIDATION)
        assert config.max_attempts == 1

    def test_platform_publish_retries_with_backoff(self):
        config = RetryConfig.for_operation(OperationType.PLATFORM_PUBLISH)
        assert config.max_attempts == 3
        assert config.backoff_multiplier == 2.0
        assert config.jitter is True

    def test_no_retry_config(self):
        config = RetryConfig.no_retry()
        assert config.max_attempts == 1
        assert config.base_delay_seconds == 0


class TestConvenienceFunction:
    """Test module-level retry() function."""

    def test_retry_function_works(self):
        result = retry(
            lambda: "ok",
            OperationType.DATABASE_READ,
            operation_name="test",
        )
        assert result.success is True
        assert result.value == "ok"
