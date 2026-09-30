"""
Centralised retry policy — Architecture §13.

Consolidates retry decisions that would otherwise be scattered across:
  - Facebook plugin (API rate limits, network errors)
  - Gemini service (timeouts, provider errors)
  - Scheduler (slot retry after failure)
  - Workflow engine (pipeline step retry)

Policy rules (Architecture §13):
  - Retryable: network timeout, temporary provider outage, rate limit, 5xx
  - Non-retryable: invalid token, permission denied, invalid request
  - Backoff: exponential with jitter (1s → 2s → 4s → 8s ... max 60s)
  - Max attempts: configurable per operation type

Architecture §14 — Duplicate Side Effect Protection:
  - Retries around publishing MUST check idempotency before re-attempting.
  - "Facebook accepted, network timeout" ≠ "Facebook rejected."
  - Use idempotency_key to determine if the side effect already occurred.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, TypeVar

from core.errors import KanyozaError, is_retryable

logger = logging.getLogger("kanyoza.services.retry_policy")

T = TypeVar("T")


# ═══════════════════════════════════════════════════════════════════════════
# Operation Type — determines default retry behaviour
# ═══════════════════════════════════════════════════════════════════════════


class OperationType(str, Enum):
    """Categories of operations with different retry characteristics."""
    
    AI_GENERATION = "ai_generation"      # Gemini calls — retryable on timeout
    AI_VALIDATION = "ai_validation"      # Schema validation — not retryable (same input)
    RENDERING = "rendering"              # Image generation — retryable
    PLATFORM_PUBLISH = "platform_publish"  # External API — careful with idempotency
    PLATFORM_READ = "platform_read"      # External API reads — safely retryable
    DATABASE_READ = "database_read"      # Supabase reads — retryable
    DATABASE_WRITE = "database_write"    # Supabase writes — retryable with caution


# ═══════════════════════════════════════════════════════════════════════════
# Default retry configurations per operation type
# ═══════════════════════════════════════════════════════════════════════════

_DEFAULTS: dict[OperationType, dict] = {
    OperationType.AI_GENERATION: {
        "max_attempts": 3,
        "base_delay_seconds": 2.0,
        "max_delay_seconds": 30.0,
        "backoff_multiplier": 2.0,
        "jitter": True,
    },
    OperationType.AI_VALIDATION: {
        "max_attempts": 1,  # Same input → same result, no point retrying
        "base_delay_seconds": 0.0,
        "max_delay_seconds": 0.0,
        "backoff_multiplier": 1.0,
        "jitter": False,
    },
    OperationType.RENDERING: {
        "max_attempts": 3,
        "base_delay_seconds": 1.0,
        "max_delay_seconds": 15.0,
        "backoff_multiplier": 2.0,
        "jitter": True,
    },
    OperationType.PLATFORM_PUBLISH: {
        "max_attempts": 3,
        "base_delay_seconds": 2.0,
        "max_delay_seconds": 60.0,
        "backoff_multiplier": 2.0,
        "jitter": True,
    },
    OperationType.PLATFORM_READ: {
        "max_attempts": 3,
        "base_delay_seconds": 1.0,
        "max_delay_seconds": 10.0,
        "backoff_multiplier": 2.0,
        "jitter": True,
    },
    OperationType.DATABASE_READ: {
        "max_attempts": 3,
        "base_delay_seconds": 0.5,
        "max_delay_seconds": 5.0,
        "backoff_multiplier": 2.0,
        "jitter": True,
    },
    OperationType.DATABASE_WRITE: {
        "max_attempts": 3,
        "base_delay_seconds": 1.0,
        "max_delay_seconds": 10.0,
        "backoff_multiplier": 2.0,
        "jitter": True,
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# Retry Policy Configuration
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class RetryConfig:
    """Configuration for a retry policy.
    
    Attributes:
        max_attempts: Total number of attempts (1 = no retry)
        base_delay_seconds: Initial delay before first retry
        max_delay_seconds: Maximum delay between retries (cap)
        backoff_multiplier: Multiply delay by this after each attempt
        jitter: If True, add ±25% random jitter to avoid thundering herd
    """
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    backoff_multiplier: float = 2.0
    jitter: bool = True
    
    @classmethod
    def for_operation(cls, operation: OperationType) -> "RetryConfig":
        """Get default retry config for an operation type."""
        defaults = _DEFAULTS.get(operation, _DEFAULTS[OperationType.PLATFORM_PUBLISH])
        return cls(**defaults)
    
    @classmethod
    def no_retry(cls) -> "RetryConfig":
        """Config that never retries."""
        return cls(max_attempts=1, base_delay_seconds=0, jitter=False)
    
    @classmethod
    def aggressive(cls) -> "RetryConfig":
        """Aggressive retry for critical operations."""
        return cls(
            max_attempts=5,
            base_delay_seconds=1.0,
            max_delay_seconds=120.0,
            backoff_multiplier=2.0,
            jitter=True,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Retry Result
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class RetryResult:
    """Outcome of a retryable operation."""
    success: bool
    value: Optional[T] = None
    error: Optional[Exception] = None
    attempts: int = 0
    total_delay_ms: float = 0.0
    
    @property
    def exhausted(self) -> bool:
        """True if all attempts were used without success."""
        return not self.success and self.error is not None


# ═══════════════════════════════════════════════════════════════════════════
# Retry Policy
# ═══════════════════════════════════════════════════════════════════════════


class RetryPolicy:
    """Executes an operation with exponential backoff and jitter.
    
    Usage:
        policy = RetryPolicy(RetryConfig.for_operation(OperationType.PLATFORM_PUBLISH))
        
        result = policy.execute(
            lambda: facebook_service.post_to_page(...),
            idempotency_check=lambda: job_service.is_published(job_id),
        )
        
        if result.success:
            handle_success(result.value)
        else:
            handle_failure(result.error, result.attempts)
    
    Architecture §14 — Duplicate Side Effect Protection:
      The optional idempotency_check callback is called BEFORE each retry
      to determine whether the previous attempt actually succeeded on the
      external platform despite appearing to fail locally.
      
      Example: Facebook accepted the post but the HTTP response timed out.
      On retry, idempotency_check should query "does post_id exist?"
      If yes → return success without re-publishing.
      If no → retry the publish.
    """
    
    def __init__(self, config: Optional[RetryConfig] = None) -> None:
        self.config = config or RetryConfig()
    
    def execute(
        self,
        operation: Callable[[], T],
        *,
        idempotency_check: Optional[Callable[[], bool]] = None,
        on_retry: Optional[Callable[[int, Exception], None]] = None,
        operation_name: str = "unknown",
    ) -> RetryResult[T]:
        """Execute an operation with retry logic.
        
        Args:
            operation: The callable to execute (must be idempotent for publish ops)
            idempotency_check: Called before retry — if returns True, assumes
                              previous attempt succeeded and returns success.
            on_retry: Optional callback invoked before each retry.
                     Receives (attempt_number, exception).
            operation_name: Human-readable name for logging.
        
        Returns:
            RetryResult with success status, value, or error.
        """
        if operation_name == OperationType.PLATFORM_PUBLISH.value and self.config.max_attempts > 1 and idempotency_check is None:
            raise ValueError("PLATFORM_PUBLISH retries require an idempotency_check to prevent duplicate external side effects")

        last_error: Optional[Exception] = None
        total_delay_ms = 0.0
        attempt = 0
        
        while attempt < self.config.max_attempts:
            attempt += 1
            
            try:
                value = operation()
                logger.debug(
                    "RetryPolicy: '%s' succeeded on attempt %d/%d",
                    operation_name, attempt, self.config.max_attempts,
                )
                return RetryResult(
                    success=True,
                    value=value,
                    attempts=attempt,
                    total_delay_ms=total_delay_ms,
                )
            
            except Exception as exc:
                last_error = exc
                
                # Check if this error is retryable
                if not is_retryable(exc):
                    logger.warning(
                        "RetryPolicy: '%s' failed with non-retryable error on attempt %d: %s",
                        operation_name, attempt, exc,
                    )
                    return RetryResult(
                        success=False,
                        error=exc,
                        attempts=attempt,
                        total_delay_ms=total_delay_ms,
                    )
                
                # Check if we've exhausted attempts
                if attempt >= self.config.max_attempts:
                    logger.error(
                        "RetryPolicy: '%s' exhausted all %d attempts. Last error: %s",
                        operation_name, self.config.max_attempts, exc,
                    )
                    return RetryResult(
                        success=False,
                        error=exc,
                        attempts=attempt,
                        total_delay_ms=total_delay_ms,
                    )
                
                # Before retrying, check idempotency (for publish operations)
                if idempotency_check:
                    try:
                        already_succeeded = idempotency_check()
                        if already_succeeded:
                            logger.info(
                                "RetryPolicy: '%s' — idempotency check indicates "
                                "previous attempt succeeded. Skipping retry.",
                                operation_name,
                            )
                            return RetryResult(
                                success=True,
                                value=None,  # Caller should handle None for this case
                                attempts=attempt,
                                total_delay_ms=total_delay_ms,
                            )
                    except Exception as check_exc:
                        logger.debug(
                            "RetryPolicy: idempotency check failed: %s — proceeding with retry",
                            check_exc,
                        )
                
                # Calculate delay with exponential backoff
                delay = self._calculate_delay(attempt)
                total_delay_ms += delay * 1000
                
                logger.warning(
                    "RetryPolicy: '%s' attempt %d/%d failed: %s. "
                    "Retrying in %.1fs...",
                    operation_name, attempt, self.config.max_attempts,
                    exc, delay,
                )
                
                if on_retry:
                    try:
                        on_retry(attempt, exc)
                    except Exception:
                        pass  # on_retry callback should never block the retry
                
                time.sleep(delay)
        
        # Should not reach here, but safety net
        return RetryResult(
            success=False,
            error=last_error or RuntimeError("Unknown retry error"),
            attempts=attempt,
            total_delay_ms=total_delay_ms,
        )
    
    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay for the given attempt number.
        
        Formula: base_delay * (backoff_multiplier ^ (attempt - 1))
        Then capped at max_delay.
        Then ±25% jitter if enabled.
        """
        delay = self.config.base_delay_seconds * (
            self.config.backoff_multiplier ** (attempt - 1)
        )
        
        # Cap at max delay
        delay = min(delay, self.config.max_delay_seconds)
        
        # Apply jitter: randomize between 75% and 125% of calculated delay
        if self.config.jitter and delay > 0:
            jitter_factor = random.uniform(0.75, 1.25)
            delay *= jitter_factor
        
        return round(delay, 2)
    
    # ═══════════════════════════════════════════════════════════════════
    # Convenience factory methods
    # ═══════════════════════════════════════════════════════════════════
    
    @classmethod
    def for_ai(cls) -> "RetryPolicy":
        return cls(RetryConfig.for_operation(OperationType.AI_GENERATION))
    
    @classmethod
    def for_render(cls) -> "RetryPolicy":
        return cls(RetryConfig.for_operation(OperationType.RENDERING))
    
    @classmethod
    def for_publish(cls) -> "RetryPolicy":
        return cls(RetryConfig.for_operation(OperationType.PLATFORM_PUBLISH))
    
    @classmethod
    def for_database(cls) -> "RetryPolicy":
        return cls(RetryConfig.for_operation(OperationType.DATABASE_READ))
    
    @classmethod
    def never(cls) -> "RetryPolicy":
        return cls(RetryConfig.no_retry())


# ═══════════════════════════════════════════════════════════════════════════
# Module-level convenience function
# ═══════════════════════════════════════════════════════════════════════════


def retry(
    operation: Callable[[], T],
    operation_type: OperationType = OperationType.PLATFORM_PUBLISH,
    *,
    config: Optional[RetryConfig] = None,
    idempotency_check: Optional[Callable[[], bool]] = None,
    operation_name: str = "unknown",
) -> RetryResult[T]:
    """Convenience function for one-shot retry execution.
    
    Usage:
        result = retry(
            lambda: facebook.post(message),
            OperationType.PLATFORM_PUBLISH,
            idempotency_check=lambda: db.job_exists(job_id),
            operation_name="facebook_publish",
        )
    """
    policy_config = config or RetryConfig.for_operation(operation_type)
    policy = RetryPolicy(policy_config)
    return policy.execute(
        operation,
        idempotency_check=idempotency_check,
        operation_name=operation_name,
    )
