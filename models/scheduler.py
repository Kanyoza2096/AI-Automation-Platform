"""
Scheduler domain models — typed job definitions for the unified scheduler.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import Field

from models.base import KanyozaModel


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class JobType(str, Enum):
    CONTENT_POST = "content_post"
    NEWS_POST = "news_post"
    GUARDIAN_SCAN = "guardian_scan"
    MEMORY_PRUNE = "memory_prune"
    CLEANUP = "cleanup"
    HEALTH_CHECK = "health_check"
    RETRY = "retry"
    CUSTOM = "custom"


class ScheduledJob(KanyozaModel):
    """
    A single unit of scheduled work.
    Immutable — create a new instance for each state transition.
    """

    job_id: str = Field(..., description="Unique job identifier")
    job_type: JobType
    status: JobStatus = Field(default=JobStatus.PENDING)

    # Execution schedule
    scheduled_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    run_at: Optional[datetime] = Field(default=None, description="Specific execution time (None = run ASAP)")

    # Retry configuration
    max_retries: int = Field(default=3, ge=0)
    retry_count: int = Field(default=0, ge=0)
    retry_delay_seconds: int = Field(default=60, ge=0)

    # Execution context (serializable payload)
    payload: Dict[str, Any] = Field(default_factory=dict)

    # Result tracking
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None

    # Tracing
    correlation_id: Optional[str] = None

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        )

    @property
    def can_retry(self) -> bool:
        return (
            self.status == JobStatus.FAILED
            and self.retry_count < self.max_retries
        )

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def mark_running(self) -> "ScheduledJob":
        return self.model_copy(
            update={"status": JobStatus.RUNNING, "started_at": KanyozaModel.utcnow()}
        )

    def mark_completed(self, result: Optional[Dict[str, Any]] = None) -> "ScheduledJob":
        return self.model_copy(
            update={
                "status": JobStatus.COMPLETED,
                "completed_at": KanyozaModel.utcnow(),
                "result": result,
            }
        )

    def mark_failed(self, error: str) -> "ScheduledJob":
        return self.model_copy(
            update={
                "status": JobStatus.FAILED,
                "completed_at": KanyozaModel.utcnow(),
                "error": error,
            }
        )

    def mark_retry(self) -> "ScheduledJob":
        return self.model_copy(
            update={
                "status": JobStatus.RETRYING,
                "retry_count": self.retry_count + 1,
            }
        )
