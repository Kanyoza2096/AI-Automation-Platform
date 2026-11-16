"""Regression tests for provider-outage classification in the brand scheduler."""

from scheduler.brand_scheduler import (
    _is_ai_provider_outage,
    _workflow_failure_is_retryable,
)


def test_gemini_quota_failure_opens_provider_cooldown():
    evidence = {
        "failed_steps": [{
            "step": "generate_content",
            "error": "Gemini HTTP 503: service unavailable",
        }]
    }
    assert _is_ai_provider_outage(evidence) is True
    assert _workflow_failure_is_retryable(evidence) is False


def test_cloudflare_ai_rate_limit_opens_provider_cooldown():
    evidence = {
        "failed_steps": [{
            "step": "generate_content",
            "error": "Cloudflare AI HTTP 429 rate limit exceeded",
        }]
    }
    assert _is_ai_provider_outage(evidence) is True
    assert _workflow_failure_is_retryable(evidence) is False


def test_unrelated_service_failure_is_not_classified_as_ai_provider_outage():
    evidence = {
        "failed_steps": [{
            "step": "publish",
            "error": "Facebook HTTP 503 service unavailable",
        }]
    }
    assert _is_ai_provider_outage(evidence) is False
    assert _workflow_failure_is_retryable(evidence) is True


def test_deterministic_content_validation_stays_non_retryable():
    evidence = {
        "failed_steps": [{
            "step": "validate_content",
            "error_code": "AI_VALIDATION_ERROR",
            "error": "Generated content failed validation",
        }]
    }
    assert _is_ai_provider_outage(evidence) is False
    assert _workflow_failure_is_retryable(evidence) is False
