"""V67.13 reliability observability truth contract.

Telemetry is a projection of authoritative execution/recovery facts. It must
never convert uncertainty into failure or success, and it must not hide
reconciliation debt behind a generic healthy status.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

TERMINAL = {"succeeded", "failed", "cancelled", "uncertain"}
RECOVERY_STATES = {"open", "recovery_planned", "recovered", "quarantined"}


def build_reliability_snapshot(executions: Iterable[Any], recoveries: Iterable[Any] = ()) -> dict[str, Any]:
    rows = list(executions)
    recs = list(recoveries)

    def value(obj: Any, key: str, default=None):
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

    statuses = Counter(value(r, "status") for r in rows)
    statuses.pop(None, None)
    invalid = sorted(set(statuses) - TERMINAL)
    recovery_states = Counter(value(r, "state") for r in recs)
    recovery_states.pop(None, None)
    invalid_recovery = sorted(set(recovery_states) - RECOVERY_STATES)

    uncertain = statuses.get("uncertain", 0)
    succeeded = statuses.get("succeeded", 0)
    failed = statuses.get("failed", 0)
    cancelled = statuses.get("cancelled", 0)
    terminal = sum(statuses.get(s, 0) for s in TERMINAL)
    known = succeeded + failed + cancelled

    # Reliability SLOs deliberately exclude uncertainty from a success rate.
    known_success_rate = succeeded / known if known else None
    reconciliation_debt = uncertain + recovery_states.get("open", 0) + recovery_states.get("recovery_planned", 0)

    if invalid or invalid_recovery:
        status = "invalid"
    elif reconciliation_debt:
        status = "attention_required"
    else:
        status = "healthy"

    return {
        "schema_version": "v67.13.1",
        "status": status,
        "execution": {
            "total": len(rows),
            "terminal": terminal,
            "succeeded": succeeded,
            "failed": failed,
            "cancelled": cancelled,
            "uncertain": uncertain,
            "known_outcomes": known,
            "known_success_rate": known_success_rate,
        },
        "recovery": {
            "total": len(recs),
            "states": dict(recovery_states),
            "reconciliation_debt": reconciliation_debt,
        },
        "invalid_execution_statuses": invalid,
        "invalid_recovery_states": invalid_recovery,
        "authority": "none",
    }


def validate_reliability_snapshot(snapshot: dict[str, Any]) -> None:
    if snapshot.get("schema_version") != "v67.13.1":
        raise ValueError("unsupported reliability snapshot schema")
    if snapshot.get("authority") != "none":
        raise ValueError("observability cannot grant authority")
    if snapshot.get("status") not in {"healthy", "attention_required", "invalid"}:
        raise ValueError("invalid reliability status")
    execution = snapshot.get("execution", {})
    if execution.get("uncertain", 0) and snapshot.get("status") == "healthy":
        raise ValueError("uncertainty cannot be reported as healthy")
    if execution.get("known_success_rate") is not None and not 0 <= execution["known_success_rate"] <= 1:
        raise ValueError("invalid success rate")
