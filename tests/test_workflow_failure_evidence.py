"""Regression tests for durable workflow failure diagnostics."""

import asyncio

from core.errors import AIValidationError
from workflows.base import Step, StepResult, StepStatus, WorkflowResult


class RejectingStep(Step):
    name = "validate_content"

    async def async_run(self, context):
        raise AIValidationError(
            "Independent semantic review did not approve publication",
            context={
                "topic": "Kruskal's algorithm",
                "semantic_review_status": "needs_review",
                "semantic_review_reason": "one claim is uncertain",
                "semantic_review_request_id": "review-123",
                "access_token": "must-not-be-persisted",
            },
        )


def test_application_error_keeps_machine_code_and_safe_diagnostics():
    result = asyncio.run(RejectingStep().execute_async({}))

    assert result.status == StepStatus.FAILED
    assert result.error_code == "AI_VALIDATION_ERROR"
    assert "Independent semantic review" in result.error
    assert result.error_detail["exception_type"] == "AIValidationError"
    assert result.error_detail["context"] == {
        "topic": "Kruskal's algorithm",
        "semantic_review_status": "needs_review",
        "semantic_review_reason": "one claim is uncertain",
        "semantic_review_request_id": "review-123",
    }
    assert "access_token" not in str(result.error_detail)


def test_workflow_failure_evidence_contains_step_reason_and_progress():
    workflow_result = WorkflowResult(workflow_name="post_publish")
    workflow_result.steps = [
        StepResult(
            step_name="select_topic",
            status=StepStatus.SUCCESS,
            output={"topic": "Kruskal's algorithm"},
        ),
        StepResult(
            step_name="validate_content",
            status=StepStatus.FAILED,
            error="Independent semantic review did not approve publication",
            error_code="AI_VALIDATION_ERROR",
            error_detail={
                "exception_type": "AIValidationError",
                "context": {"semantic_review_status": "needs_review"},
            },
            latency_ms=42.4,
        ),
    ]
    workflow_result.finished_at = workflow_result.started_at + 0.1

    evidence = workflow_result.failure_evidence()

    assert evidence["schema_version"] == 1
    assert evidence["workflow_name"] == "post_publish"
    assert evidence["completed_steps"] == ["select_topic"]
    assert evidence["failed_steps"] == [{
        "step": "validate_content",
        "error": "Independent semantic review did not approve publication",
        "error_code": "AI_VALIDATION_ERROR",
        "detail": {
            "exception_type": "AIValidationError",
            "context": {"semantic_review_status": "needs_review"},
        },
        "latency_ms": 42,
    }]
    assert evidence["duration_ms"] == 100
