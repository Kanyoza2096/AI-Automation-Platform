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
    task_sync: Mapping[str, Any] | None = None


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
                      max_provider_calls: int | None = None,
                      max_reasoning_rounds: int = 3) -> dict[str, Any]:
        if not 1 <= int(max_reasoning_rounds) <= 10:
            raise ValueError("max_reasoning_rounds_out_of_range")
        return {
            "task_ids": list(task_ids),
            "task_status": {str(task_id): "approval_required" for task_id in task_ids},
            "task_assignments": {},
            "engine_progress": {},
            "task_results": {},
            "evidence_refs": [],
            "unresolved_disagreements": [],
            "pending_handoffs": {},
            "pending_questions": {},
            "active_reasoning_round": 0,
            "budget": {
                "max_rounds": int(max_reasoning_rounds),
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
    def _require_active(status: str) -> None:
        if status not in {"admitted", "queued", "running"}:
            raise MissionTransitionRejected("mission_not_accepting_work")

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

    def ensure_budget_available(
        self,
        current: MissionRuntimeSnapshot,
        *,
        estimated_tokens: int = 0,
        estimated_provider_calls: int = 1,
    ) -> None:
        """Preflight a provider call before dispatch; daily workspace limits still apply."""
        if isinstance(estimated_tokens, bool) or isinstance(estimated_provider_calls, bool) or estimated_tokens < 0 or estimated_provider_calls < 0:
            raise MissionTransitionRejected("provider_estimate_must_be_nonnegative")
        budget = dict((current.state_snapshot or {}).get("budget") or {})
        if budget.get("exhausted"):
            raise MissionTransitionRejected("mission_budget_exhausted")
        if current.status not in {"admitted", "queued", "running"}:
            raise MissionTransitionRejected("mission_not_accepting_provider_calls")
        max_tokens = budget.get("max_tokens")
        max_calls = budget.get("max_provider_calls")
        if max_tokens is not None and int(budget.get("tokens_used", 0)) + estimated_tokens > int(max_tokens):
            raise MissionTransitionRejected("mission_token_budget_would_be_exceeded")
        if max_calls is not None and int(budget.get("provider_calls", 0)) + estimated_provider_calls > int(max_calls):
            raise MissionTransitionRejected("mission_provider_call_budget_would_be_exceeded")

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
        state.setdefault("task_results", {})
        state.setdefault("evidence_refs", [])
        state.setdefault("unresolved_disagreements", [])
        state.setdefault("pending_handoffs", {})
        state.setdefault("pending_questions", {})
        state.setdefault("active_reasoning_round", 0)
        budget = state.setdefault("budget", {})
        budget.setdefault("max_rounds", self.max_reasoning_rounds)
        budget.setdefault("rounds_used", 0)
        budget.setdefault("tokens_used", 0)
        budget.setdefault("provider_calls", 0)
        budget.setdefault("max_tokens", None)
        budget.setdefault("max_provider_calls", None)
        budget.setdefault("exhausted", False)

        task_ids = set(str(value) for value in current.task_ids)
        state["task_ids"] = sorted(task_ids)
        task_status = {key: value for key, value in dict(state["task_status"]).items() if key in task_ids}
        for task_id in task_ids:
            task_status.setdefault(task_id, "approval_required")
        state["task_status"] = task_status
        assignments = {key: value for key, value in dict(state["task_assignments"]).items() if key in task_ids}
        state["task_assignments"] = assignments
        progress = {key: value for key, value in dict(state["engine_progress"]).items() if key in task_ids}
        state["engine_progress"] = progress
        task_results = {key: value for key, value in dict(state["task_results"]).items() if key in task_ids}
        state["task_results"] = task_results
        unresolved = state["unresolved_disagreements"]
        pending_handoffs = {key: value for key, value in dict(state["pending_handoffs"]).items() if key in task_ids}
        state["pending_handoffs"] = pending_handoffs
        for question in state["pending_questions"].values():
            question_task_id = question.get("task_id")
            if question_task_id is not None and question_task_id not in task_ids:
                raise MissionTransitionRejected("pending_question_task_scope_invalid")
        payload = dict(command.payload or {})
        next_status = current.status
        next_phase = current.phase

        event_type = command.event_type
        event_task_id = command.task_id

        if event_type == "task.assigned":
            self._require_active(current.status)

            task_id = command.task_id or self._require_text(payload, "task_id")
            engine_id = self._require_text(payload, "engine_id")
            capability = self._require_text(payload, "capability")
            if task_id not in task_ids:
                raise MissionTransitionRejected("task_not_in_mission")
            if task_id in assignments or task_status.get(task_id) != "approval_required":
                raise MissionTransitionRejected("task_already_assigned_or_not_approval_required")
            if command.actor_type not in {"director", "system"}:
                raise MissionTransitionRejected("task_assignment_requires_director_or_system")
            assignments[task_id] = {"engine_id": engine_id, "capability": capability}
            event_task_id = task_id
            next_phase = "delegation"

        elif event_type == "task.approved":
            self._require_active(current.status)
            if command.actor_type not in {"operator", "system"}:
                raise MissionTransitionRejected("task_approval_requires_governed_actor")
            task_id = command.task_id or self._require_text(payload, "task_id")
            if task_id not in assignments or task_status.get(task_id) != "approval_required":
                raise MissionTransitionRejected("task_not_awaiting_approval")
            task_status[task_id] = "approved"
            event_task_id = task_id
            next_phase = "delegation"

        elif event_type == "task.cancelled":
            if current.status not in {"admitted", "queued", "running", "awaiting_review"}:
                raise MissionTransitionRejected("mission_not_accepting_task_cancellation")
            task_id = command.task_id or self._require_text(payload, "task_id")
            if task_id not in task_ids or task_status.get(task_id) not in {"approval_required", "approved"}:
                raise MissionTransitionRejected("task_not_cancellable_from_current_state")
            if command.actor_type not in {"operator", "system"}:
                raise MissionTransitionRejected("task_cancellation_requires_operator_or_system")
            self._require_text(payload, "reason")
            task_status[task_id] = "cancelled"
            event_task_id = task_id
            next_phase = "review"
            if current.status in {"admitted", "running"}:
                next_status = "awaiting_review"

        elif event_type == "task.started":
            self._require_active(current.status)

            task_id = command.task_id or self._require_text(payload, "task_id")
            if task_id not in assignments or task_status.get(task_id) != "approved":
                raise MissionTransitionRejected("task_approval_required_before_start")
            if command.actor_type not in {"operator", "director", "system"}:
                raise MissionTransitionRejected("task_start_requires_governed_actor")
            assignment = assignments[task_id]
            attempt_id = payload.get("attempt_id")
            if (
                not isinstance(attempt_id, str)
                or not attempt_id.strip()
                or attempt_id != attempt_id.strip()
                or len(attempt_id) > 150
                or payload.get("engine_id") != assignment.get("engine_id")
                or payload.get("capability") != assignment.get("capability")
            ):
                raise MissionTransitionRejected("task_start_attempt_identity_mismatch")
            task_status[task_id] = "executing"
            event_task_id = task_id
            next_status, next_phase = "running", "execution"

        elif event_type == "task.progress":
            task_id = command.task_id or self._require_text(payload, "task_id")
            self._require_active(current.status)
            if task_id not in assignments or task_status.get(task_id) != "executing":
                raise MissionTransitionRejected("task_must_be_executing_before_progress")
            if command.actor_type == "engine" and assignments[task_id].get("engine_id") != command.actor_id:
                raise MissionTransitionRejected("task_progress_engine_assignment_mismatch")
            value = payload.get("progress")
            if not isinstance(value, (int, float)) or not 0 <= value <= 100:
                raise MissionTransitionRejected("task_progress_out_of_range")
            if command.actor_type not in {"engine", "director", "system"}:
                raise MissionTransitionRejected("task_progress_actor_invalid")
            progress[task_id] = float(value)
            event_task_id = task_id

        elif event_type in {"task.completed", "task.failed", "task.blocked"}:
            self._require_active(current.status)
            task_id = command.task_id or self._require_text(payload, "task_id")
            if task_id not in assignments:
                raise MissionTransitionRejected("task_must_be_assigned_before_result")
            if event_type in {"task.completed", "task.failed"} and task_status.get(task_id) != "executing":
                raise MissionTransitionRejected("task_must_be_executing_before_result")
            if event_type == "task.blocked" and task_status.get(task_id) not in {"approval_required", "approved", "executing"}:
                raise MissionTransitionRejected("task_cannot_be_blocked_from_current_state")
            if command.actor_type not in {"engine", "director", "system"}:
                raise MissionTransitionRejected("task_result_actor_invalid")
            if command.actor_type == "engine" and assignments[task_id].get("engine_id") != command.actor_id:
                raise MissionTransitionRejected("task_result_engine_assignment_mismatch")
            task_status[task_id] = {
                "task.completed": "completed",
                "task.failed": "failed",
                "task.blocked": "blocked",
            }[event_type]
            event_task_id = task_id
            if event_type == "task.completed":
                def _bounded_strings(key: str, limit: int = 12, width: int = 600) -> list[str]:
                    values = payload.get(key) or []
                    if not isinstance(values, list):
                        return []
                    return [str(value).strip()[:width] for value in values[:limit] if str(value).strip()]

                summary = str(payload.get("summary") or "").strip()
                if not summary:
                    raise MissionTransitionRejected("task_completion_summary_required")
                confidence = payload.get("confidence")
                if confidence is not None and (
                    isinstance(confidence, bool)
                    or not isinstance(confidence, (int, float))
                    or not 0.0 <= float(confidence) <= 1.0
                ):
                    raise MissionTransitionRejected("task_result_confidence_out_of_range")
                task_results[task_id] = {
                    "engine_id": assignments[task_id]["engine_id"],
                    "capability": assignments[task_id]["capability"],
                    "summary": summary[:500],
                    "findings": _bounded_strings("findings", limit=3, width=120),
                    "recommendations": _bounded_strings("recommendations", limit=2, width=120),
                    "limitations": _bounded_strings("limitations", limit=2, width=80),
                    "evidence_refs": [
                        str(item.get("ref"))[:80]
                        for item in state["evidence_refs"]
                        if isinstance(item, Mapping)
                        and item.get("ref")
                        and item.get("task_id") == task_id
                    ][:3],
                    "confidence": float(confidence) if confidence is not None else None,
                    "completed_at": str(payload.get("completed_at") or "")[:64],
                }
                next_phase = "synthesis"
            elif event_type in {"task.failed", "task.blocked"}:
                payload.setdefault("reason", "Task did not complete successfully.")
                task_results[task_id] = {
                    "engine_id": assignments[task_id]["engine_id"],
                    "capability": assignments[task_id]["capability"],
                    "status": task_status[task_id],
                    "reason": str(payload.get("reason") or "")[:1000],
                }

        elif event_type == "task.verified":
            if current.status not in {"admitted", "queued", "running", "awaiting_review"}:
                raise MissionTransitionRejected("mission_not_accepting_task_verification")
            task_id = command.task_id or self._require_text(payload, "task_id")
            if task_id not in task_ids or task_status.get(task_id) != "completed":
                raise MissionTransitionRejected("task_must_complete_before_verification")
            verification_ref = str(payload.get("verification_ref") or "").strip()
            if not command.evidence_refs and not verification_ref:
                raise MissionTransitionRejected("task_verification_evidence_required")
            if command.actor_type not in {"director", "system"}:
                raise MissionTransitionRejected("task_verification_requires_director_or_system")
            task_status[task_id] = "verified"
            event_task_id = task_id
            next_phase = "verification"

        elif event_type == "task.reassigned":
            self._require_active(current.status)
            if command.actor_type not in {"director", "system"}:
                raise MissionTransitionRejected("task_reassignment_requires_director_or_system")
            task_id = command.task_id or self._require_text(payload, "task_id")
            if task_id not in task_ids:
                raise MissionTransitionRejected("task_not_in_mission")
            if task_status.get(task_id) not in {"approval_required", "approved", "failed", "blocked"}:
                raise MissionTransitionRejected("task_not_reassignable_from_current_state")
            engine_id = self._require_text(payload, "engine_id")
            capability = self._require_text(payload, "capability")
            previous = assignments.get(task_id, {})
            attempt = int(previous.get("attempt", 0)) + 1
            assignments[task_id] = {"engine_id": engine_id, "capability": capability, "attempt": attempt}
            task_status[task_id] = "approval_required"
            event_task_id = task_id
            next_phase = "delegation"

        elif event_type == "task.handoff.requested":
            self._require_active(current.status)
            task_id = command.task_id or self._require_text(payload, "task_id")
            to_engine = self._require_text(payload, "to_engine")
            if command.actor_type not in {"engine", "director"}:
                raise MissionTransitionRejected("handoff_request_actor_invalid")
            if task_id not in assignments or task_status.get(task_id) != "executing":
                raise MissionTransitionRejected("task_must_be_executing_before_handoff")
            if command.actor_type == "engine" and assignments[task_id].get("engine_id") != command.actor_id:
                raise MissionTransitionRejected("handoff_request_engine_assignment_mismatch")
            if command.recipient_type != "engine" or command.recipient_id != to_engine:
                raise MissionTransitionRejected("handoff_recipient_mismatch")
            pending_handoffs[task_id] = to_engine
            event_task_id = task_id
            next_phase = "collaboration"

        elif event_type == "task.handoff.accepted":
            self._require_active(current.status)
            task_id = command.task_id or self._require_text(payload, "task_id")
            to_engine = pending_handoffs.get(task_id)
            if not to_engine:
                raise MissionTransitionRejected("handoff_not_pending")
            engine_id = str(payload.get("engine_id") or to_engine)
            if engine_id != to_engine or command.actor_type != "engine" or command.actor_id != to_engine:
                raise MissionTransitionRejected("handoff_recipient_mismatch")
            assignment = dict(assignments[task_id])
            assignment["engine_id"] = engine_id
            assignments[task_id] = assignment
            pending_handoffs.pop(task_id, None)
            task_status[task_id] = "approval_required"
            event_task_id = task_id
            next_phase = "delegation"

        elif event_type == "engine.question.asked":
            self._require_active(current.status)
            if command.actor_type != "engine":
                raise MissionTransitionRejected("engine_question_requires_engine_actor")
            if command.recipient_type != "engine":
                raise MissionTransitionRejected("engine_question_requires_engine_recipient")
            if not command.message_id:
                raise MissionTransitionRejected("engine_question_requires_message_id")
            round_number = int(command.round_number)
            max_rounds = int(budget.get("max_rounds", self.max_reasoning_rounds))
            if round_number < 1 or round_number > min(max_rounds, self.max_reasoning_rounds):
                raise MissionTransitionRejected("reasoning_round_limit_exceeded")
            if round_number > int(state["active_reasoning_round"]) + 1:
                raise MissionTransitionRejected("reasoning_round_sequence_invalid")
            question_task_id = command.task_id
            if question_task_id is not None:
                if question_task_id not in assignments:
                    raise MissionTransitionRejected("question_task_not_assigned")
                if assignments[question_task_id].get("engine_id") != command.actor_id:
                    raise MissionTransitionRejected("question_engine_assignment_mismatch")
            if command.message_id in state["pending_questions"]:
                raise MissionTransitionRejected("duplicate_question_message_id")
            active_round = int(state["active_reasoning_round"])
            if round_number > active_round:
                if any(int(item.get("round", 0)) == active_round for item in state["pending_questions"].values()):
                    raise MissionTransitionRejected("previous_reasoning_round_has_unanswered_questions")
                state["active_reasoning_round"] = round_number
                budget["rounds_used"] = max(int(budget["rounds_used"]), round_number)
            recipient_capability = str(
                payload.get("recipient_capability")
                or (assignments.get(question_task_id) or {}).get("capability")
                or ""
            ).strip()
            if not recipient_capability:
                raise MissionTransitionRejected("question_recipient_capability_required")
            state["pending_questions"][command.message_id] = {
                "from_engine": command.actor_id,
                "to_engine": command.recipient_id,
                "round": round_number,
                "task_id": question_task_id,
                "recipient_capability": recipient_capability,
            }
            event_task_id = question_task_id
            next_status, next_phase = "running", "collaboration"

        elif event_type == "engine.answer.received":
            self._require_active(current.status)
            if command.actor_type != "engine":
                raise MissionTransitionRejected("engine_answer_requires_engine_actor")
            if not command.message_id or command.round_number < 1:
                raise MissionTransitionRejected("engine_answer_message_and_round_required")
            in_reply_to = self._require_text(payload, "in_reply_to")
            question = state["pending_questions"].get(in_reply_to)
            if not question:
                raise MissionTransitionRejected("question_not_pending")
            if command.actor_id != question["to_engine"] or command.recipient_type != "engine" or command.recipient_id != question["from_engine"]:
                raise MissionTransitionRejected("answer_sender_or_recipient_mismatch")
            if command.round_number != int(question["round"]) or command.round_number != int(state["active_reasoning_round"]):
                raise MissionTransitionRejected("answer_round_not_open")
            state["pending_questions"].pop(in_reply_to, None)
            event_task_id = question.get("task_id")
            next_status, next_phase = "running", "collaboration"

        elif event_type == "engine.challenge.raised":
            self._require_active(current.status)
            if command.actor_type != "engine":
                raise MissionTransitionRejected("engine_challenge_requires_engine_actor")
            self._require_text(payload, "claim_id")
            self._require_text(payload, "challenge")
            if command.task_id is not None:
                if command.task_id not in assignments or assignments[command.task_id].get("engine_id") != command.actor_id:
                    raise MissionTransitionRejected("challenge_engine_assignment_mismatch")
            next_status, next_phase = "running", "collaboration"

        elif event_type == "engine.evidence.submitted":
            self._require_active(current.status)
            if command.actor_type != "engine":
                raise MissionTransitionRejected("evidence_submission_requires_engine_actor")
            if not command.evidence_refs:
                raise MissionTransitionRejected("evidence_references_required")
            if command.task_id and command.task_id not in task_ids:
                raise MissionTransitionRejected("evidence_task_not_in_mission")
            if command.task_id and command.actor_type == "engine":
                assignment = assignments.get(command.task_id) or {}
                assigned_engine = assignment.get("engine_id")
                is_pending_collaborator = any(
                    item.get("task_id") == command.task_id and item.get("to_engine") == command.actor_id
                    for item in state["pending_questions"].values()
                    if isinstance(item, Mapping)
                )
                if command.actor_id != assigned_engine and not is_pending_collaborator:
                    raise MissionTransitionRejected("evidence_engine_not_authorized_for_task")
            known = {
                (str(item.get("ref")), item.get("task_id"))
                for item in state["evidence_refs"] if isinstance(item, Mapping)
            }
            for item in command.evidence_refs:
                ref = str(item.get("ref") or "").strip()
                if not ref:
                    raise MissionTransitionRejected("evidence_reference_id_required")
                evidence_key = (ref, command.task_id)
                if evidence_key not in known:
                    if len(state["evidence_refs"]) >= 50:
                        raise MissionTransitionRejected("mission_evidence_reference_limit_exceeded")
                    state["evidence_refs"].append({
                        "ref": ref[:100],
                        "source_engine": command.actor_id[:128],
                        "task_id": command.task_id,
                    })
                    known.add(evidence_key)
            if command.task_id in task_results:
                task_results[command.task_id]["evidence_refs"] = [
                    str(item.get("ref"))[:80]
                    for item in state["evidence_refs"]
                    if isinstance(item, Mapping)
                    and item.get("ref")
                    and item.get("task_id") == command.task_id
                ][:3]
            event_task_id = command.task_id
            next_status, next_phase = "running", "collaboration"

        elif event_type == "engine.disagreement.detected":
            self._require_active(current.status)
            if command.actor_type not in {"engine", "director"}:
                raise MissionTransitionRejected("disagreement_detection_actor_invalid")
            disagreement_id = self._require_text(payload, "disagreement_id")
            self._require_text(payload, "summary")
            if disagreement_id not in unresolved:
                if len(unresolved) >= 25:
                    raise MissionTransitionRejected("mission_disagreement_limit_exceeded")
                unresolved.append(disagreement_id)
            event_task_id = command.task_id
            next_status, next_phase = "running", "collaboration"

        elif event_type == "engine.disagreement.resolved":
            if command.actor_type not in {"director", "operator"}:
                raise MissionTransitionRejected("disagreement_resolution_requires_director_or_operator")
            disagreement_id = self._require_text(payload, "disagreement_id")
            self._require_text(payload, "resolution")
            if disagreement_id not in unresolved:
                raise MissionTransitionRejected("disagreement_not_open")
            unresolved.remove(disagreement_id)
            event_task_id = command.task_id
            next_phase = "synthesis"

        elif event_type == "engine.call.completed":
            self._require_active(current.status)
            if command.actor_type not in {"system", "director"}:
                raise MissionTransitionRejected("provider_usage_requires_system_actor")
            usage = payload.get("usage_tokens", 0)
            calls = payload.get("provider_calls", 1)
            if isinstance(usage, bool) or not isinstance(usage, int) or usage < 0 or isinstance(calls, bool) or not isinstance(calls, int) or calls < 0:
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
                next_status, next_phase = "awaiting_review", "review"

        elif event_type == "mission.awaiting_review":
            if command.actor_type not in {"director", "system"} or current.status not in {"admitted", "running"}:
                raise MissionTransitionRejected("mission_review_transition_not_authorized")
            next_status, next_phase = "awaiting_review", "review"

        elif event_type == "mission.paused":
            if command.actor_type not in {"director", "operator", "system"}:
                raise MissionTransitionRejected("mission_pause_actor_invalid")
            next_status, next_phase = "paused", "recovery"

        elif event_type == "mission.resumed":
            if command.actor_type not in {"operator", "system"}:
                raise MissionTransitionRejected("mission_resume_requires_operator_or_system")
            if current.status not in {"paused", "needs_reconciliation", "awaiting_review"}:
                raise MissionTransitionRejected("mission_not_resumable")
            if budget.get("exhausted"):
                raise MissionTransitionRejected("mission_budget_exhausted")
            next_status, next_phase = "running", "delegation"

        elif event_type == "mission.completed":
            if command.actor_type not in {"director", "system"}:
                raise MissionTransitionRejected("mission_completion_requires_director_or_system")
            if not task_ids or any(task_status.get(task_id) != "verified" for task_id in task_ids):
                raise MissionTransitionRejected("mission_tasks_not_all_verified")
            if unresolved or pending_handoffs or state["pending_questions"]:
                raise MissionTransitionRejected("mission_has_unresolved_collaboration")
            next_status, next_phase = "completed", "completed"

        elif event_type == "mission.partial":
            if command.actor_type not in {"director", "system"}:
                raise MissionTransitionRejected("mission_partial_requires_director_or_system")
            self._require_text(payload, "reason")
            next_status, next_phase = "partial", "review"

        elif event_type == "mission.failed":
            if command.actor_type not in {"director", "system"}:
                raise MissionTransitionRejected("mission_failure_actor_invalid")
            self._require_text(payload, "reason")
            next_status, next_phase = "failed", "recovery"

        elif event_type == "mission.cancelled":
            if command.actor_type not in {"operator", "system"}:
                raise MissionTransitionRejected("mission_cancellation_requires_operator_or_system")
            self._require_text(payload, "reason")
            next_status, next_phase = "cancelled", "recovery"

        elif event_type == "mission.needs_reconciliation":
            if command.actor_type not in {"director", "system"}:
                raise MissionTransitionRejected("mission_reconciliation_actor_invalid")
            self._require_text(payload, "reason")
            next_status, next_phase = "needs_reconciliation", "recovery"

        else:
            raise MissionTransitionRejected(f"unsupported_mission_event:{event_type}")

        # Persist provider usage in the same durable event as the engine
        # result. A separate usage event could fail after task completion and
        # leave a completed task with an uncharged mission budget.
        usage_keys = {"usage_tokens", "provider_calls"}
        if usage_keys.intersection(payload) and event_type != "engine.call.completed":
            if event_type not in {
                "task.completed", "task.failed", "task.blocked", "engine.answer.received"
            }:
                raise MissionTransitionRejected("provider_usage_event_type_invalid")
            if not usage_keys.issubset(payload):
                raise MissionTransitionRejected("provider_usage_contract_incomplete")
            usage = payload["usage_tokens"]
            calls = payload["provider_calls"]
            if (
                isinstance(usage, bool) or not isinstance(usage, int) or usage < 0
                or isinstance(calls, bool) or not isinstance(calls, int) or not 1 <= calls <= 12
            ):
                raise MissionTransitionRejected("provider_usage_invalid")
            budget["tokens_used"] = int(budget["tokens_used"]) + usage
            budget["provider_calls"] = int(budget["provider_calls"]) + calls
            max_tokens = budget.get("max_tokens")
            max_calls = budget.get("max_provider_calls")
            if (
                (max_tokens is not None and budget["tokens_used"] >= int(max_tokens))
                or (max_calls is not None and budget["provider_calls"] >= int(max_calls))
            ):
                budget["exhausted"] = True
                budget["exhaustion_reason"] = "mission_resource_ceiling_reached"
                next_status, next_phase = "awaiting_review", "review"

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
            "task_id": event_task_id,
            "message_id": command.message_id,
            "thread_id": command.thread_id,
            "round_number": command.round_number,
            "correlation_id": command.correlation_id,
            "payload": payload,
            "evidence_refs": list(command.evidence_refs),
            "task_sync": dict(command.task_sync) if command.task_sync else None,
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
            task_id=event_task_id,
            message_id=command.message_id,
            thread_id=command.thread_id,
            round_number=command.round_number,
            correlation_id=command.correlation_id,
            payload=payload,
            evidence_refs=tuple(command.evidence_refs),
            status=next_status,
            phase=next_phase,
            state_snapshot=state,
            task_sync=dict(command.task_sync) if command.task_sync else None,
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
