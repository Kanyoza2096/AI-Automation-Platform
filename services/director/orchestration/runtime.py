"""Durable Director orchestration runtime over native engine adapters.

This layer coordinates only advisory/read-only native work. It never invokes
KCPG commands, Action Kernel execution, publishing, or other external actions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from .contracts import AdapterRegistry, EngineContribution, MissionTaskContext
from .state_machine import (
    MissionEventCommand,
    MissionRuntimeSnapshot,
    MissionStateMachine,
    MissionTransitionRejected,
)
from .store import MissionRuntimeStore


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
        sync_event_types = {
            "task.started", "task.progress", "task.completed", "task.failed",
            "task.blocked", "task.verified", "task.reassigned", "task.handoff.accepted",
        }
        if task_id and event_type in sync_event_types and self.task_state_callback is None:
            raise MissionTaskNotReady("runtime_task_state_callback_required")
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
            ),
        )
        sync_rules = {
            "task.started": ("executing", {"approved"}),
            "task.progress": ("executing", {"executing"}),
            "task.completed": ("completed", {"executing"}),
            "task.failed": ("failed", {"executing"}),
            "task.blocked": ("blocked", {"approval_required", "approved", "executing"}),
            "task.verified": ("verified", {"completed"}),
            "task.reassigned": ("approval_required", {"failed", "blocked", "flagged"}),
            "task.handoff.accepted": ("approval_required", {"executing"}),
        }
        rule = sync_rules.get(event_type)
        if task_id and rule:
            target_status, expected_statuses = rule
            result = None
            error = None
            if event_type == "task.completed":
                result = {
                    key: event_payload.get(key)
                    for key in ("summary", "findings", "recommendations", "limitations", "confidence")
                    if key in event_payload
                }
            elif event_type in {"task.failed", "task.blocked"}:
                error = str(event_payload.get("reason") or event_type)[:2000]
            await self.store.sync_workspace_task_status(
                task_id=task_id,
                workspace_id=str(current.workspace_id),
                mission_id=current.mission_id,
                target_status=target_status,
                expected_statuses=tuple(expected_statuses),
                result=result,
                error=error,
            )
            self.task_state_callback(task_id, target_status, result, error)
        return snapshot

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
        idempotency_key: str,
        correlation_id: str,
    ) -> MissionRuntimeSnapshot:
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
        return await self._commit(
            current,
            event_type="task.approved",
            idempotency_key=idempotency_key,
            actor_type="operator",
            actor_id=operator_id,
            correlation_id=correlation_id,
            task_id=task_id,
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
    def _usage_int(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        try:
            number = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, number)

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

        current = await self._commit(
            current,
            event_type="task.started",
            idempotency_key=f"{idempotency_prefix}:started",
            actor_type="director",
            actor_id="director",
            correlation_id=correlation_id,
            task_id=task_id,
        )
        context = MissionTaskContext(
            task_id=task_id,
            mission_id=mission_id,
            workspace_id=workspace_id,
            capability=capability,
            objective=objective,
            inputs=dict(inputs),
            evidence_refs=tuple(
                str(item.get("ref")) for item in (state.get("evidence_refs") or [])
                if isinstance(item, Mapping) and item.get("ref")
            ),
            correlation_id=correlation_id,
            timeout_seconds=timeout_seconds or self.default_timeout_seconds,
        )

        try:
            contribution = await self.adapters.invoke(engine_id, context)
        except Exception as exc:
            latest = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
            if (latest.state_snapshot.get("task_status") or {}).get(task_id) == "executing":
                await self._commit(
                    latest,
                    event_type="task.failed",
                    idempotency_key=f"{idempotency_prefix}:failed",
                    actor_type="system",
                    actor_id="director_orchestrator",
                    correlation_id=correlation_id,
                    task_id=task_id,
                    payload={
                        "reason": "native_engine_invocation_failed",
                        "error_type": type(exc).__name__[:120],
                    },
                )
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
                payload={"reason": contribution.summary[:1000] or contribution.status},
            )
            await self._record_engine_usage(
                mission_id=mission_id,
                workspace_id=workspace_id,
                contribution=contribution,
                idempotency_key=f"{idempotency_prefix}:usage",
                correlation_id=correlation_id,
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
                "findings": list(contribution.findings),
                "recommendations": list(contribution.recommendations),
                "limitations": list(contribution.limitations),
                "confidence": contribution.confidence,
                "completed_at": contribution.finished_at.isoformat(),
            },
        )
        await self._record_engine_usage(
            mission_id=mission_id,
            workspace_id=workspace_id,
            contribution=contribution,
            idempotency_key=f"{idempotency_prefix}:usage",
            correlation_id=correlation_id,
        )
        return contribution

    async def _record_engine_usage(
        self,
        *,
        mission_id: str,
        workspace_id: str,
        contribution: EngineContribution,
        idempotency_key: str,
        correlation_id: str,
    ) -> MissionRuntimeSnapshot:
        providers = contribution.source_metadata.get("providers")
        provider_calls = len(providers) if isinstance(providers, list) else 1
        provider_calls = max(1, min(provider_calls, 12))
        usage_tokens = self._usage_int(contribution.source_metadata.get("usage_tokens"))
        current = await self.load_snapshot(mission_id=mission_id, workspace_id=workspace_id)
        return await self._commit(
            current,
            event_type="engine.call.completed",
            idempotency_key=idempotency_key,
            actor_type="system",
            actor_id="director_orchestrator",
            correlation_id=correlation_id,
            payload={"usage_tokens": usage_tokens, "provider_calls": provider_calls},
        )

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
        if capability not in actor.capabilities or capability not in recipient.capabilities:
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
            payload={"question": question[:2000], "context": context[:4000]},
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
        capability = str(assignment.get("capability") or "")
        recipient = self.adapters.get(recipient_id)
        if capability not in recipient.capabilities:
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

        context = MissionTaskContext(
            task_id=task_id,
            mission_id=mission_id,
            workspace_id=workspace_id,
            capability=capability,
            objective=question_text,
            inputs={
                "topic": question_text,
                "context": str(question_payload.get("context") or "")[:4000],
            },
            correlation_id=correlation_id,
            timeout_seconds=self.default_timeout_seconds,
        )
        contribution = await self.adapters.invoke(recipient_id, context)
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
                "findings": list(contribution.findings),
                "recommendations": list(contribution.recommendations),
                "evidence_refs": [item["ref"] for item in refs],
            },
        )
        await self._record_engine_usage(
            mission_id=mission_id,
            workspace_id=workspace_id,
            contribution=contribution,
            idempotency_key=f"{idempotency_prefix}:usage",
            correlation_id=correlation_id,
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
