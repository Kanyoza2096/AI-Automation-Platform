from scheduler.brand_scheduler import _workflow_failure_is_retryable


def test_ai_validation_failure_is_not_retried():
    evidence = {
        "failed_steps": [
            {"step": "generate_content", "error_code": "AI_VALIDATION_ERROR"}
        ]
    }
    assert _workflow_failure_is_retryable(evidence) is False


def test_editorial_quality_rejection_is_not_retried():
    evidence = {
        "failed_steps": [
            {"step": "validate_content", "error_code": "editorial_quality_gate_rejected"}
        ]
    }
    assert _workflow_failure_is_retryable(evidence) is False


def test_transient_or_unclassified_failure_remains_retryable():
    evidence = {
        "failed_steps": [
            {"step": "knowledge_context", "error_code": "database_timeout"}
        ]
    }
    assert _workflow_failure_is_retryable(evidence) is True


def test_empty_failure_evidence_fails_closed():
    assert _workflow_failure_is_retryable({"failed_steps": []}) is False
