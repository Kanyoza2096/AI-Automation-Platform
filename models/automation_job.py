"""
Persistent automation job model — Architecture §7.

v3.2 (2026-10-22) — PUBLISH TRANSITION FIX:
  mark_published() transitions RUNNING -> PUBLISHING -> PUBLISHED.
  VALID_TRANSITIONS did not include RUNNING -> PUBLISHING (nor
  GENERATING/VALIDATING/RENDERING -> PUBLISHING), so every publish
  failed with "Invalid state transition: RUNNING → PUBLISHING" after
  the Facebook API call had already succeeded. The post was live;
  the DB job stayed RUNNING; pg_cron reaped it to STALE; the retry
  cascade sent it back to QUEUED; and _get_or_create_automation_job
  skipped it forever. Publishing appeared to work while the state
  machine silently recorded every job as a failure.

  Fix: allow X -> PUBLISHING from RUNNING, GENERATING, VALIDATING,
  RENDERING, and READY. These are all states from which the workflow
  can hand off to PublishStep. The intermediate states are tracked
  in the workflow's in-memory trace but are not persisted as
  job.status between steps; mark_published must accept whatever
  active state the job is actually in.

v3.1 — Added ACTIVE_STATUS_VALUES and TERMINAL_STATUS_VALUES
       string tuples for database queries.

v3.0 FIXES:
  - Added CREATED → RUNNING as valid transition (scheduler creates
    jobs as CREATED then immediately transitions to RUNNING).
  - JobService.reset_to_pending() now correctly uses QUEUED instead
    of the non-existent PENDING enum value.
  - mark_failed() now transitions through RETRY_PENDING correctly
    for all active states.

Job Lifecycle (Architecture §22):
  CREATED → QUEUED → RUNNING → GENERATING → VALIDATING →
  RENDERING → READY → PUBLISHING → PUBLISHED

  CREATED → RUNNING (direct, for scheduler-created jobs)

Failure paths:
  Any active state → RETRY_PENDING (if retryable)
  Any active state → FAILED (if non-retryable)
  RETRY_PENDING → QUEUED (on retry)
  PUBLISHING (stale > 15 min) → STALE → RETRY_PENDING

Idempotency contract (Architecture §25):
  ONE JOB → ONE PUBLISHING RESULT
  Enforced via UNIQUE(idempotency_key) at database level.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════
# Job Status Enum
# ═══════════════════════════════════════════════════════════════════════════


class JobStatus(str, enum.Enum):
    """Canonical job statuses. Architecture §7."""

    CREATED = "CREATED"              # Job record exists, not yet queued
    QUEUED = "QUEUED"                # Waiting for a worker
    RUNNING = "RUNNING"              # Worker picked it up, pipeline executing
    GENERATING = "GENERATING"        # AI content generation in progress
    VALIDATING = "VALIDATING"        # Content validation in progress
    RENDERING = "RENDERING"          # Media rendering in progress
    READY = "READY"                  # Content + media ready, awaiting publish
    PUBLISHING = "PUBLISHING"        # Platform publish in progress
    PUBLISHED = "PUBLISHED"          # Successfully published

    # Terminal failure states
    FAILED = "FAILED"                # Non-retryable failure
    CANCELLED = "CANCELLED"          # Explicitly cancelled

    # Recovery states
    RETRY_PENDING = "RETRY_PENDING"  # Failed but retryable, waiting for retry
    STALE = "STALE"                  # Running too long, needs investigation


# ═══════════════════════════════════════════════════════════════════════════
# Status Sets
# ═══════════════════════════════════════════════════════════════════════════

ACTIVE_STATES: frozenset[JobStatus] = frozenset({
    JobStatus.CREATED,
    JobStatus.QUEUED,
    JobStatus.RUNNING,
    JobStatus.GENERATING,
    JobStatus.VALIDATING,
    JobStatus.RENDERING,
    JobStatus.READY,
    JobStatus.PUBLISHING,
    JobStatus.RETRY_PENDING,
    JobStatus.STALE,
})

TERMINAL_STATES: frozenset[JobStatus] = frozenset({
    JobStatus.PUBLISHED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
})

# ═══════════════════════════════════════════════════════════════════════════
# String Constants (for database queries — single source of truth)
# ═══════════════════════════════════════════════════════════════════════════

ACTIVE_STATUS_VALUES: tuple[str, ...] = tuple(
    s.value for s in ACTIVE_STATES
)

TERMINAL_STATUS_VALUES: tuple[str, ...] = tuple(
    s.value for s in TERMINAL_STATES
)

# ═══════════════════════════════════════════════════════════════════════════
# Transition Rules
# ═══════════════════════════════════════════════════════════════════════════

RETRYABLE_SOURCE_STATES: frozenset[JobStatus] = frozenset({
    JobStatus.RETRY_PENDING,
    JobStatus.STALE,
    JobStatus.FAILED,
})

# v3.0 — All active states can transition to RETRY_PENDING or FAILED
_ALL_ACTIVE = frozenset(ACTIVE_STATES)

# v3.0 — CREATED can go directly to RUNNING (scheduler path)
#        and also to QUEUED (queue-based path)
#
# v3.2 — RUNNING, GENERATING, VALIDATING, RENDERING all allow -> PUBLISHING.
#        The workflow tracks intermediate steps in memory but does not
#        persist them as job.status. When mark_published is called, the
#        job is still in the state the scheduler transitioned it to
#        (usually RUNNING). The state machine must permit the direct
#        handoff to PUBLISHING, or every publish fails at mark_published
#        even though the external side effect already occurred.
VALID_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.CREATED:        frozenset({
        JobStatus.QUEUED,
        JobStatus.RUNNING,       # v3.0 — scheduler direct path
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    }),
    JobStatus.QUEUED:         frozenset({
        JobStatus.RUNNING,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    }),
    JobStatus.RUNNING:        frozenset({
        JobStatus.GENERATING,
        JobStatus.PUBLISHING,    # v3.2 — direct handoff to PublishStep
        JobStatus.FAILED,
        JobStatus.RETRY_PENDING,
        JobStatus.STALE,
    }),
    JobStatus.GENERATING:     frozenset({
        JobStatus.VALIDATING,
        JobStatus.PUBLISHING,    # v3.2
        JobStatus.FAILED,
        JobStatus.RETRY_PENDING,
        JobStatus.STALE,
    }),
    JobStatus.VALIDATING:     frozenset({
        JobStatus.RENDERING,
        JobStatus.PUBLISHING,    # v3.2
        JobStatus.FAILED,
        JobStatus.RETRY_PENDING,
    }),
    JobStatus.RENDERING:      frozenset({
        JobStatus.READY,
        JobStatus.PUBLISHING,    # v3.2
        JobStatus.FAILED,
        JobStatus.RETRY_PENDING,
        JobStatus.STALE,
    }),
    JobStatus.READY:          frozenset({
        JobStatus.PUBLISHING,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }),
    JobStatus.PUBLISHING:     frozenset({
        JobStatus.PUBLISHED,
        JobStatus.FAILED,
        JobStatus.RETRY_PENDING,
        JobStatus.STALE,
    }),
    JobStatus.RETRY_PENDING:  frozenset({
        JobStatus.QUEUED,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    }),
    JobStatus.STALE:          frozenset({
        JobStatus.RETRY_PENDING,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }),
    # Terminal states
    JobStatus.PUBLISHED:      frozenset(),
    JobStatus.FAILED:         frozenset({JobStatus.QUEUED}),  # Manual retry
    JobStatus.CANCELLED:      frozenset(),
}


# ═══════════════════════════════════════════════════════════════════════════
# Workflow Type Enum
# ═══════════════════════════════════════════════════════════════════════════


class WorkflowType(str, enum.Enum):
    CONTENT_PUBLISH = "content_publish"
    NEWS_PUBLISH = "news_publish"
    MANUAL_PUBLISH = "manual_publish"


# ═══════════════════════════════════════════════════════════════════════════
# Automation Job Model
# ═══════════════════════════════════════════════════════════════════════════


class AutomationJob(BaseModel):
    """Persistent representation of an automation job.

    v3.0: CREATED → RUNNING is now a valid transition.
    mark_failed() correctly handles all active states.
    v3.2: X → PUBLISHING allowed from all in-progress states so
          mark_published succeeds after the external side effect.
    """

    # ── Identity ──────────────────────────────────────────────────────
    id: str = Field(
        default_factory=lambda: f"job_{uuid.uuid4().hex[:12]}",
    )
    job_id: str = Field(default="")
    idempotency_key: str = Field(...)

    # ── Tenant Context ────────────────────────────────────────────────
    workspace_id: str = Field(default="")
    brand_id: str = Field(...)

    # ── Execution Context ─────────────────────────────────────────────
    workflow_type: WorkflowType = Field(
        default=WorkflowType.CONTENT_PUBLISH,
    )
    platform: str = Field(default="facebook")
    social_account_id: Optional[str] = Field(default=None)

    # ── Scheduling ────────────────────────────────────────────────────
    source: str = Field(default="scheduled")
    scheduled_for: Optional[datetime] = Field(default=None)

    # ── Lifecycle ─────────────────────────────────────────────────────
    status: JobStatus = Field(default=JobStatus.CREATED)
    attempt_count: int = Field(default=0)
    max_attempts: int = Field(default=3)
    last_error: Optional[str] = Field(default=None)
    last_error_code: Optional[str] = Field(default=None)

    # ── Result ────────────────────────────────────────────────────────
    topic: Optional[str] = Field(default=None)
    platform_post_id: Optional[str] = Field(default=None)
    result: Optional[dict[str, Any]] = Field(default=None)

    # Phase A/B — organism decision that authorized this job
    decision_id: Optional[str] = Field(
        default=None,
        description="Council/Director/Reflex decision id required before publish",
    )
    correlation_id: Optional[str] = Field(default=None)

    # ── Timestamps ────────────────────────────────────────────────────
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    duration_ms: Optional[int] = Field(default=None)

    # ═══════════════════════════════════════════════════════════════════
    # State Transition Methods
    # ═══════════════════════════════════════════════════════════════════

    def transition_to(self, new_status: JobStatus) -> bool:
        """Attempt to transition to a new status.

        Returns True if the transition is valid and was applied.
        """
        allowed = VALID_TRANSITIONS.get(self.status, frozenset())
        if new_status not in allowed:
            return False

        self.status = new_status
        self.updated_at = datetime.now(timezone.utc)

        if new_status == JobStatus.RUNNING and self.started_at is None:
            self.started_at = datetime.now(timezone.utc)

        if new_status in TERMINAL_STATES:
            self.completed_at = datetime.now(timezone.utc)
            if self.started_at:
                self.duration_ms = int(
                    (self.completed_at - self.started_at).total_seconds()
                    * 1000
                )

        return True

    def record_attempt(
        self,
        error: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> None:
        self.attempt_count += 1
        if error:
            self.last_error = error
        if error_code:
            self.last_error_code = error_code
        self.updated_at = datetime.now(timezone.utc)

    def mark_success(
        self,
        post_id: Optional[str] = None,
        result: Optional[dict] = None,
    ) -> bool:
        if post_id:
            self.platform_post_id = post_id
        if result:
            self.result = result
        return self.transition_to(JobStatus.PUBLISHED)

    def mark_failed(
        self,
        error: str,
        error_code: Optional[str] = None,
        retryable: bool = False,
    ) -> bool:
        """Mark the job as failed.

        v3.0: Works from ANY active state. If retryable and under
        max_attempts, transitions to RETRY_PENDING. Otherwise FAILED.
        """
        self.attempt_count += 1
        self.last_error = error
        if error_code:
            self.last_error_code = error_code

        if retryable and self.attempt_count < self.max_attempts:
            # v3.0 — RETRY_PENDING is reachable from any active state
            if self.status in ACTIVE_STATES:
                return self.transition_to(JobStatus.RETRY_PENDING)
            # If not in an active state, try anyway
            return self.transition_to(JobStatus.RETRY_PENDING)

        return self.transition_to(JobStatus.FAILED)

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATES

    def is_active(self) -> bool:
        return self.status in ACTIVE_STATES

    def can_retry(self) -> bool:
        return (
            self.status in RETRYABLE_SOURCE_STATES
            and self.attempt_count < self.max_attempts
        )

    # ═══════════════════════════════════════════════════════════════════
    # Factory Methods
    # ═══════════════════════════════════════════════════════════════════

    @classmethod
    def for_scheduled_slot(
        cls,
        brand_id: str,
        workspace_id: str,
        slot_date: str,
        slot_hour: int,
        job_id: str,
        platform: str = "facebook",
    ) -> "AutomationJob":
        idempotency_key = (
            f"{brand_id}-{slot_date}-{slot_hour:02d}-content"
        )
        scheduled_for = datetime.fromisoformat(
            f"{slot_date}T{slot_hour:02d}:00:00+00:00"
        )
        return cls(
            job_id=job_id,
            idempotency_key=idempotency_key,
            brand_id=brand_id,
            workspace_id=workspace_id,
            platform=platform,
            workflow_type=WorkflowType.CONTENT_PUBLISH,
            source="scheduled",
            scheduled_for=scheduled_for,
            status=JobStatus.CREATED,
        )

    @classmethod
    def for_manual_trigger(
        cls,
        brand_id: str,
        workspace_id: str,
        job_id: str,
        platform: str = "facebook",
    ) -> "AutomationJob":
        now = datetime.now(timezone.utc)
        idempotency_key = (
            f"{brand_id}-manual-"
            f"{now.strftime('%Y%m%d-%H%M%S-%f')}"
        )
        return cls(
            job_id=job_id,
            idempotency_key=idempotency_key,
            brand_id=brand_id,
            workspace_id=workspace_id,
            platform=platform,
            workflow_type=WorkflowType.MANUAL_PUBLISH,
            source="manual",
            scheduled_for=now,
            status=JobStatus.CREATED,
        )

    # ═══════════════════════════════════════════════════════════════════
    # Serialization
    # ═══════════════════════════════════════════════════════════════════

    def to_db_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_status_dict(self) -> dict[str, Any]:
        _status_map = {
            "CREATED": "queued", "QUEUED": "queued",
            "RUNNING": "running", "GENERATING": "running",
            "VALIDATING": "running", "RENDERING": "running",
            "READY": "running", "PUBLISHING": "running",
            "PUBLISHED": "completed", "FAILED": "failed",
            "CANCELLED": "cancelled", "RETRY_PENDING": "queued",
            "STALE": "failed",
        }
        _progress_map = {
            "CREATED": 0, "QUEUED": 5, "RUNNING": 20,
            "GENERATING": 35, "VALIDATING": 50, "RENDERING": 65,
            "READY": 80, "PUBLISHING": 90, "PUBLISHED": 100,
            "FAILED": 0, "CANCELLED": 0, "RETRY_PENDING": 0,
            "STALE": 0,
        }
        raw_status = (
            self.status.value
            if hasattr(self.status, "value")
            else str(self.status)
        )
        return {
            "job_id": self.job_id,
            "status": _status_map.get(raw_status, raw_status.lower()),
            "progress": _progress_map.get(raw_status, 0),
            "current_step": raw_status,
            "started_at": (
                self.started_at.isoformat()
                if self.started_at else None
            ),
            "updated_at": (
                self.updated_at.isoformat()
                if self.updated_at else None
            ),
            "error": self.last_error or None,
            "brand_id": self.brand_id,
            "workflow_type": (
                self.workflow_type.value
                if hasattr(self.workflow_type, "value")
                else str(self.workflow_type)
            ),
            "platform": self.platform,
            "topic": self.topic,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "duration_ms": self.duration_ms,
            "created_at": (
                self.created_at.isoformat()
                if self.created_at else None
            ),
            "completed_at": (
                self.completed_at.isoformat()
                if self.completed_at else None
            ),
        }
