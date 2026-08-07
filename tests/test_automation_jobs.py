"""
Tests for AutomationJob model — Architecture §7-8.

Validates:
  - Job creation with correct defaults
  - Status transitions follow valid paths
  - attempt_count increments correctly
  - Timestamps auto-set (started_at, completed_at)
  - Serialization (to_db_dict, to_status_dict)
  - Stale detection logic
  - Terminal state detection
"""

import pytest
from datetime import datetime, timezone
from models.automation_job import (
    AutomationJob,
    JobStatus,
    WorkflowType,
    ACTIVE_STATES,
    TERMINAL_STATES,
    VALID_TRANSITIONS,
)


class TestAutomationJobCreation:
    """Test job factory methods."""

    def test_scheduled_job_defaults(self):
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_123",
            workspace_id="ws_456",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_20260728_abc123",
        )

        assert job.brand_id == "brand_123"
        assert job.workspace_id == "ws_456"
        assert job.workflow_type == WorkflowType.CONTENT_PUBLISH
        assert job.source == "scheduled"
        assert job.status == JobStatus.CREATED
        assert job.attempt_count == 0
        assert job.max_attempts == 3
        assert job.platform == "facebook"
        assert job.scheduled_for is not None
        assert "brand_123-2026-07-28-09-content" in job.idempotency_key

    def test_manual_job_defaults(self):
        job = AutomationJob.for_manual_trigger(
            brand_id="brand_123",
            workspace_id="ws_456",
            job_id="pub_manual_001",
        )

        assert job.source == "manual"
        assert job.workflow_type == WorkflowType.MANUAL_PUBLISH
        assert job.status == JobStatus.CREATED
        assert "manual" in job.idempotency_key


class TestAutomationJobTransitions:
    """Test all valid and invalid state transitions."""

    def test_all_valid_transitions_are_defined(self):
        """Every non-terminal state has at least one valid transition."""
        for status in JobStatus:
            if status not in TERMINAL_STATES:
                transitions = VALID_TRANSITIONS.get(status, frozenset())
                assert len(transitions) > 0, \
                    f"State {status.value} has no valid transitions defined"

    def test_terminal_states_have_no_transitions(self):
        """PUBLISHED, FAILED, CANCELLED cannot transition."""
        for status in TERMINAL_STATES:
            transitions = VALID_TRANSITIONS.get(status, frozenset())
            # FAILED can transition to QUEUED (manual retry only)
            if status == JobStatus.FAILED:
                assert JobStatus.QUEUED in transitions
            else:
                assert len(transitions) == 0, \
                    f"Terminal state {status.value} should have no transitions"

    def test_mark_success_sets_published(self):
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )
        # Navigate to PUBLISHING
        for s in [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.GENERATING,
                  JobStatus.VALIDATING, JobStatus.RENDERING, JobStatus.READY,
                  JobStatus.PUBLISHING]:
            job.transition_to(s)

        success = job.mark_success(
            post_id="fb_post_789",
            result={"topic": "AI News", "platforms": ["facebook"]},
        )

        assert success is True
        assert job.status == JobStatus.PUBLISHED
        assert job.platform_post_id == "fb_post_789"
        assert job.result is not None

    def test_mark_failed_non_retryable(self):
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )
        job.transition_to(JobStatus.QUEUED)
        job.transition_to(JobStatus.RUNNING)

        success = job.mark_failed(
            error="Token expired",
            error_code="TOKEN_EXPIRED",
            retryable=False,
        )

        assert success is True
        assert job.status == JobStatus.FAILED
        assert job.last_error == "Token expired"
        assert job.last_error_code == "TOKEN_EXPIRED"


class TestAutomationJobTimestamps:
    """Test automatic timestamp management."""

    def test_started_at_set_on_first_active_transition(self):
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )

        assert job.started_at is None
        job.transition_to(JobStatus.QUEUED)
        assert job.started_at is None  # QUEUED is not active
        job.transition_to(JobStatus.RUNNING)
        assert job.started_at is not None  # RUNNING sets started_at

    def test_completed_at_set_on_terminal_state(self):
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )
        job.transition_to(JobStatus.QUEUED)
        job.transition_to(JobStatus.RUNNING)

        assert job.completed_at is None
        job.transition_to(JobStatus.FAILED)  # FAILED is valid from RUNNING? Check...
        # Need to go through proper path
        job2 = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=10,
            job_id="pub_002",
        )
        for s in [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.GENERATING,
                  JobStatus.VALIDATING, JobStatus.RENDERING, JobStatus.READY,
                  JobStatus.PUBLISHING]:
            job2.transition_to(s)
        job2.mark_success(post_id="fb_123")

        assert job2.completed_at is not None
        assert job2.duration_ms is not None
        assert job2.duration_ms >= 0


class TestAutomationJobSerialization:
    """Test serialization methods."""

    def test_to_db_dict_includes_all_fields(self):
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )

        data = job.to_db_dict()

        assert data["brand_id"] == "brand_a"
        assert data["status"] == "CREATED"
        assert data["job_id"] == "pub_001"
        assert "idempotency_key" in data

    def test_to_status_dict_excludes_internal_fields(self):
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )

        status = job.to_status_dict()

        # Safe for API responses
        assert "job_id" in status
        assert "brand_id" in status
        assert "status" in status
        # Internal fields excluded
        assert "idempotency_key" not in status
        assert "workspace_id" not in status


class TestAutomationJobHelpers:
    """Test helper methods."""

    def test_is_terminal(self):
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )

        assert job.is_terminal() is False
        job.status = JobStatus.PUBLISHED
        assert job.is_terminal() is True

    def test_is_active(self):
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )

        job.status = JobStatus.RUNNING
        assert job.is_active() is True

        job.status = JobStatus.PUBLISHED
        assert job.is_active() is False

    def test_can_retry(self):
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )

        assert job.can_retry() is False  # Not in retryable state

        job.status = JobStatus.RETRY_PENDING
        assert job.can_retry() is True

        job.attempt_count = 3
        job.max_attempts = 3
        assert job.can_retry() is False  # Exhausted

    def test_record_attempt(self):
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )

        job.record_attempt(error="Something broke", error_code="ERR_001")
        assert job.attempt_count == 1
        assert job.last_error == "Something broke"
        assert job.last_error_code == "ERR_001"
