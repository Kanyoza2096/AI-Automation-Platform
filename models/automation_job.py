"""
Persistent automation job model — Architecture §7.

Replaces in-memory scheduler state (_fired_slots, _published_keys)
with a database-backed job lifecycle that survives restarts and
works correctly across multiple workers.

Job Lifecycle (Architecture §22):
  CREATED → QUEUED → RUNNING → GENERATING → VALIDATING →
  RENDERING → READY → PUBLISHING → PUBLISHED

Failure paths:
  Any state → FAILED (if non-retryable)
  Any state → RETRY_PENDING (if retryable)
  RETRY_PENDING → QUEUED (on retry)
  PUBLISHING (stale > 15 min) → STALE → RETRY_PENDING (if retryable)

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
# Transition Rules
# ═══════════════════════════════════════════════════════════════════════════

# Which states indicate the job is still active (not terminal)
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

# Terminal states — no further transitions allowed
TERMINAL_STATES: frozenset[JobStatus] = frozenset({
    JobStatus.PUBLISHED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
})

# States that can transition to QUEUED (for retry)
RETRYABLE_SOURCE_STATES: frozenset[JobStatus] = frozenset({
    JobStatus.RETRY_PENDING,
    JobStatus.STALE,
    JobStatus.FAILED,  # Manual retry only
})

# Valid transitions — maps from current state to allowed next states
VALID_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.CREATED:        frozenset({JobStatus.QUEUED, JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.QUEUED:         frozenset({JobStatus.RUNNING, JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.RUNNING:        frozenset({JobStatus.GENERATING, JobStatus.FAILED, JobStatus.RETRY_PENDING, JobStatus.STALE}),
    JobStatus.GENERATING:     frozenset({JobStatus.VALIDATING, JobStatus.FAILED, JobStatus.RETRY_PENDING, JobStatus.STALE}),
    JobStatus.VALIDATING:     frozenset({JobStatus.RENDERING, JobStatus.FAILED, JobStatus.RETRY_PENDING}),
    JobStatus.RENDERING:      frozenset({JobStatus.READY, JobStatus.FAILED, JobStatus.RETRY_PENDING, JobStatus.STALE}),
    JobStatus.READY:          frozenset({JobStatus.PUBLISHING, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.PUBLISHING:     frozenset({JobStatus.PUBLISHED, JobStatus.FAILED, JobStatus.RETRY_PENDING, JobStatus.STALE}),
    JobStatus.RETRY_PENDING:  frozenset({JobStatus.QUEUED, JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.STALE:          frozenset({JobStatus.RETRY_PENDING, JobStatus.FAILED, JobStatus.CANCELLED}),
    # Terminal states — no valid transitions
    JobStatus.PUBLISHED:      frozenset(),
    JobStatus.FAILED:         frozenset({JobStatus.QUEUED}),  # Manual retry only
    JobStatus.CANCELLED:      frozenset(),
}


# ═══════════════════════════════════════════════════════════════════════════
# Workflow Type Enum
# ═══════════════════════════════════════════════════════════════════════════


class WorkflowType(str, enum.Enum):
    """Types of automation workflows."""
    CONTENT_PUBLISH = "content_publish"
    NEWS_PUBLISH = "news_publish"
    MANUAL_PUBLISH = "manual_publish"


# ═══════════════════════════════════════════════════════════════════════════
# Automation Job Model
# ═══════════════════════════════════════════════════════════════════════════


class AutomationJob(BaseModel):
    """Persistent representation of an automation job.
    
    This is the database entity that replaces in-memory scheduler tracking.
    Every scheduled slot and manual trigger creates one AutomationJob record.
    
    Idempotency is enforced via the idempotency_key field, which should have
    a UNIQUE constraint at the database level.
    
    Architecture §8 — Database-Level Idempotency:
      UNIQUE(brand_id, scheduled_for, workflow_type)
      Two workers attempting the same job → ONE JOB, ONE PUBLICATION.
    """
    
    # ── Identity ──────────────────────────────────────────────────────────
    id: str = Field(
        default_factory=lambda: f"job_{uuid.uuid4().hex[:12]}",
        description="Unique job identifier"
    )
    
    # ── Correlation ───────────────────────────────────────────────────────
    job_id: str = Field(
        default="",
        description="Human-readable correlation ID (pub_YYYYMMDD_xxxxxxxx)"
    )
    
    idempotency_key: str = Field(
        ...,
        description="Unique key preventing duplicate execution. Format: {brand_id}-{date}-{hour}-{type}"
    )
    
    # ── Tenant Context ────────────────────────────────────────────────────
    workspace_id: str = Field(default="", description="Owning workspace")
    brand_id: str = Field(..., description="Owning brand")
    
    # ── Execution Context ─────────────────────────────────────────────────
    workflow_type: WorkflowType = Field(
        default=WorkflowType.CONTENT_PUBLISH,
        description="Type of workflow being executed"
    )
    
    platform: str = Field(
        default="facebook",
        description="Target platform for publishing"
    )
    
    social_account_id: Optional[str] = Field(
        default=None,
        description="Resolved social account ID (set at publish time)"
    )
    
    # ── Scheduling ────────────────────────────────────────────────────────
    source: str = Field(
        default="scheduled",
        description="Trigger source: 'scheduled' or 'manual'"
    )
    
    scheduled_for: Optional[datetime] = Field(
        default=None,
        description="When this job was scheduled to run (UTC)"
    )
    
    # ── Lifecycle ─────────────────────────────────────────────────────────
    status: JobStatus = Field(
        default=JobStatus.CREATED,
        description="Current job status"
    )
    
    attempt_count: int = Field(
        default=0,
        description="Number of execution attempts"
    )
    
    max_attempts: int = Field(
        default=3,
        description="Maximum retry attempts before dead-letter"
    )
    
    last_error: Optional[str] = Field(
        default=None,
        description="Error message from last failed attempt"
    )
    
    last_error_code: Optional[str] = Field(
        default=None,
        description="Machine-readable error code from last failure"
    )
    
    # ── Result ────────────────────────────────────────────────────────────
    topic: Optional[str] = Field(
        default=None,
        description="Content topic that was generated"
    )
    
    platform_post_id: Optional[str] = Field(
        default=None,
        description="External platform post ID after successful publish"
    )
    
    result: Optional[dict[str, Any]] = Field(
        default=None,
        description="Full structured result from the publishing operation"
    )
    
    # ── Timestamps ────────────────────────────────────────────────────────
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this job record was created (UTC)"
    )
    
    started_at: Optional[datetime] = Field(
        default=None,
        description="When execution began (UTC)"
    )
    
    completed_at: Optional[datetime] = Field(
        default=None,
        description="When execution completed or failed (UTC)"
    )
    
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this record was last modified (UTC)"
    )
    
    # ── Duration ──────────────────────────────────────────────────────────
    duration_ms: Optional[int] = Field(
        default=None,
        description="Total execution duration in milliseconds"
    )
    
    # ═══════════════════════════════════════════════════════════════════════
    # State Transition Methods
    # ═══════════════════════════════════════════════════════════════════════
    
    def transition_to(self, new_status: JobStatus) -> bool:
        """Attempt to transition to a new status.
        
        Returns True if the transition is valid and was applied.
        Returns False if the transition is invalid (no change made).
        
        Side effects:
          - Updates updated_at timestamp
          - Sets started_at if entering first active state
          - Sets completed_at if entering terminal state
        """
        if new_status not in VALID_TRANSITIONS.get(self.status, frozenset()):
            return False
        
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc)
        
        # Track when execution actually began
        if new_status == JobStatus.RUNNING and self.started_at is None:
            self.started_at = datetime.now(timezone.utc)
        
        # Track completion
        if new_status in TERMINAL_STATES:
            self.completed_at = datetime.now(timezone.utc)
            if self.started_at:
                self.duration_ms = int(
                    (self.completed_at - self.started_at).total_seconds() * 1000
                )
        
        return True
    
    def record_attempt(self, error: Optional[str] = None, error_code: Optional[str] = None) -> None:
        """Record an execution attempt.
        
        Call this before each retry. Increments attempt_count and
        optionally records the error that caused the retry.
        """
        self.attempt_count += 1
        if error:
            self.last_error = error
        if error_code:
            self.last_error_code = error_code
        self.updated_at = datetime.now(timezone.utc)
    
    def mark_success(self, post_id: Optional[str] = None, result: Optional[dict] = None) -> bool:
        """Mark the job as successfully published."""
        if post_id:
            self.platform_post_id = post_id
        if result:
            self.result = result
        return self.transition_to(JobStatus.PUBLISHED)
    
    def mark_failed(self, error: str, error_code: Optional[str] = None, retryable: bool = False) -> bool:
        """Mark the job as failed.
        
        If retryable and under max_attempts, transitions to RETRY_PENDING.
        Otherwise transitions to FAILED (terminal).
        """
        self.attempt_count += 1   
        self.last_error = error
        if error_code:
            self.last_error_code = error_code
        
        if retryable and self.attempt_count < self.max_attempts:
            return self.transition_to(JobStatus.RETRY_PENDING)
        
        return self.transition_to(JobStatus.FAILED)
    
    def is_terminal(self) -> bool:
        """Check if the job has reached a terminal state."""
        return self.status in TERMINAL_STATES
    
    def is_active(self) -> bool:
        """Check if the job is still in progress."""
        return self.status in ACTIVE_STATES
    
    def can_retry(self) -> bool:
        """Check if this job can be retried."""
        return (
            self.status in RETRYABLE_SOURCE_STATES
            and self.attempt_count < self.max_attempts
        )
    
    # ═══════════════════════════════════════════════════════════════════════
    # Factory Methods
    # ═══════════════════════════════════════════════════════════════════════
    
    @classmethod
    def for_scheduled_slot(
        cls,
        brand_id: str,
        workspace_id: str,
        slot_date: str,  # ISO format: YYYY-MM-DD
        slot_hour: int,
        job_id: str,
        platform: str = "facebook",
    ) -> "AutomationJob":
        """Create a job for a scheduled content slot.
        
        Idempotency key format: {brand_id}-{date}-{hour}-content
        This should be enforced as UNIQUE at the database level.
        """
        idempotency_key = f"{brand_id}-{slot_date}-{slot_hour:02d}-content"
        
        scheduled_for = datetime.fromisoformat(f"{slot_date}T{slot_hour:02d}:00:00+00:00")
        
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
        """Create a job for a manual publish trigger.
        
        Manual triggers use a unique idempotency key with microsecond
        precision to prevent accidental duplicates while allowing
        intentional multiple publishes.
        """
        now = datetime.now(timezone.utc)
        idempotency_key = f"{brand_id}-manual-{now.strftime('%Y%m%d-%H%M%S-%f')}"
        
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
    
    # ═══════════════════════════════════════════════════════════════════════
    # Serialization
    # ═══════════════════════════════════════════════════════════════════════
    
    def to_db_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary for Supabase insertion.
        
        Converts enums to strings and datetimes to ISO format.
        """
        data = self.model_dump(mode="json")
        # Pydantic serializes enums to their values by default with mode='json'
        return data
    
    def to_status_dict(self) -> dict[str, Any]:
        """
        Public-facing status summary (safe for API responses).

        Spec §7 — Canonical workflow status schema:
          job_id, status, progress, current_step, started_at, updated_at, error.
        Required states: queued, running, completed, failed, cancelled.
        """
        # Map internal granular statuses to spec canonical states
        _status_map = {
            "CREATED": "queued",
            "QUEUED": "queued",
            "RUNNING": "running",
            "GENERATING": "running",
            "VALIDATING": "running",
            "RENDERING": "running",
            "READY": "running",
            "PUBLISHING": "running",
            "PUBLISHED": "completed",
            "FAILED": "failed",
            "CANCELLED": "cancelled",
            "RETRY_PENDING": "queued",
            "STALE": "failed",
        }
        # Map internal status to approximate progress percentage
        _progress_map = {
            "CREATED": 0, "QUEUED": 5, "RUNNING": 20, "GENERATING": 35,
            "VALIDATING": 50, "RENDERING": 65, "READY": 80, "PUBLISHING": 90,
            "PUBLISHED": 100, "FAILED": 0, "CANCELLED": 0,
            "RETRY_PENDING": 0, "STALE": 0,
        }
        raw_status = self.status.value if hasattr(self.status, "value") else str(self.status)
        canonical_status = _status_map.get(raw_status, raw_status.lower())
        progress = _progress_map.get(raw_status, 0)

        return {
            # Spec §7 canonical fields
            "job_id": self.job_id,
            "status": canonical_status,
            "progress": progress,
            "current_step": self.status.value if hasattr(self.status, "value") else raw_status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "error": self.last_error or None,
            # Additional context fields
            "brand_id": self.brand_id,
            "workflow_type": self.workflow_type.value if hasattr(self.workflow_type, "value") else str(self.workflow_type),
            "platform": self.platform,
            "topic": self.topic,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
