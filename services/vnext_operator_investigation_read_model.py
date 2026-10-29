"""Canonical read-only operator investigation projection.

This module links already-authoritative evidence for one execution. It never
creates a causal claim, incident, execution, retry, or reconciliation record.
Correlation is association only unless an authoritative source explicitly
provides stronger evidence.
"""
from __future__ import annotations
from typing import Any, Mapping, Sequence


def _clean(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _state(value: Any) -> str:
    return "AVAILABLE" if value is not None else "UNKNOWN"


def build_operator_investigation_read_model(
    *,
    workspace_id: str,
    execution: Mapping[str, Any],
    intent: Mapping[str, Any] | None = None,
    governance: Mapping[str, Any] | None = None,
    observations: Sequence[Mapping[str, Any]] = (),
    attributions: Sequence[Mapping[str, Any]] = (),
    events: Sequence[Mapping[str, Any]] = (),
    incident_signal: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    execution = dict(execution)
    intent = dict(intent) if intent else None
    governance = dict(governance) if governance else None
    observations = [dict(x) for x in observations]
    attributions = [dict(x) for x in attributions]
    events = [dict(x) for x in events]

    if str(execution.get("workspace_id")) != str(workspace_id):
        raise ValueError("execution workspace mismatch")
    for name, row in (("intent", intent), ("governance", governance)):
        if row is not None and row.get("workspace_id") is not None and str(row.get("workspace_id")) != str(workspace_id):
            raise ValueError(f"{name} workspace mismatch")

    execution_id = execution.get("execution_id")
    linked_events = [e for e in events if _event_matches(e, execution, intent)]
    linked_obs = [o for o in observations if str(o.get("execution_id")) == str(execution_id)]
    linked_attrs = [a for a in attributions if str(a.get("execution_id")) == str(execution_id)]
    business = [o for o in linked_obs if o.get("observation_type") == "business_outcome"]
    latest_business = business[0] if business else None

    identity = {
        "workspaceId": workspace_id,
        "executionId": execution_id,
        "intentId": intent.get("intent_id") if intent else None,
        "decisionId": intent.get("decision_id") if intent else None,
        "correlationId": intent.get("correlation_id") if intent else None,
        "commandId": intent.get("command_id") if intent else None,
        "transactionId": intent.get("transaction_id") if intent else None,
        "idempotencyKey": execution.get("idempotency_key") or (intent or {}).get("idempotency_key"),
    }

    connector = {
        "state": _state(execution.get("provider")),
        "provider": execution.get("provider"),
        "providerObjectId": execution.get("provider_object_id"),
        "providerRequestId": execution.get("provider_request_id"),
        "source": "external_execution_ledger",
    }
    outcome = {
        "state": _state(intent),
        "status": intent.get("status") if intent else None,
        "output": intent.get("output") if intent else None,
        "error": intent.get("error") if intent else None,
    }
    reconciliation_required = execution.get("status") == "uncertain" or bool((execution.get("evidence") or {}).get("reconciliation_required"))
    verification = {
        "state": "AVAILABLE" if linked_obs else "UNKNOWN",
        "observations": _clean(linked_obs),
        "businessOutcome": _clean(latest_business),
        "attributions": _clean(linked_attrs),
        "businessSuccess": latest_business.get("business_success") if latest_business else None,
        "businessSuccessConfidence": latest_business.get("business_success_confidence") if latest_business else None,
        "authority": "none",
    }

    return {
        "schema": "CommandCenterOperatorInvestigation/v1",
        "version": "vNext.1",
        "workspaceId": workspace_id,
        "executionId": execution_id,
        "state": "LIVE",
        "authority": {
            "readOnly": True,
            "actionAuthorityGranted": False,
            "reconciliationAuthorityGranted": False,
            "causalInferenceGranted": False,
            "sourceOfTruth": "backend_authoritative_evidence",
        },
        "identity": identity,
        "chain": {
            "signal": {
                "state": "AVAILABLE" if incident_signal else "UNKNOWN",
                "data": _clean(incident_signal),
            },
            "intent": {"state": _state(intent), "data": _clean(intent)},
            "governance": {"state": _state(governance), "data": _clean(governance)},
            "execution": {"state": "AVAILABLE", "data": _clean(execution)},
            "connector": {"state": connector["state"], "data": connector},
            "events": {
                "state": "AVAILABLE" if linked_events else "UNKNOWN",
                "associationOnly": True,
                "items": _clean(linked_events),
            },
            "outcome": {"state": outcome["state"], "data": outcome},
            "businessOutcome": {
                "state": verification["state"],
                "data": verification,
            },
            "verification": {
                "state": verification["state"],
                "data": verification,
            },
            "reconciliation": {
                "state": "REQUIRED" if reconciliation_required else "NOT_REQUIRED" if execution.get("status") in {"succeeded", "failed", "cancelled"} else "UNKNOWN",
                "required": reconciliation_required,
                "authority": "reconciliation_service",
            },
        },
        "causality": {
            "claim": False,
            "status": "ASSOCIATION_ONLY",
            "statement": "Correlated identifiers establish association, not causal proof.",
        },
        "summary": {
            "executionStatus": execution.get("status"),
            "provider": execution.get("provider"),
            "eventCount": len(linked_events),
            "businessOutcomeState": verification["state"],
            "reconciliationRequired": reconciliation_required,
            "incidentSignal": bool(incident_signal),
        },
    }


def _event_matches(event: Mapping[str, Any], execution: Mapping[str, Any], intent: Mapping[str, Any] | None) -> bool:
    identifiers = {
        str(execution.get("execution_id")),
        str(execution.get("correlation_id")) if execution.get("correlation_id") else "",
        str((intent or {}).get("correlation_id")) if (intent or {}).get("correlation_id") else "",
        str((intent or {}).get("command_id")) if (intent or {}).get("command_id") else "",
        str((intent or {}).get("transaction_id")) if (intent or {}).get("transaction_id") else "",
    }
    for key in ("execution_id", "executionId", "correlation_id", "correlationId", "command_id", "commandId", "transaction_id", "transactionId"):
        value = event.get(key)
        if value is not None and str(value) in identifiers:
            return True
    return False
