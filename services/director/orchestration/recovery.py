"""Read-only, fail-closed assessment of Director native execution recovery candidates."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class RecoveryAssessment:
    classification: str
    mission_id: str
    workspace_id: str
    task_id: str
    attempt_key: str | None
    started_at: str | None
    reason: str
    evidence_event_ids: tuple[str, ...] = ()


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def assess_execution_recovery(
    *,
    task: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    mission_id: str,
    workspace_id: str,
    assigned_engine_id: str | None = None,
    now: datetime,
    stale_after: timedelta,
) -> RecoveryAssessment:
    """Classify durable evidence without mutating task or mission state.

    Missing, malformed, cross-scope, or contradictory evidence always fails closed
    to outcome_unknown. A timeout or elapsed lease is never proof of no effects.
    """
    task_id = str(task.get("id") or "").strip()
    payload = task.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}
    status = str(task.get("status") or "")
    if (
        not task_id
        or task.get("scope_type") != "workspace"
        or task.get("workspace_id") != workspace_id
        or payload.get("scope_type") != "workspace"
        or payload.get("workspace_id") != workspace_id
        or payload.get("mission_id") != mission_id
        or payload.get("status") != status
        or stale_after.total_seconds() <= 0
        or now.tzinfo is None
    ):
        return RecoveryAssessment(
            "outcome_unknown", mission_id, workspace_id, task_id, None, None,
            "canonical_task_identity_or_assessment_parameters_invalid",
        )

    scoped_events = [
        event for event in events
        if event.get("task_id") == task_id
        and event.get("mission_id") == mission_id
        and event.get("scope_type") == "workspace"
        and event.get("workspace_id") == workspace_id
    ]
    starts = [event for event in scoped_events if event.get("event_type") == "task.started"]

    if status == "approved":
        if starts or any(event.get("event_type") in {"task.completed", "task.failed", "task.blocked"} for event in scoped_events):
            return RecoveryAssessment(
                "outcome_unknown", mission_id, workspace_id, task_id, None, None,
                "approved_task_has_durable_start_event",
                tuple(str(item.get("event_id") or "") for item in starts),
            )
        return RecoveryAssessment(
            "not_started", mission_id, workspace_id, task_id, None, None,
            "canonical_task_is_approved_and_no_start_event_exists",
        )

    if len(starts) != 1:
        return RecoveryAssessment(
            "outcome_unknown", mission_id, workspace_id, task_id, None, None,
            "execution_start_event_missing_or_duplicated",
            tuple(str(item.get("event_id") or "") for item in starts),
        )

    start = starts[0]
    started_at = _parse_timestamp(start.get("created_at"))
    start_key = str(start.get("idempotency_key") or "")
    if (
        not started_at
        or not start_key.endswith(":started")
        or not str(start.get("correlation_id") or "").strip()
        or start.get("actor_type") not in {"operator", "director"}
        or not str(start.get("actor_id") or "").strip()
    ):
        return RecoveryAssessment(
            "outcome_unknown", mission_id, workspace_id, task_id, None,
            str(start.get("created_at") or "") or None,
            "execution_start_timestamp_or_attempt_identity_invalid",
            (str(start.get("event_id") or ""),),
        )

    attempt_key = start_key[:-len(":started")]
    if not attempt_key:
        return RecoveryAssessment(
            "outcome_unknown", mission_id, workspace_id, task_id, None,
            started_at.isoformat(), "execution_attempt_identity_empty",
        )

    terminal = [
        event for event in scoped_events
        if event.get("idempotency_key") in {
            f"{attempt_key}:completed", f"{attempt_key}:failed", f"{attempt_key}:blocked"
        }
        and event.get("correlation_id") == start.get("correlation_id")
    ]
    if len(terminal) > 1:
        return RecoveryAssessment(
            "outcome_unknown", mission_id, workspace_id, task_id, attempt_key,
            started_at.isoformat(), "multiple_terminal_events_for_one_attempt",
            tuple(str(item.get("event_id") or "") for item in terminal),
        )

    if status == "executing" and terminal:
        return RecoveryAssessment(
            "outcome_unknown", mission_id, workspace_id, task_id, attempt_key,
            started_at.isoformat(), "terminal_event_conflicts_with_executing_canonical_task",
            (str(start.get("event_id") or ""),) + tuple(str(item.get("event_id") or "") for item in terminal),
        )

    if status == "completed":
        if (
            len(terminal) == 1
            and terminal[0].get("event_type") == "task.completed"
            and assigned_engine_id
            and terminal[0].get("actor_type") == "engine"
            and terminal[0].get("actor_id") == assigned_engine_id
            and isinstance(terminal[0].get("payload"), Mapping)
            and bool(str(terminal[0]["payload"].get("summary") or "").strip())
            and bool(str(terminal[0]["payload"].get("completed_at") or "").strip())
        ):
            return RecoveryAssessment(
                "completed_response_lost", mission_id, workspace_id, task_id,
                attempt_key, started_at.isoformat(),
                "durable_completion_exists_reconcile_existing_result_without_reexecution",
                (str(start.get("event_id") or ""), str(terminal[0].get("event_id") or "")),
            )
        return RecoveryAssessment(
            "outcome_unknown", mission_id, workspace_id, task_id, attempt_key,
            started_at.isoformat(), "canonical_completed_status_lacks_matching_completion_event",
        )

    if status == "failed":
        if (
            len(terminal) == 1
            and terminal[0].get("event_type") == "task.failed"
            and isinstance(terminal[0].get("payload"), Mapping)
            and terminal[0]["payload"].get("no_side_effects_confirmed") is True
            and bool(str(terminal[0]["payload"].get("no_side_effects_evidence_ref") or "").strip())
            and terminal[0].get("actor_type") == "system"
            and terminal[0].get("actor_id") == "director_recovery_reconciler"
        ):
            return RecoveryAssessment(
                "failed_reconsiderable", mission_id, workspace_id, task_id,
                attempt_key, started_at.isoformat(),
                "authoritative_failure_confirms_no_side_effects_fresh_approval_required",
                (str(start.get("event_id") or ""), str(terminal[0].get("event_id") or "")),
            )
        return RecoveryAssessment(
            "outcome_unknown", mission_id, workspace_id, task_id, attempt_key,
            started_at.isoformat(), "failure_does_not_prove_absence_of_side_effects",
            (str(start.get("event_id") or ""),) + tuple(str(item.get("event_id") or "") for item in terminal),
        )

    if status != "executing":
        return RecoveryAssessment(
            "outcome_unknown", mission_id, workspace_id, task_id, attempt_key,
            started_at.isoformat(), "canonical_task_status_not_recoverable",
            (str(start.get("event_id") or ""),),
        )

    normalized_now = now.astimezone(timezone.utc)
    if normalized_now - started_at <= stale_after:
        return RecoveryAssessment(
            "not_stale", mission_id, workspace_id, task_id, attempt_key,
            started_at.isoformat(), "execution_attempt_is_within_staleness_window",
            (str(start.get("event_id") or ""),),
        )

    return RecoveryAssessment(
        "outcome_unknown", mission_id, workspace_id, task_id, attempt_key,
        started_at.isoformat(),
        "stale_execution_has_no_authoritative_terminal_outcome_hold_and_reconcile",
        (str(start.get("event_id") or ""),),
    )
