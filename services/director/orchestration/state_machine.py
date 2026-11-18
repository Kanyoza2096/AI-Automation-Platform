"""Deterministic Director mission state transitions.

LLMs may propose work and contribute evidence; this module owns bounded,
validated state transitions. It contains no model calls or execution tools.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .store import MissionEventWrite, MissionRuntimeStore


TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled"}
MISSION_STATUSES = {
    "admitted", "queued", "running", "awaiting_review", "paused",
    "completed", "partial", "failed", "cancelled", "needs_reconciliation",
}
MISSION_PHASES = {
    "admission", "decomposition", "delegation", "collaboration", "synthesis",
    "review", "execution", "verification", "recovery", "completed",
}


class MissionTransitionRejected(ValueError):
    pass


class MissionRuntimeConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class MissionRuntimeSnapshot:
    mission_id: str
    scope_type: str
    workspace_id: str | None
    version: int
    status: str
    phase: str
    task_ids: Sequence[str] = field(default_factory=tuple)
    state_snapshot: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "MissionRuntimeSnapshot":
        return cls(
            mission_id=str(row["mission_id"]),
            scope_type=str(row["scope_type"]),
            workspace_id=row.get("workspace_id"),
            version=int(row.get("version") or 0),
            status=str(row["status"]),
            phase=str(row["phase"]),
            task_ids=tuple(str(value) for value in (row.get("task_ids") or [])),
            state_snapshot=copy.deepcopy(row.get("state_snapshot") or {}),
        )


@dataclass(frozen=True)
class MissionEventCommand:
    event_type: str
    idempotency_key: str
    actor_type: str
    actor_id: str
    correlation_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    recipient_type: str | None = None
    recipient_id: str | None = None
    task_id: str | None = None
    message_id: str | None = None
    thread_id: str | None = None
    round_number: int = 0
    evidence_refs: Sequence[Mapping[str, Any]] = field(default_factory=tuple)


@dataclass(frozen=True)
class MissionTransition:
    event: MissionEventWrite
    next_snapshot: MissionRuntimeSnapshot


class MissionStateMachine:
    """Pure transition validator and durable event command builder."""

    def __init__(self, *, max_reasoning_rounds: int = 3) -> None:
        if not 1 <= int(max_reasoning_rounds) <= 10:
            raise ValueError("max_reasoning_rounds_out_of_range")
        self.max_reasoning_rounds = int(max_reasoning_rounds)

    @staticmethod
    def initial_state(task_ids: Sequence[str], *, max_tokens: int | None = None,
                      max_provider_calls: int | None = None) -> dict[str, Any]:
        return {
            "task_ids": list(task_ids),
            "task_status": {},
            "task_assignments": {},
            "engine_progress": {},
            "evidence_refs": [],
            "unresolved_disagreements": [],
            "pending_handoffs": {},
            "active_reasoning_round": 0,
            "budget": {
                "max_rounds": 3,
                "rounds_used": 0,
                "tokens_used": 0,
                "provider_calls": 0,
                "max_tokens": max_tokens,
                "max_provider_calls": max_provider_calls,
                "exhausted": False,
            },
        }

    @staticmethod
    def _require_text(payload: Mapping[str, Any], key: str) -> str:
        value = str(payload.get(key) or "").strip()
        if not value:
            raise MissionTransitionRejected(f"{key}_required")
        return value

    @staticmethod
    def _status_transition_allowed(current: str, target: str) -> bool:
        if current == target:
            return True
        allowed = {
            "admitted": {"queued", "running", "awaiting_review", "paused", "failed", "cancelled", "needs_reconciliation"},
            "queued": {"running", "paused", "failed", "cancelled", "needs_reconciliation"},
            "running": {"awaiting_review", "paused", "completed", "partial", "failed", "cancelled", "needs_reconciliation"},
            "awaiting_review": {"queued", "running", "paused", "completed", "partial", "failed", "cancelled", "needs_reconciliation"},
            "paused": {"queued", "running", "failed", "cancelled", "needs_reconciliation"},
            "needs_reconciliation": {"queued", "running", "paused", "completed", "partial", "failed", "cancelled"},
        }
        return target in allowed.get(current, set())

    def prepare(
        self, current: MissionRuntimeSnapshot, command: MissionEventCommand
    ) -> MissionTransition:
        if current.status not in MISSION_STATUSES or current.phase not in MISSION_PHASES:
            raise MissionTransitionRejected("current_mission_state_invalid")
        if current.status in TERMINAL_STATUSES:
            raise MissionTransitionRejected("mission_is_terminal")
        if not command.event_type or not command.idempotency_key or not command.correlation_id:
            raise MissionTransitionRejected("event_identity_required")
        if command.actor_type not in {"director", "engine", "operator", "system"}:
            raise MissionTransitionRejected("invalid_event_actor")
        if command.recipient_type is None and command.recipient_id is not None:
            raise MissionTransitionRejected("recipient_id_requires_recipient_type")
        if command.recipient_type is not None and not command.recipient_id:
            raise MissionTransitionRejected("recipient_type_requires_recipient_id")

        state = copy.deepcopy(dict(current.state_snapshot or {}))
        state.setdefault("task_ids", list(current.task_ids))
        state.setdefault("task_status", {})
        state.setdefault("task_assignments", {})
        state.setdefault("engine_progress", {})
        state.setdefault("evidence_refs", [])
        state.setdefault("unresolved_disagreements", [])
        state.setdefault("pending_handoffs", {})
        state.setdefault("active_reasoning_round", 0)
        budget = state.setdefault("budget", {})
        budget.setdefault("max_rounds", self.max_reasoning_rounds)
        budget.setdefault("rounds_used", 0)
        budget.setdefault("tokens_used", 0)
        budget.setdefault("provider_calls", 0)
        budget.setdefault("max_tokens", None)
        budget.setdefault("max_provider_calls", None)
        budget.setdefault("exhausted", False)

        task_ids = set(str(value) for value in (state.get("task_ids") or current.task_ids))
        task_status = state["task_status"]
        assignments = state["task_assignments"]
        progress = state["engine_progress"]
        unresolved = state["unresolved_disagreements"]
        pending_handoffs = state["pending_handoffs"]
        payload = dict(command.payload or {})
        next_status = current.status
        next_phase = current.phase

        event_type = command.event_type

        if event_type == "task.assigned":
            task_id = command.task_id or self._require_text(payload, "task_id")
            engine_id = self._require_text(payload, "engine_id")
            capability = self._require_text(payload, "capability")
            if task_id not in task_ids:
                raise MissionTransitionRejected("task_not_in_mission")
            assignments[task_id] = {"engine_id": engine_id, "capability": capability}
            task_status[task_id] = "assigned"
            next_phase = "delegation"

        elif event_type == "task.started":
            task_id = command.task_id or self._require_text(payload, "task_id")
            if task_id not in assignments:
                raise MissionTransitionRejected("task_must_be_assigned_before_start")
            task_status[task_id] = "running"
            next_status, next_phase = "running", "execution"

        elif event_type == "task.progress":
            task_id = command.task_id or self._require_text(payload, "task_id")
            if task_id not in assignments:
                raise MissionTransitionRejected("task_must_be_assigned_before_progress")
            value = payload.get("progress")
            if not isinstance(value, (int, float)) or not 0 <= value <= 100:
                raise MissionTransitionRejected("task_progress_out_of_range")
            progress[task_id] = float(value)

        elif event_type in {"task.completed", "task.failed", "task.blocked"}:
            task_id = command.task_id or self._require_text(payload, "task_id")
            if task_id not in assignments:
                raise MissionTransitionRejected("task_must_be_assigned_before_terminal_result")
            task_status[task_id] = {
                "task.completed": "completed",
                "task.failed": "failed",
                "task.blocked": "blocked",
            }[event_type]
            if event_type == "task.completed":
                next_phase = "synthesis"
            elif event_type in {"task.failed", "task.blocked"}:
                payload.setdefault("reason", "Task did not complete successfully.")

        elif event_type == "task.reassigned":
            task_id = command.task_id or self._require_text(payload, "task_id")
            if task_id not in task_ids:
                raise MissionTransitionRejected("task_not_in_mission")
            engine_id = self._require_text(payload, "engine_id")
            capability = self._require_text(payload, "capability")
            previous = assignments.get(task_id, {})
            attempt = int(previous.get("attempt", 0)) + 1
            assignments[task_id] = {"engine_id": engine_id, "capability": capability, "attempt": attempt}
            task_status[task_id] = "assigned"
            next_phase = "delegation"

        elif event_type == "task.handoff.requested":
            task_id = command.task_id or self._require_text(payload, "task_id")
            to_engine = self._require_text(payload, "to_engine")
            if task_id not in assignments:
                raise MissionTransitionRejected("task_must_be_assigned_before_handoff")
            pending_handoffs[task_id] = to_engine
            next_phase = "collaboration"

        elif event_type == "task.handoff.accepted":
            task_id = command.task_id or self._require_text(payload, "task_id")
            to_engine = pending_handoffs.get(task_id)
            if not to_engine:
                raise MissionTransitionRejected("handoff_not_pending")
            engine_id = str(payload.get("engine_id") or to_engine)
            if engine_id != to_engine:
                raise MissionTransitionRejected("handoff_recipient_mismatch")
            assignment = dict(assignments[task_id])
            assignment["engine_id"] = engine_id
            assignments[task_id] = assignment
            pending_handoffs.pop(task_id, None)
            task_status[task_id] = "assigned"
            next_phase = "delegation"

        elif event_type == "engine.question.asked":
            if command.recipient_type != "engine":
                raise MissionTransitionRejected("engine_question_requires_engine_recipient")
            if not command.message_id:
                raise MissionTransitionRejected("engine_question_requires_message_id")
            round_number = int(command.round_number)
            max_rounds = int(budget.get("max_rounds") or self.max_reasoning_rounds)
            if round_number < 1 or round_number > min(max_rounds, self.max_reasoning_rounds):
                raise MissionTransitionRejected("reasoning_round_limit_exceeded")
            if round_number > int(state["active_reasoning_round"]):
                state["active_reasoning_round"] = round_number
                budget["rounds_used"] = max(int(budget["rounds_used"]), round_number)
            next_status, next_phase = "running", "collaboration"

        elif event_type == "engine.answer.received":
            if not command.message_id or command.round_number < 1:
                raise MissionTransitionRejected("engine_answer_message_and_round_required")
            if command.round_number > int(state["active_reasoning_round"]):
                raise MissionTransitionRejected("answer_round_not_open")
            next_status, next_phase = "running", "collaboration"

        elif event_type == "engine.challenge.raised":
            self._require_text(payload, "claim_id")
            self._require_text(payload, "challenge")
            next_status, next_phase = "running", "collaboration"

        elif event_type == "engine.evidence.submitted":
            if not command.evidence_refs:
                raise MissionTransitionRejected("evidence_references_required")
            known = {str(item.get("ref")) for item in state["evidence_refs"] if isinstance(item, Mapping)}
            for item in command.evidence_refs:
                ref = str(item.get("ref") or "").strip()
                if not ref:
                    raise MissionTransitionRejected("evidence_reference_id_required")
                if ref not in known:
                    state["evidence_refs"].append({"ref": ref, "source_engine": command.actor_id})
                    known.add(ref)
            next_status, next_phase = "running", "collaboration"

        elif event_type == "engine.disagreement.detected":
            disagreement_id = self._require_text(payload, "disagreement_id")
            self._require_text(payload, "summary")
            if disagreement_id not in unresolved:
                unresolved.append(disagreement_id)
            next_status, next_phase = "running", "collaboration"

        elif event_type == "engine.disagreement.resolved":
            disagreement_id = self._require_text(payload, "disagreement_id")
            self._require_text(payload, "resolution")
            if disagreement_id not in unresolved:
                raise MissionTransitionRejected("disagreement_not_open")
            unresolved.remove(disagreement_id)
            next_phase = "synthesis"

        elif event_type == "engine.call.completed":
            usage = payload.get("usage_tokens", 0)
            calls = payload.get("provider_calls", 1)
            if not isinstance(usage, int) or usage < 0 or not isinstance(calls, int) or calls < 0:
                raise MissionTransitionRejected("provider_usage_invalid")
            budget["tokens_used"] = int(budget["tokens_used"]) + usage
            budget["provider_calls"] = int(budget["provider_calls"]) + calls
            max_tokens = budget.get("max_tokens")
            max_calls = budget.get("max_provider_calls")
            exhausted = (
                (max_tokens is not None and budget["tokens_used"] >= int(max_tokens))
                or (max_calls is not None and budget["provider_calls"] >= int(max_calls))
            )
            if exhausted:
                budget["exhausted"] = True
                budget["exhaustion_reason"] = "mission_resource_ceiling_reached"
                next_status, next_phase = "paused", "recovery"

        elif event_type == "mission.awaiting_review":
            next_status, next_phase = "awaiting_review", "review"

        elif event_type == "mission.paused":
            next_status, next_phase = "paused", "recovery"

        elif event_type == "mission.resumed":
            if current.status not in {"paused", "needs_reconciliation"}:
                raise MissionTransitionRejected("mission_not_resumable")
            if budget.get("exhausted"):
                raise MissionTransitionRejected("mission_budget_exhausted")
            next_status, next_phase = "running", "delegation"

        elif event_type == "mission.completed":
            if any(task_status.get(task_id) != "completed" for task_id in task_ids):
                raise MissionTransitionRejected("mission_tasks_not_all_completed")
            if unresolved or pending_handoffs:
                raise MissionTransitionRejected("mission_has_unresolved_collaboration")
            next_status, next_phase = "completed", "completed"

        elif event_type == "mission.partial":
            self._require_text(payload, "reason")
            next_status, next_phase = "partial", "review"

        elif event_type == "mission.failed":
            self._require_text(payload, "reason")
            next_status, next_phase = "failed", "recovery"

        elif event_type == "mission.cancelled":
            self._require_text(payload, "reason")
            next_status, next_phase = "cancelled", "recovery"

        elif event_type == "mission.needs_reconciliation":
            self._require_text(payload, "reason")
            next_status, next_phase = "needs_reconciliation", "recovery"

        else:
            raise MissionTransitionRejected(f"unsupported_mission_event:{event_type}")

        if next_status not in MISSION_STATUSES or next_phase not in MISSION_PHASES:
            raise MissionTransitionRejected("mission_transition_target_invalid")
        if not self._status_transition_allowed(current.status, next_status):
            raise MissionTransitionRejected(f"mission_status_transition_invalid:{current.status}->{next_status}")

        canonical = {
            "mission_id": current.mission_id,
            "scope_type": current.scope_type,
            "workspace_id": current.workspace_id,
            "expected_version": current.version,
            "event_type": command.event_type,
            "idempotency_key": command.idempotency_key,
            "actor_type": command.actor_type,
            "actor_id": command.actor_id,
            "recipient_type": command.recipient_type,
            "recipient_id": command.recipient_id,
            "task_id": command.task_id,
            "message_id": command.message_id,
            "thread_id": command.thread_id,
            "round_number": command.round_number,
            "correlation_id": command.correlation_id,
            "payload": payload,
            "evidence_refs": list(command.evidence_refs),
            "status": next_status,
            "phase": next_phase,
            "state_snapshot": state,
        }
        fingerprint = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()

        event = MissionEventWrite(
            scope_type=current.scope_type,
            workspace_id=current.workspace_id,
            mission_id=current.mission_id,
            expected_version=current.version,
            idempotency_key=command.idempotency_key,
            request_fingerprint=fingerprint,
            event_type=command.event_type,
            actor_type=command.actor_type,
            actor_id=command.actor_id,
            recipient_type=command.recipient_type,
            recipient_id=command.recipient_id,
            task_id=command.task_id,
            message_id=command.message_id,
            thread_id=command.thread_id,
            round_number=command.round_number,
            correlation_id=command.correlation_id,
            payload=payload,
            evidence_refs=tuple(command.evidence_refs),
            status=next_status,
            phase=next_phase,
            state_snapshot=state,
        )
        next_snapshot = MissionRuntimeSnapshot(
            mission_id=current.mission_id,
            scope_type=current.scope_type,
            workspace_id=current.workspace_id,
            version=current.version + 1,
            status=next_status,
            phase=next_phase,
            task_ids=tuple(current.task_ids),
            state_snapshot=state,
        )
        return MissionTransition(event=event, next_snapshot=next_snapshot)

    async def commit(
        self,
        store: MissionRuntimeStore,
        current: MissionRuntimeSnapshot,
        command: MissionEventCommand,
    ) -> MissionRuntimeSnapshot:
        transition = self.prepare(current, command)
        result = await store.append_event(transition.event)
        if not result:
            raise MissionRuntimeConflict("mission_event_write_not_acknowledged")
        if result.get("error"):
            raise MissionRuntimeConflict(str(result["error"]))
        if result.get("replayed"):
            row = await store.get_mission(
                mission_id=current.mission_id,
                scope_type=current.scope_type,
                workspace_id=current.workspace_id,
            )
            if not row:
                raise MissionRuntimeConflict("mission_state_missing_after_event_replay")
            return MissionRuntimeSnapshot.from_row(row)
        if int(result.get("version", -1)) != transition.next_snapshot.version:
            raise MissionRuntimeConflict("mission_version_acknowledgement_mismatch")
        return transition.next_snapshot
