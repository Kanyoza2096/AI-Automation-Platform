"""KDO AI Engineering Vertical contracts (foundation phase).

These contracts define engineering-task identity and lifecycle semantics. They do
not create execution authority, call an LLM/provider, access a repository, or
persist state. Persistence and execution must be delegated to KDO's existing
workspace, workforce, policy, and execution boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping
import hashlib
import json


class EngineeringTaskStatus(str, Enum):
    PROPOSED = "proposed"
    PLANNED = "planned"
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class EngineeringTaskBlock(str, Enum):
    INVALID_TASK = "invalid_task"
    INVALID_TRANSITION = "invalid_transition"
    COMPLETION_EVIDENCE_REQUIRED = "completion_evidence_required"
    REVIEW_NOT_APPROVED = "review_not_approved"
    TENANT_WORKSPACE_MISMATCH = "tenant_workspace_mismatch"


TERMINAL_STATUSES = frozenset({
    EngineeringTaskStatus.COMPLETED,
    EngineeringTaskStatus.FAILED,
    EngineeringTaskStatus.CANCELLED,
})

_ALLOWED_TRANSITIONS: dict[EngineeringTaskStatus, frozenset[EngineeringTaskStatus]] = {
    EngineeringTaskStatus.PROPOSED: frozenset({EngineeringTaskStatus.PLANNED, EngineeringTaskStatus.BLOCKED, EngineeringTaskStatus.CANCELLED}),
    EngineeringTaskStatus.PLANNED: frozenset({EngineeringTaskStatus.QUEUED, EngineeringTaskStatus.BLOCKED, EngineeringTaskStatus.CANCELLED}),
    EngineeringTaskStatus.QUEUED: frozenset({EngineeringTaskStatus.RUNNING, EngineeringTaskStatus.BLOCKED, EngineeringTaskStatus.CANCELLED}),
    EngineeringTaskStatus.RUNNING: frozenset({EngineeringTaskStatus.AWAITING_REVIEW, EngineeringTaskStatus.FAILED, EngineeringTaskStatus.BLOCKED, EngineeringTaskStatus.CANCELLED}),
    EngineeringTaskStatus.AWAITING_REVIEW: frozenset({EngineeringTaskStatus.RUNNING, EngineeringTaskStatus.AWAITING_APPROVAL, EngineeringTaskStatus.COMPLETED, EngineeringTaskStatus.BLOCKED, EngineeringTaskStatus.CANCELLED}),
    EngineeringTaskStatus.AWAITING_APPROVAL: frozenset({EngineeringTaskStatus.COMPLETED, EngineeringTaskStatus.RUNNING, EngineeringTaskStatus.BLOCKED, EngineeringTaskStatus.CANCELLED}),
    EngineeringTaskStatus.BLOCKED: frozenset({EngineeringTaskStatus.PLANNED, EngineeringTaskStatus.QUEUED, EngineeringTaskStatus.CANCELLED}),
    EngineeringTaskStatus.COMPLETED: frozenset(),
    EngineeringTaskStatus.FAILED: frozenset(),
    EngineeringTaskStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True)
class EngineeringTaskContract:
    """Stable, serializable contract for one engineering objective."""

    task_id: str
    tenant_id: str
    workspace_id: str
    objective: str
    acceptance_criteria: tuple[str, ...]
    idempotency_key: str
    status: EngineeringTaskStatus = EngineeringTaskStatus.PROPOSED
    revision: int = 1
    repository_ref: str | None = None
    base_revision: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    authority_granted: bool = False
    execution_requested: bool = False
    provider_called: bool = False

    def __post_init__(self) -> None:
        required = (self.task_id, self.tenant_id, self.workspace_id,
                    self.objective, self.idempotency_key)
        if any(not isinstance(value, str) or not value.strip() for value in required):
            raise ValueError(EngineeringTaskBlock.INVALID_TASK.value)
        if not self.acceptance_criteria or any(not str(x).strip() for x in self.acceptance_criteria):
            raise ValueError(EngineeringTaskBlock.INVALID_TASK.value)
        if self.revision < 1:
            raise ValueError(EngineeringTaskBlock.INVALID_TASK.value)
        if self.authority_granted or self.execution_requested or self.provider_called:
            raise ValueError("engineering_task_contract_cannot_grant_execution_authority")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "objective": self.objective,
            "acceptance_criteria": list(self.acceptance_criteria),
            "idempotency_key": self.idempotency_key,
            "status": self.status.value,
            "revision": self.revision,
            "repository_ref": self.repository_ref,
            "base_revision": self.base_revision,
            "metadata": dict(self.metadata),
            "evidence": dict(self.evidence),
            # These flags are invariantly false: this record is not an authority token.
            "authority_granted": False,
            "execution_requested": False,
            "provider_called": False,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def transition_engineering_task(
    task: EngineeringTaskContract,
    next_status: EngineeringTaskStatus,
    *,
    evidence: Mapping[str, Any] | None = None,
    tenant_id: str,
    workspace_id: str,
) -> EngineeringTaskContract:
    """Validate a lifecycle transition; caller remains responsible for durable CAS.

    A successful completion requires machine-verifiable test evidence and an
    explicit independent review result. This helper never executes or persists.
    """
    if tenant_id != task.tenant_id or workspace_id != task.workspace_id:
        raise ValueError(EngineeringTaskBlock.TENANT_WORKSPACE_MISMATCH.value)
    if next_status not in _ALLOWED_TRANSITIONS[task.status]:
        raise ValueError(EngineeringTaskBlock.INVALID_TRANSITION.value)

    new_evidence = dict(task.evidence)
    if evidence:
        new_evidence.update(dict(evidence))
    if next_status == EngineeringTaskStatus.COMPLETED:
        if new_evidence.get("tests_passed") is not True or not new_evidence.get("test_run_id"):
            raise ValueError(EngineeringTaskBlock.COMPLETION_EVIDENCE_REQUIRED.value)
        if new_evidence.get("review_status") != "approved" or not new_evidence.get("review_id"):
            raise ValueError(EngineeringTaskBlock.REVIEW_NOT_APPROVED.value)

    return replace(task, status=next_status, revision=task.revision + 1, evidence=new_evidence)
