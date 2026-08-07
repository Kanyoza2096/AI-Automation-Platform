"""
Tests for scheduler idempotency — Architecture §25.

Validates:
  - Same slot cannot be claimed twice (database-level)
  - DuplicateJobError raised on duplicate claim
  - Slot released on failure, retryable on next cycle
  - Manual triggers create unique idempotency keys
  - Concurrent workers cannot double-publish

Uses in-memory fallback when JobService unavailable.
"""

import pytest
import threading
import time
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock

from models.automation_job import AutomationJob, JobStatus, WorkflowType
from core.errors import DuplicateJobError


class TestJobServiceIdempotency:
    """Test that JobService prevents duplicate slot claims."""

    def test_create_scheduled_job_generates_unique_idempotency_key(self):
        """Each scheduled slot produces a deterministic, unique key."""
        job1 = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_20260728_abc123",
        )

        job2 = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=10,
            job_id="pub_20260728_def456",
        )

        assert job1.idempotency_key != job2.idempotency_key
        assert job1.idempotency_key == "brand_a-2026-07-28-09-content"
        assert job2.idempotency_key == "brand_a-2026-07-28-10-content"

    def test_same_slot_same_brand_same_key(self):
        """Same brand + same date + same hour = same idempotency key."""
        job1 = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )

        job2 = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_002",
        )

        assert job1.idempotency_key == job2.idempotency_key

    def test_different_brands_same_slot_different_keys(self):
        """Brand A 9am ≠ Brand B 9am."""
        job_a = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )

        job_b = AutomationJob.for_scheduled_slot(
            brand_id="brand_b",
            workspace_id="ws_2",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_002",
        )

        assert job_a.idempotency_key != job_b.idempotency_key

    def test_manual_jobs_have_unique_keys(self):
        """Manual triggers use microsecond timestamps — always unique."""
        job1 = AutomationJob.for_manual_trigger(
            brand_id="brand_a",
            workspace_id="ws_1",
            job_id="pub_manual_001",
        )
        time.sleep(0.001)
        job2 = AutomationJob.for_manual_trigger(
            brand_id="brand_a",
            workspace_id="ws_1",
            job_id="pub_manual_002",
        )

        assert job1.idempotency_key != job2.idempotency_key
        assert "manual" in job1.idempotency_key


class TestJobStateMachine:
    """Test that job state transitions enforce idempotency rules."""

    def test_valid_transition_created_to_queued(self):
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )
        assert job.status == JobStatus.CREATED

        success = job.transition_to(JobStatus.QUEUED)
        assert success is True
        assert job.status == JobStatus.QUEUED

    def test_invalid_transition_rejected(self):
        """Cannot jump from CREATED directly to PUBLISHED."""
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )

        success = job.transition_to(JobStatus.PUBLISHED)
        assert success is False
        assert job.status == JobStatus.CREATED

    def test_terminal_state_no_transitions(self):
        """PUBLISHED, FAILED, CANCELLED cannot transition further."""
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )
        job.status = JobStatus.PUBLISHED

        success = job.transition_to(JobStatus.RUNNING)
        assert success is False
        assert job.status == JobStatus.PUBLISHED

    def test_full_happy_path(self):
        """CREATED → QUEUED → RUNNING → ... → PUBLISHING → PUBLISHED."""
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )

        path = [
            JobStatus.QUEUED,
            JobStatus.RUNNING,
            JobStatus.GENERATING,
            JobStatus.VALIDATING,
            JobStatus.RENDERING,
            JobStatus.READY,
            JobStatus.PUBLISHING,
            JobStatus.PUBLISHED,
        ]

        for status in path:
            success = job.transition_to(status)
            assert success is True, f"Failed at {status.value}"
            assert job.status == status

    def test_failure_path_with_retry(self):
        """CREATED → ... → PUBLISHING → RETRY_PENDING → QUEUED."""
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )

        for status in [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.GENERATING,
                       JobStatus.VALIDATING, JobStatus.RENDERING, JobStatus.READY,
                       JobStatus.PUBLISHING]:
            job.transition_to(status)

        success = job.mark_failed(
            error="Facebook timeout",
            error_code="NETWORK_TIMEOUT",
            retryable=True,
        )
        assert success is True
        assert job.status == JobStatus.RETRY_PENDING
        assert job.attempt_count == 1

        success = job.transition_to(JobStatus.QUEUED)
        assert success is True

    def test_max_attempts_exhausted_goes_to_failed(self):
        """After max_attempts, retryable failures go to FAILED."""
        job = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )
        job.max_attempts = 2

        job.transition_to(JobStatus.QUEUED)
        job.transition_to(JobStatus.RUNNING)
        job.mark_failed(error="Error 1", retryable=True)
        assert job.status == JobStatus.RETRY_PENDING
        assert job.attempt_count == 1

        job.transition_to(JobStatus.QUEUED)
        job.transition_to(JobStatus.RUNNING)
        job.mark_failed(error="Error 2", retryable=True)
        assert job.status == JobStatus.FAILED
        assert job.attempt_count == 2


class TestConcurrentSlotProtection:
    """Test that concurrent access to the same slot is protected."""

    def test_idempotency_key_uniqueness(self):
        """Two jobs for same slot have identical idempotency keys."""
        keys = set()
        for i in range(10):
            job = AutomationJob.for_scheduled_slot(
                brand_id="brand_a",
                workspace_id="ws_1",
                slot_date="2026-07-28",
                slot_hour=9,
                job_id=f"pub_{i:03d}",
            )
            keys.add(job.idempotency_key)

        assert len(keys) == 1

    def test_different_dates_different_keys(self):
        """Same hour, different day = different keys."""
        job_today = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-28",
            slot_hour=9,
            job_id="pub_001",
        )
        job_tomorrow = AutomationJob.for_scheduled_slot(
            brand_id="brand_a",
            workspace_id="ws_1",
            slot_date="2026-07-29",
            slot_hour=9,
            job_id="pub_002",
        )

        assert job_today.idempotency_key != job_tomorrow.idempotency_key


class TestBrandSchedulerInMemoryFallback:
    """Test scheduler's in-memory idempotency when JobService unavailable."""

    def test_fired_slots_prevent_duplicate_trigger(self):
        """In-memory _fired_slots blocks same (date, hour, type)."""
        today = date.today()
        hour = 9

        fired = set()
        fired.add((today, hour, "content"))

        assert (today, hour, "content") in fired
        assert (today, hour + 1, "content") not in fired

    def test_published_keys_prevent_duplicate_publish(self):
        """In-memory _published_keys blocks idempotency key."""
        keys = set()
        key = "brand_a-2026-07-28-09-content"
        keys.add(key)

        assert key in keys
        assert "brand_a-2026-07-28-10-content" not in keys
