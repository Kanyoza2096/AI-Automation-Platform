"""Durable Director orchestration runtime over native engine adapters.

This layer coordinates only advisory/read-only native work. It never invokes
KCPG commands, Action Kernel execution, publishing, or other external actions.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import math
from typing import Any, Callable, Mapping, Sequence

from .contracts import (
    AdapterRegistry,
    EngineAttemptReconciliation,
    EngineContribution,
    ExecutionAttemptReference,
    MissionTaskContext,
)
from .recovery import RecoveryAssessment, assess_execution_recovery
from .state_machine import (
    MissionEventCommand,
    MissionRuntimeSnapshot,
    MissionRuntimeConflict,
    MissionStateMachine,
    MissionTransitionRejected,
)
from .store import MissionRuntimeStore
from .swarm_planning import SwarmCandidate, SwarmLimits, SwarmPlan, plan_swarm


logger = logging.getLogger(__name__)


class MissionRuntimeNotFound(LookupError):
    pass


class MissionTaskNotReady(RuntimeError):
    pass


class MissionOrchestrationRuntime:
    """Coordinates persisted mission transitions and actual native engine calls."""

    def __init__(
        self,
        *,
        store: MissionRuntimeStore,
        adapters: AdapterRegistry,
        state_machine: MissionStateMachine | None = None,
        task_state_callback: Callable[[str, str, Mapping[str, Any] | None, str | None], None] | None = None,
        default_timeout_seconds: float = 75.0,
    ) -> None:
        if default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds_must_be_positive")
        self.store = store
        self.adapters = adapters
        self.state_machine = state_machine or MissionStateMachine()
        self.task_state_callback = task_state_callback
        self.default_timeout_seconds = float(default_timeout_seconds)

    async def load_snapshot(
        self, *, mission_id: str, workspace_id: str
    ) -> MissionRuntimeSnapshot:
        row = await self.store.get_mission(
            mission_id=mission_id,
            scope_type="workspace",
            workspace_id=workspace_id,
        )
        if not row:
            raise MissionRuntimeNotFound("workspace_mission_not_found")
        if row.get("scope_type") != "workspace" or row.get("workspace_id") != workspace_id:
            raise MissionRuntimeNotFound("workspace_mission_not_found")
        return MissionRuntimeSnapshot.from_row(row)

    async def _commit(
        self,
        current: MissionRuntimeSnapshot,
        *,
        event_type: str,
        idempotency_key: str,
        actor_type: str,
        actor_id: str,
        correlation_id: str,
        payload: Mapping[str, Any] | None = None,
        task_id: str | None = None,
        recipient_type: str | None = None,
        recipient_id: str | None = None,
        message_id: str | None = None,
        thread_id: str | None = None,
        round_number: int = 0,
        evidence_refs: Sequence[Mapping[str, Any]] = (),
    ) -> MissionRuntimeSnapshot:
        event_payload = dict(payload or {})
        sync_rules = {
            "task.started": ("executing", {"approved"}),
            "task.progress": ("executing", {"executing"}),
            "task.completed": ("completed", {"executing"}),
            "task.failed": ("failed", {"executing"}),
            "task.blocked": ("blocked", {"approval_required", "approved", "executing"}),
            "task.verified": ("verified", {"completed"}),
            "task.cancelled": ("cancelled", {"approval_required", "approved"}),
            "task.reassigned": ("approval_required", {"approval_required", "approved"}),
            "task.handoff.accepted": ("approval_required", {"executing"}),
        }
        rule = sync_rules.get(event_type)
        task_sync = None
        callback_result = None
        callback_error = None
        if task_id and rule:
            if self.task_state_callback is None:
                raise MissionTaskNotReady("runtime_task_state_callback_required")
            target_status, expected_statuses = rule
            if event_type == "task.completed":
                callback_result = {
                    key: event_payload.get(key)
                    for key in ("summary", "findings", "recommendations", "limitations", "confidence")
                    if key in event_payload
                }
            elif event_type in {"task.failed", "task.blocked", "task.cancelled"}:
                callback_error = str(event_payload.get("reason") or event_type)[:2000]
            task_sync = {
                "target_status": target_status,
                "expected_statuses": sorted(expected_statuses),
                "result": callback_result,
                "error": callback_error,
            }
        snapshot = await self.state_machine.commit(
            self.store,
            current,
            MissionEventCommand(
                event_type=event_type,
                idempotency_key=idempotency_key,
                actor_type=actor_type,
                actor_id=actor_id,
                correlation_id=correlation_id,
                payload=event_payload,
                task_id=task_id,
                recipient_type=recipient_type,
                recipient_id=recipient_id,
                message_id=message_id,
                thread_id=thread_id,
                round_number=round_number,
                evidence_refs=tuple(evidence_refs),
                task_sync=task_sync,
            ),
        )
        if task_id and rule and self.task_state_callback is not None:
            try:
                self.task_state_callback(task_id, task_sync["target_status"], callback_result, callback_error)
            except Exception:
                # The database RPC already committed both the mission event and
                # canonical task row. A cache/registry callback failure must not
                # make callers retry a completed transition as if it rolled back.
                # The canonical row is authoritative and can rebuild in-memory state.
                logger.exception(
                    "director_task_registry_sync_failed_after_durable_commit",
                    extra={"task_id": task_id, "target_status": task_sync["target_status"]},
                )
        return snapshot

    async def spawn_swarm(
        self,
        *,
        mission_id: str,
        workspace_id: str,
        task_ids: Sequence[str],
        candidates: Sequence[SwarmCandidate],
        complexity_score: float,
        idempotency_prefix: str,
        correlation_id: str,
        limits: SwarmLimits | None = None,
        director_id: str = "director",
    ) -> tuple[SwarmPlan, MissionRuntimeSnapshot]:
        """Plan and durably register a bounded specialist wave under Director control.

        Repeated calls may add specialists during an active mission. Existing
        agent history, active concurrency, and per-task occupancy reduce the
        available capacity before the next wave is planned.
        """
        current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        mission_task_ids = set(current.task_ids)
        requested_task_ids = {str(value).strip() for value in task_ids if str(value).strip()}
        if not requested_task_ids or not requested_task_ids.issubset(mission_task_ids):
            raise MissionTransitionRejected("swarm_tasks_outside_mission_scope")

        existing = dict(current.state_snapshot.get("agents") or {})
        active = [
            item for item in existing.values()
            if item.get("status") in {"spawned", "running"}
        ]
        base = limits or SwarmLimits()
        remaining_total = 8 - len(existing)
        remaining_parallel = 4 - len(active)
        if remaining_total < 1 or remaining_parallel < 1:
            raise MissionTransitionRejected("mission_agent_capacity_exhausted")

        occupied_by_task: dict[str, int] = {}
        for item in active:
            occupied_by_task[str(item.get("task_id") or "")] = (
                occupied_by_task.get(str(item.get("task_id") or ""), 0) + 1
            )
        candidates_by_task: dict[str, list[SwarmCandidate]] = {}
        for candidate in candidates:
            if candidate.agent_id not in existing and candidate.task_id in requested_task_ids:
                candidates_by_task.setdefault(candidate.task_id, []).append(candidate)
        available_candidates: list[SwarmCandidate] = []
        for candidate_task_id, task_candidates in candidates_by_task.items():
            task_slots = max(0, 3 - occupied_by_task.get(candidate_task_id, 0))
            task_candidates.sort(key=lambda item: (-item.priority, item.engine_id, item.agent_id))
            available_candidates.extend(task_candidates[:task_slots])

        existing_estimated_tokens = sum(
            int(item.get("estimated_tokens", 0) or 0) for item in existing.values()
        )
        budget = dict(current.state_snapshot.get("budget") or {})
        mission_token_limit = budget.get("max_tokens")
        mission_tokens_remaining = (
            int(mission_token_limit) - int(budget.get("tokens_used", 0)) - existing_estimated_tokens
            if mission_token_limit is not None else base.max_total_estimated_tokens - existing_estimated_tokens
        )
        estimated_tokens_remaining = min(
            base.max_total_estimated_tokens - existing_estimated_tokens,
            mission_tokens_remaining,
        )
        effective_max = min(base.max_agents, remaining_total, remaining_parallel)
        if estimated_tokens_remaining < 1:
            raise MissionTransitionRejected("swarm_estimated_token_budget_exhausted")
        effective_limits = SwarmLimits(
            max_agents=effective_max,
            max_parallel_agents=min(base.max_parallel_agents, effective_max),
            max_agents_per_task=min(base.max_agents_per_task, effective_max),
            max_total_estimated_tokens=estimated_tokens_remaining,
        )
        plan = plan_swarm(
            complexity_score=complexity_score,
            task_ids=sorted(requested_task_ids),
            candidates=available_candidates,
            registry=self.adapters,
            limits=effective_limits,
        )

        latest = current
        for candidate in plan.selected:
            latest = await self.spawn_agent(
                mission_id=mission_id,
                workspace_id=workspace_id,
                task_id=candidate.task_id,
                agent_id=candidate.agent_id,
                engine_id=candidate.engine_id,
                capability=candidate.capability,
                role=candidate.role,
                estimated_tokens=candidate.estimated_tokens,
                idempotency_key=f"{idempotency_prefix}:{candidate.agent_id}",
                correlation_id=correlation_id,
                director_id=director_id,
            )
        return plan, latest

    async def spawn_agent(
        self,
        *,
        mission_id: str,
        workspace_id: str,
        task_id: str,
        agent_id: str,
        engine_id: str,
        capability: str,
        role: str,
        idempotency_key: str,
        correlation_id: str,
        estimated_tokens: int = 1500,
        director_id: str = "director",
    ) -> MissionRuntimeSnapshot:
        """Durably add one advisory specialist to an existing mission task.

        This records an agent assignment only. It does not execute the engine,
        approve canonical tasks, create tasks, or grant external-action authority.
        """
        adapter = self.adapters.get(engine_id)
        if capability not in adapter.capabilities:
            raise MissionTransitionRejected("engine_capability_not_registered")
        current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        if task_id not in set(current.task_ids):
            raise MissionTransitionRejected("agent_task_not_in_mission")
        return await self._commit(
            current,
            event_type="agent.spawned",
            idempotency_key=idempotency_key,
            actor_type="director",
            actor_id=director_id,
            correlation_id=correlation_id,
            task_id=task_id,
            payload={
                "agent_id": agent_id,
                "task_id": task_id,
                "engine_id": engine_id,
                "capability": capability,
                "role": role,
                "estimated_tokens": estimated_tokens,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def update_agent_status(
        self,
        *,
        mission_id: str,
        workspace_id: str,
        agent_id: str,
        status: str,
        idempotency_key: str,
        correlation_id: str,
        reason: str | None = None,
        director_id: str = "director",
    ) -> MissionRuntimeSnapshot:
        """Advance a persisted agent lifecycle under Director control."""
        if status not in {"running", "completed", "failed", "terminated"}:
            raise MissionTransitionRejected("agent_status_invalid")
        current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        payload: dict[str, Any] = {
            "agent_id": agent_id,
            "status": status,
            "finished_at": datetime.now(timezone.utc).isoformat()
            if status in {"completed", "failed", "terminated"} else "",
        }
        if reason is not None:
            payload["reason"] = str(reason)[:1000]
        return await self._commit(
            current,
            event_type="agent.status.changed",
            idempotency_key=idempotency_key,
            actor_type="director",
            actor_id=director_id,
            correlation_id=correlation_id,
            payload=payload,
        )

    async def assign_task(
        self,
        *,
        mission_id: str,
        workspace_id: str,
        task_id: str,
        engine_id: str,
        capability: str,
        idempotency_key: str,
        correlation_id: str,
        director_id: str = "director",
    ) -> MissionRuntimeSnapshot:
        adapter = self.adapters.get(engine_id)
        if capability not in adapter.capabilities:
            raise MissionTransitionRejected("engine_capability_not_registered")
        current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        task_row = await self.store.get_workspace_task(
            task_id=task_id, workspace_id=workspace_id, mission_id=mission_id
        )
        if not task_row or task_row.get("status") != "approval_required":
            raise MissionTaskNotReady("canonical_task_not_awaiting_approval")
        return await self._commit(
            current,
            event_type="task.assigned",
            idempotency_key=idempotency_key,
            actor_type="director",
            actor_id=director_id,
            correlation_id=correlation_id,
            task_id=task_id,
            payload={"engine_id": engine_id, "capability": capability},
        )

    async def approve_task(
        self,
        *,
        mission_id: str,
        workspace_id: str,
        task_id: str,
        operator_id: str,
        approved_at: datetime,
        idempotency_key: str,
        correlation_id: str,
    ) -> MissionRuntimeSnapshot:
        """Atomically persist canonical approval provenance and task.approved."""
        current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        task_row = await self.store.get_workspace_task(
            task_id=task_id, workspace_id=workspace_id, mission_id=mission_id
        )
        if not task_row:
            raise MissionTaskNotReady("canonical_workspace_task_not_found")
        payload = task_row.get("payload") or {}
        approved_at_value = approved_at.astimezone(timezone.utc).isoformat()
        if task_row.get("status") == "approved":
            # The RPC may have committed while its response was lost, leaving
            # process memory stale. The canonical task row supplies the original
            # timestamp, and the durable event proves whether this is a replay.
            canonical_approved_at = str(payload.get("approved_at") or "")
            if (
                payload.get("approved_by") != operator_id
                or not canonical_approved_at
                or (current.state_snapshot.get("task_status") or {}).get(task_id) != "approved"
            ):
                raise MissionTaskNotReady("canonical_approval_provenance_conflict")
            canonical_key = f"director-task-approval:{task_id}:{canonical_approved_at}"
            prior_event = await self.store.find_event(
                mission_id=mission_id,
                scope_type="workspace",
                workspace_id=workspace_id,
                idempotency_key=canonical_key,
                event_type="task.approved",
                task_id=task_id,
            )
            if (
                not prior_event
                or prior_event.get("actor_type") != "operator"
                or prior_event.get("actor_id") != operator_id
                or (prior_event.get("payload") or {}).get("approved_at") != canonical_approved_at
            ):
                raise MissionTaskNotReady("canonical_approval_journal_provenance_conflict")
            return current
        if task_row.get("status") != "approval_required":
            raise MissionTaskNotReady("canonical_task_not_awaiting_approval")
        if (current.state_snapshot.get("task_status") or {}).get(task_id) != "approval_required":
            raise MissionTaskNotReady("canonical_approval_journal_state_mismatch")

        command = MissionEventCommand(
            event_type="task.approved",
            idempotency_key=idempotency_key,
            actor_type="operator",
            actor_id=operator_id,
            correlation_id=correlation_id,
            payload={"approved_at": approved_at_value},
            task_id=task_id,
        )
        transition = self.state_machine.prepare(current, command)
        result = await self.store.approve_workspace_task_atomic(
            event=transition.event,
            task_id=task_id,
            approved_by=operator_id,
            approved_at=approved_at_value,
        )
        if not result:
            raise MissionTaskNotReady("atomic_task_approval_not_acknowledged")
        if result.get("error"):
            raise MissionTaskNotReady(str(result["error"]))
        if result.get("replayed"):
            return await self.load_snapshot(
                mission_id=mission_id, workspace_id=workspace_id
            )
        if int(result.get("version", -1)) != transition.next_snapshot.version:
            raise MissionTaskNotReady("atomic_task_approval_version_acknowledgement_mismatch")
        if self.task_state_callback is not None:
            try:
                self.task_state_callback(task_id, "approved", None, None)
            except Exception:
                logger.exception(
                    "director_task_registry_sync_failed_after_atomic_approval",
                    extra={"task_id": task_id},
                )
        return transition.next_snapshot

    async def cancel_task(
        self,
        *,
        mission_id: str,
        workspace_id: str,
        task_id: str,
        operator_id: str,
        reason: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> MissionRuntimeSnapshot:
        current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        task_row = await self.store.get_workspace_task(
            task_id=task_id, workspace_id=workspace_id, mission_id=mission_id
        )
        if not task_row:
            raise MissionTaskNotReady("canonical_workspace_task_not_found")
        if task_row.get("status") == "cancelled":
            if (current.state_snapshot.get("task_status") or {}).get(task_id) == "cancelled":
                return current
            raise MissionTaskNotReady("cancelled_task_requires_mission_journal_reconciliation")
        if task_row.get("status") not in {"approval_required", "approved"}:
            raise MissionTaskNotReady("task_not_cancellable_from_canonical_status")
        return await self._commit(
            current,
            event_type="task.cancelled",
            idempotency_key=idempotency_key,
            actor_type="operator",
            actor_id=operator_id,
            correlation_id=correlation_id,
            task_id=task_id,
            payload={"reason": str(reason or "Task cancelled by operator")[:1000]},
        )

    @staticmethod
    def _evidence_refs(contribution: EngineContribution) -> list[dict[str, str]]:
        refs: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in contribution.evidence:
            ref = str(item.get("ref") or "").strip()
            if ref and ref not in seen:
                refs.append({"ref": ref[:300]})
                seen.add(ref)
            if len(refs) >= 30:
                break
        return refs

    @staticmethod
    def _bounded_texts(values: Sequence[Any], *, limit: int = 12, width: int = 600) -> list[str]:
        return [
            str(value).strip()[:width]
            for value in list(values or ())[:limit]
            if str(value).strip()
        ]

    @staticmethod
    def _bounded_input_mapping(values: Mapping[str, Any] | None) -> dict[str, Any]:
        if values is None:
            return {}
        if not isinstance(values, Mapping):
            raise TypeError("recipient_inputs_must_be_mapping")
        bounded: dict[str, Any] = {}
        for key, value in list(values.items())[:20]:
            name = str(key).strip()[:80]
            if not name:
                continue
            if isinstance(value, str):
                bounded[name] = value[:2000]
            elif isinstance(value, bool) or value is None or isinstance(value, (int, float)):
                bounded[name] = value
            elif isinstance(value, (list, tuple)):
                bounded[name] = [
                    item[:500] if isinstance(item, str) else item
                    for item in list(value)[:10]
                    if isinstance(item, (str, int, float, bool)) or item is None
                ]
        return bounded

    @staticmethod
    def _usage_int(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        try:
            number = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, number)

    async def reconcile_task_attempt(
        self,
        *,
        mission_id: str,
        workspace_id: str,
        task_id: str,
    ) -> EngineAttemptReconciliation:
        """Read-only authoritative adapter lookup for one persisted execution attempt."""
        snapshot = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        task = await self.store.get_workspace_task(
            task_id=task_id, workspace_id=workspace_id, mission_id=mission_id
        )
        if not task:
            raise MissionRuntimeNotFound("workspace_mission_task_not_found")
        if task.get("status") not in {"executing", "failed", "blocked"}:
            raise MissionTaskNotReady("task_has_no_unresolved_execution_attempt")

        event_limit = min(500, int(getattr(self.store, "MAX_EVENT_READ_LIMIT", 500)))
        events = await self.store.list_events(
            mission_id=mission_id,
            scope_type="workspace",
            workspace_id=workspace_id,
            limit=event_limit,
        )
        if len(events) >= event_limit:
            raise MissionRuntimeConflict("mission_event_history_may_be_truncated")
        starts = [
            event for event in events
            if event.get("task_id") == task_id
            and event.get("event_type") == "task.started"
            and event.get("scope_type") == "workspace"
            and event.get("workspace_id") == workspace_id
            and event.get("mission_id") == mission_id
        ]
        if len(starts) != 1:
            raise MissionRuntimeConflict("execution_attempt_start_event_missing_or_duplicated")
        start = starts[0]
        start_key = str(start.get("idempotency_key") or "")
        payload = start.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        attempt_id = start_key[:-len(":started")] if start_key.endswith(":started") else ""
        assignments = snapshot.state_snapshot.get("task_assignments") or {}
        assignment = assignments.get(task_id) if isinstance(assignments, Mapping) else None
        engine_id = str((assignment or {}).get("engine_id") or "")
        capability = str((assignment or {}).get("capability") or "")
        if (
            not attempt_id
            or len(attempt_id) > 150
            or payload.get("attempt_id") != attempt_id
            or payload.get("engine_id") != engine_id
            or payload.get("capability") != capability
            or not engine_id
            or not capability
            or start.get("actor_type") not in {"operator", "director"}
            or not str(start.get("actor_id") or "").strip()
            or not str(start.get("correlation_id") or "").strip()
        ):
            raise MissionRuntimeConflict("execution_attempt_identity_or_assignment_mismatch")

        reference = ExecutionAttemptReference(
            attempt_id=attempt_id,
            engine_id=engine_id,
            capability=capability,
            task_id=task_id,
            mission_id=mission_id,
            workspace_id=workspace_id,
            correlation_id=str(start["correlation_id"]),
        )
        return await self.adapters.reconcile_attempt(reference)

    async def reconcile_and_record_task_attempt(
        self,
        *,
        mission_id: str,
        workspace_id: str,
        task_id: str,
        correlation_id: str,
    ) -> EngineAttemptReconciliation:
        """Query one attempt and append the observed result without changing task authority."""
        reconciliation = await self.reconcile_task_attempt(
            mission_id=mission_id,
            workspace_id=workspace_id,
            task_id=task_id,
        )
        current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        task = await self.store.get_workspace_task(
            task_id=task_id, workspace_id=workspace_id, mission_id=mission_id
        )
        if not task:
            raise MissionRuntimeNotFound("workspace_mission_task_not_found")
        if task.get("status") not in {"executing", "failed", "blocked"}:
            raise MissionTaskNotReady("canonical_task_not_in_recovery_review_state")
        assignments = current.state_snapshot.get("task_assignments") or {}
        assignment = assignments.get(task_id) if isinstance(assignments, Mapping) else None
        capability = str((assignment or {}).get("capability") or "")
        if not capability:
            raise MissionRuntimeConflict("execution_attempt_capability_missing")
        result = reconciliation.result if isinstance(reconciliation.result, Mapping) else {}
        payload = {
            "attempt_id": reconciliation.attempt_id,
            "engine_id": reconciliation.engine_id,
            "capability": capability,
            "status": reconciliation.status,
            "authoritative": reconciliation.authoritative,
            "evidence_ref": reconciliation.evidence_ref,
            "summary": str(result.get("summary") or "")[:5000],
            "completed_at": str(result.get("completed_at") or "")[:64],
        }
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:20]
        await self._commit(
            current,
            event_type="task.reconciliation.observed",
            idempotency_key=f"{reconciliation.attempt_id}:reconciliation:{fingerprint}",
            actor_type="system",
            actor_id="director_recovery_reconciler",
            correlation_id=correlation_id,
            task_id=task_id,
            payload=payload,
        )
        return reconciliation

    async def review_task_recovery(
        self,
        *,
        mission_id: str,
        workspace_id: str,
        task_id: str,
        operator_id: str,
        decision: str,
        reason: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> MissionRuntimeSnapshot:
        """Append an operator's hold/reconciliation decision without changing task authority."""
        if decision not in {"hold", "request_reconciliation"}:
            raise MissionTransitionRejected("task_recovery_decision_not_permitted")
        if not str(reason or "").strip():
            raise ValueError("task_recovery_review_reason_required")
        assessment = await self.assess_task_recovery(
            mission_id=mission_id,
            workspace_id=workspace_id,
            task_id=task_id,
            stale_after_seconds=300,
        )
        current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        task = await self.store.get_workspace_task(
            task_id=task_id, workspace_id=workspace_id, mission_id=mission_id
        )
        if not task:
            raise MissionRuntimeNotFound("workspace_mission_task_not_found")
        if task.get("status") not in {"executing", "failed", "blocked"}:
            raise MissionTaskNotReady("canonical_task_not_in_recovery_review_state")
        return await self._commit(
            current,
            event_type="task.recovery.reviewed",
            idempotency_key=idempotency_key,
            actor_type="operator",
            actor_id=operator_id,
            correlation_id=correlation_id,
            task_id=task_id,
            payload={
                "decision": decision,
                "reason": str(reason).strip()[:1000],
                "assessment_classification": assessment.classification,
                "assessment_reason": assessment.reason[:300],
            },
        )

    async def assess_task_recovery(
        self,
        *,
        mission_id: str,
        workspace_id: str,
        task_id: str,
        stale_after_seconds: float,
        now: datetime | None = None,
    ) -> RecoveryAssessment:
        """Read-only recovery assessment; this method never changes task state or dispatches work."""
        if (
            isinstance(stale_after_seconds, bool)
            or not isinstance(stale_after_seconds, (int, float))
            or not math.isfinite(float(stale_after_seconds))
            or stale_after_seconds <= 0
        ):
            raise ValueError("recovery_stale_after_seconds_must_be_positive_finite_number")
        snapshot = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        task = await self.store.get_workspace_task(
            task_id=task_id, workspace_id=workspace_id, mission_id=mission_id
        )
        if not task:
            raise MissionRuntimeNotFound("workspace_mission_task_not_found")
        event_limit = min(500, int(getattr(self.store, "MAX_EVENT_READ_LIMIT", 500)))
        events = await self.store.list_events(
            mission_id=mission_id,
            scope_type="workspace",
            workspace_id=workspace_id,
            limit=event_limit,
        )
        if len(events) >= event_limit:
            return RecoveryAssessment(
                "outcome_unknown", mission_id, workspace_id, task_id, None, None,
                "mission_event_history_read_limit_reached; full_attempt_history_required",
            )
        return assess_execution_recovery(
            task=task,
            events=events,
            mission_id=mission_id,
            workspace_id=workspace_id,
            assigned_engine_id=str(((snapshot.state_snapshot.get("task_assignments") or {}).get(task_id) or {}).get("engine_id") or "") or None,
            assigned_capability=str(((snapshot.state_snapshot.get("task_assignments") or {}).get(task_id) or {}).get("capability") or "") or None,
            now=now or datetime.now(timezone.utc),
            stale_after=timedelta(seconds=float(stale_after_seconds)),
        )

    async def execute_approved_task(
        self,
        *,
        mission_id: str,
        workspace_id: str,
        task_id: str,
        engine_id: str,
        objective: str,
        inputs: Mapping[str, Any],
        idempotency_prefix: str,
        correlation_id: str,
        estimated_tokens: int = 1800,
        timeout_seconds: float | None = None,
        operator_id: str = "director",
    ) -> EngineContribution:
        current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        task_row = await self.store.get_workspace_task(
            task_id=task_id, workspace_id=workspace_id, mission_id=mission_id
        )
        if (
            not task_row
            or task_row.get("status") != "approved"
            or not task_row.get("payload", {}).get("approved_by")
        ):
            raise MissionTaskNotReady("canonical_director_approval_not_persisted")
        task_payload = task_row.get("payload") if isinstance(task_row.get("payload"), Mapping) else {}
        dependency_ids = [
            str(value).strip()
            for value in (task_payload.get("depends_on") or [])
            if str(value).strip()
        ]
        for dependency_id in dependency_ids:
            dependency_row = await self.store.get_workspace_task(
                task_id=dependency_id,
                workspace_id=workspace_id,
                mission_id=mission_id,
            )
            if not dependency_row:
                raise MissionTaskNotReady("mission_dependency_missing_or_out_of_scope")
            if dependency_row.get("status") not in {"completed", "verified"}:
                raise MissionTaskNotReady("mission_dependency_not_completed")
        state = dict(current.state_snapshot or {})
        assignments = dict(state.get("task_assignments") or {})
        assignment = assignments.get(task_id)
        if not assignment or assignment.get("engine_id") != engine_id:
            raise MissionTaskNotReady("task_assignment_mismatch")
        if (state.get("task_status") or {}).get(task_id) != "approved":
            raise MissionTaskNotReady("task_requires_operator_approval")

        adapter = self.adapters.get(engine_id)
        capability = str(assignment.get("capability") or "")
        if capability not in adapter.capabilities:
            raise MissionTransitionRejected("assigned_engine_capability_unavailable")
        self.state_machine.ensure_budget_available(
            current,
            estimated_tokens=estimated_tokens,
            estimated_provider_calls=1,
        )

        if (
            not isinstance(idempotency_prefix, str)
            or not idempotency_prefix.strip()
            or idempotency_prefix != idempotency_prefix.strip()
            or len(idempotency_prefix) > 150
        ):
            raise MissionTransitionRejected("execution_attempt_identity_invalid")

        # Refuse a previously used attempt identity before task state can be reconsidered.
        # This read is not the concurrency lock; the atomic event append + task sync below
        # remains the claim boundary.
        prior_start = await self.store.find_event(
            mission_id=mission_id,
            scope_type="workspace",
            workspace_id=workspace_id,
            idempotency_key=f"{idempotency_prefix}:started",
            event_type="task.started",
            task_id=task_id,
        )
        if prior_start:
            raise MissionRuntimeConflict("execution_attempt_identity_already_used")

        # The existing append-only mission event is the durable attempt claim.
        # Its atomic task sync changes approved -> executing in the same DB transaction.
        # Reuse that ledger rather than maintaining a second table that could drift.
        current = await self._commit(
            current,
            event_type="task.started",
            idempotency_key=f"{idempotency_prefix}:started",
            actor_type="operator" if operator_id != "director" else "director",
            actor_id=operator_id,
            correlation_id=correlation_id,
            task_id=task_id,
            payload={
                "attempt_id": idempotency_prefix,
                "engine_id": engine_id,
                "capability": capability,
                "requested_by": operator_id,
            },
        )
        dependency_task_ids = {
            str(value) for value in (task_row.get("payload", {}).get("depends_on") or [])
            if str(value).strip()
        }
        evidence_task_ids = dependency_task_ids | {task_id}
        task_evidence_refs = tuple(
            str(item.get("ref"))
            for item in (state.get("evidence_refs") or [])
            if isinstance(item, Mapping)
            and item.get("ref")
            and item.get("task_id") in evidence_task_ids
        )
        effective_timeout = (
            self.default_timeout_seconds
            if timeout_seconds is None
            else float(timeout_seconds)
        )
        context = MissionTaskContext(
            task_id=task_id,
            mission_id=mission_id,
            workspace_id=workspace_id,
            capability=capability,
            objective=str(objective)[:4000],
            inputs=self._bounded_input_mapping(inputs),
            evidence_refs=task_evidence_refs,
            correlation_id=correlation_id,
            attempt_id=idempotency_prefix,
            timeout_seconds=effective_timeout,
        )

        try:
            contribution = await asyncio.wait_for(
                self.adapters.invoke(engine_id, context),
                timeout=context.timeout_seconds,
            )
        except Exception:
            # An adapter exception/timeout does not prove that no external
            # side effect occurred. Preserve the durable executing claim so
            # recovery assessment classifies the attempt as outcome_unknown.
            # Never turn transport/process failure into a retryable task.failed.
            raise

        if contribution.status != "completed":
            latest = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
            event_type = "task.failed" if contribution.status == "failed" else "task.blocked"
            await self._commit(
                latest,
                event_type=event_type,
                idempotency_key=f"{idempotency_prefix}:{contribution.status}",
                actor_type="system" if event_type == "task.failed" else "engine",
                actor_id="director_orchestrator" if event_type == "task.failed" else engine_id,
                correlation_id=correlation_id,
                task_id=task_id,
                payload={
                    "reason": contribution.summary[:1000] or contribution.status,
                    **self._usage_metadata(contribution),
                },
            )
            return contribution

        refs = self._evidence_refs(contribution)
        if refs:
            current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
            current = await self._commit(
                current,
                event_type="engine.evidence.submitted",
                idempotency_key=f"{idempotency_prefix}:evidence",
                actor_type="engine",
                actor_id=engine_id,
                correlation_id=correlation_id,
                task_id=task_id,
                evidence_refs=refs,
                payload={"evidence_count": len(refs)},
            )

        current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        await self._commit(
            current,
            event_type="task.completed",
            idempotency_key=f"{idempotency_prefix}:completed",
            actor_type="engine",
            actor_id=engine_id,
            correlation_id=correlation_id,
            task_id=task_id,
            payload={
                "summary": contribution.summary,
                "findings": self._bounded_texts(contribution.findings),
                "recommendations": self._bounded_texts(contribution.recommendations),
                "limitations": self._bounded_texts(contribution.limitations),
                "confidence": contribution.confidence,
                "completed_at": contribution.finished_at.isoformat(),
                **self._usage_metadata(contribution),
            },
        )
        return contribution

    @staticmethod
    def _usage_metadata(contribution: EngineContribution) -> dict[str, int]:
        providers = contribution.source_metadata.get("providers")
        provider_calls = len(providers) if isinstance(providers, list) else 1
        return {
            "usage_tokens": MissionOrchestrationRuntime._usage_int(
                contribution.source_metadata.get("usage_tokens")
            ),
            "provider_calls": max(1, min(provider_calls, 12)),
        }

    async def verify_task(
        self,
        *,
        mission_id: str,
        workspace_id: str,
        task_id: str,
        verification_ref: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> MissionRuntimeSnapshot:
        current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        state = dict(current.state_snapshot or {})
        refs = [
            {"ref": str(item.get("ref"))}
            for item in (state.get("evidence_refs") or [])
            if isinstance(item, Mapping) and item.get("ref") and item.get("task_id") == task_id
        ]
        return await self._commit(
            current,
            event_type="task.verified",
            idempotency_key=idempotency_key,
            actor_type="director",
            actor_id="director",
            correlation_id=correlation_id,
            task_id=task_id,
            payload={"verification_ref": verification_ref},
            evidence_refs=refs,
        )

    async def ask_engine(
        self,
        *,
        mission_id: str,
        workspace_id: str,
        task_id: str,
        actor_engine_id: str,
        recipient_engine_id: str,
        question: str,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        round_number: int,
        context: str = "",
        recipient_capability: str | None = None,
        recipient_inputs: Mapping[str, Any] | None = None,
    ) -> MissionRuntimeSnapshot:
        if actor_engine_id == recipient_engine_id:
            raise MissionTransitionRejected("engine_cannot_question_itself")
        actor = self.adapters.get(actor_engine_id)
        recipient = self.adapters.get(recipient_engine_id)
        current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        assignment = (current.state_snapshot.get("task_assignments") or {}).get(task_id) or {}
        capability = str(assignment.get("capability") or "")
        if assignment.get("engine_id") != actor_engine_id:
            raise MissionTransitionRejected("question_actor_does_not_own_task")
        target_capability = str(recipient_capability or capability)
        if capability not in actor.capabilities or target_capability not in recipient.capabilities:
            raise MissionTransitionRejected("question_recipient_capability_unavailable")
        return await self._commit(
            current,
            event_type="engine.question.asked",
            idempotency_key=idempotency_key,
            actor_type="engine",
            actor_id=actor_engine_id,
            correlation_id=correlation_id,
            task_id=task_id,
            recipient_type="engine",
            recipient_id=recipient_engine_id,
            message_id=message_id,
            thread_id=f"task:{task_id}",
            round_number=round_number,
            payload={
                "question": question[:2000],
                "context": context[:4000],
                "recipient_capability": target_capability,
                "recipient_inputs": self._bounded_input_mapping(recipient_inputs),
            },
        )

    async def answer_engine_question(
        self,
        *,
        mission_id: str,
        workspace_id: str,
        question_message_id: str,
        answer_message_id: str,
        correlation_id: str,
        idempotency_prefix: str,
    ) -> EngineContribution:
        current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        pending = (current.state_snapshot.get("pending_questions") or {}).get(question_message_id)
        if not pending:
            raise MissionTaskNotReady("engine_question_not_pending")
        task_id = str(pending.get("task_id") or "")
        if not task_id:
            raise MissionTaskNotReady("engine_question_requires_task_scope")
        asker_id = str(pending.get("from_engine") or "")
        recipient_id = str(pending.get("to_engine") or "")
        assignment = (current.state_snapshot.get("task_assignments") or {}).get(task_id) or {}
        assigned_capability = str(assignment.get("capability") or "")
        recipient_capability = str(pending.get("recipient_capability") or assigned_capability)
        recipient = self.adapters.get(recipient_id)
        if recipient_capability not in recipient.capabilities:
            raise MissionTransitionRejected("answer_engine_capability_unavailable")

        events = await self.store.list_events(
            mission_id=mission_id,
            scope_type="workspace",
            workspace_id=workspace_id,
            limit=500,
        )
        question_event = next(
            (event for event in events if event.get("message_id") == question_message_id),
            None,
        )
        if not question_event:
            raise MissionTaskNotReady("persisted_engine_question_event_missing")
        question_payload = question_event.get("payload") or {}
        question_text = str(question_payload.get("question") or "").strip()
        if not question_text:
            raise MissionTaskNotReady("persisted_engine_question_text_missing")
        self.state_machine.ensure_budget_available(
            current,
            estimated_tokens=1800,
            estimated_provider_calls=1,
        )

        context = MissionTaskContext(
            task_id=task_id,
            mission_id=mission_id,
            workspace_id=workspace_id,
            capability=recipient_capability,
            objective=question_text,
            inputs={
                **self._bounded_input_mapping(question_payload.get("recipient_inputs")),
                "topic": question_text,
                "context": str(question_payload.get("context") or "")[:4000],
            },
            correlation_id=correlation_id,
            timeout_seconds=self.default_timeout_seconds,
        )
        contribution = await asyncio.wait_for(
            self.adapters.invoke(recipient_id, context),
            timeout=context.timeout_seconds,
        )
        refs = self._evidence_refs(contribution)
        if refs:
            current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
            await self._commit(
                current,
                event_type="engine.evidence.submitted",
                idempotency_key=f"{idempotency_prefix}:evidence",
                actor_type="engine",
                actor_id=recipient_id,
                correlation_id=correlation_id,
                task_id=task_id,
                evidence_refs=refs,
                payload={"evidence_count": len(refs), "in_reply_to": question_message_id},
            )

        current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        await self._commit(
            current,
            event_type="engine.answer.received",
            idempotency_key=f"{idempotency_prefix}:answer",
            actor_type="engine",
            actor_id=recipient_id,
            correlation_id=correlation_id,
            task_id=task_id,
            recipient_type="engine",
            recipient_id=asker_id,
            message_id=answer_message_id,
            thread_id=f"task:{task_id}",
            round_number=int(pending.get("round") or 0),
            payload={
                "in_reply_to": question_message_id,
                "answer": contribution.summary[:4000],
                "status": contribution.status,
                "findings": self._bounded_texts(contribution.findings),
                "recommendations": self._bounded_texts(contribution.recommendations),
                "evidence_refs": [item["ref"] for item in refs],
                **self._usage_metadata(contribution),
            },
        )
        return contribution

    async def resolve_disagreement(
        self,
        *,
        mission_id: str,
        workspace_id: str,
        disagreement_id: str,
        resolution: str,
        actor_id: str,
        actor_type: str = "director",
        idempotency_key: str,
        correlation_id: str,
    ) -> MissionRuntimeSnapshot:
        current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        return await self._commit(
            current,
            event_type="engine.disagreement.resolved",
            idempotency_key=idempotency_key,
            actor_type=actor_type,
            actor_id=actor_id,
            correlation_id=correlation_id,
            payload={"disagreement_id": disagreement_id, "resolution": resolution[:4000]},
        )

    async def complete_mission(
        self,
        *,
        mission_id: str,
        workspace_id: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> MissionRuntimeSnapshot:
        current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        return await self._commit(
            current,
            event_type="mission.completed",
            idempotency_key=idempotency_key,
            actor_type="director",
            actor_id="director",
            correlation_id=correlation_id,
        )

    async def timeline(
        self, *, mission_id: str, workspace_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        return await self.store.list_events(
            mission_id=mission_id,
            scope_type="workspace",
            workspace_id=workspace_id,
            limit=limit,
        )
