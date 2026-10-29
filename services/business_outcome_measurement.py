"""V66.1 outcome observation and attribution boundary.

Execution receipts establish what the Action Kernel/provider reported. They do
not establish business success. This module creates a durable, idempotent
observation contract that links an execution to a later measurable outcome
without granting any execution authority or allowing learning to mutate past
facts.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA_VERSION = "1.0"
OBSERVATION_TYPES = {"execution_receipt", "business_outcome", "external_signal"}
OUTCOME_STATES = {"succeeded", "failed", "uncertain", "observed", "unknown"}
SOURCE_TRUST = {"execution_ledger", "provider", "customer", "analytics", "system"}


def make_observation_idempotency_key(*, workspace_id: str, execution_id: str,
                                     observation_type: str, external_event_id: Optional[str] = None) -> str:
    if not workspace_id or not execution_id or observation_type not in OBSERVATION_TYPES:
        raise ValueError("workspace_id, execution_id and valid observation_type are required")
    material = "|".join((workspace_id, execution_id, observation_type, external_event_id or ""))
    return "outobs_" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_execution_observation(execution: Any, *, observed_at: Optional[str] = None) -> dict[str, Any]:
    """Project an authoritative execution receipt into an outcome observation.

    The status is intentionally named ``execution_status`` and never promoted
    to a business-success claim. A successful provider call can still produce a
    poor business outcome; V66 measures that separately.
    """
    def _value(name: str, default: Any = None) -> Any:
        if isinstance(execution, dict):
            return execution.get(name, default)
        return getattr(execution, name, default)

    workspace_id = _value("workspace_id")
    execution_id = _value("execution_id")
    action = _value("action")
    status = _value("status")
    provider = _value("provider")
    provider_object_id = _value("provider_object_id")
    if not workspace_id or not execution_id or not action or not status:
        raise ValueError("execution receipt is incomplete")
    if status not in {"succeeded", "failed", "uncertain", "cancelled"}:
        raise ValueError("invalid execution receipt status")
    now = observed_at or datetime.now(timezone.utc).isoformat()
    observation = {
        "schema_version": SCHEMA_VERSION,
        "observation_id": "obs_" + hashlib.sha256(f"{workspace_id}|{execution_id}".encode()).hexdigest()[:24],
        "idempotency_key": make_observation_idempotency_key(
            workspace_id=workspace_id, execution_id=execution_id,
            observation_type="execution_receipt"),
        "workspace_id": workspace_id,
        "execution_id": execution_id,
        "action": action,
        "observation_type": "execution_receipt",
        "execution_status": status,
        "outcome_state": "unknown" if status == "uncertain" else status,
        "provider": provider,
        "provider_object_id": provider_object_id,
        "observed_at": now,
        "source": "execution_ledger",
        "source_trust": "execution_ledger",
        "business_success": None,
        "business_success_confidence": None,
        "attribution_status": "not_yet_attributed",
        "evidence": _value("evidence", {}) or {},
        "authority": "none",
    }
    validate_outcome_observation(observation)
    return observation


def build_business_outcome_observation(*, workspace_id: str, execution_id: str,
                                        outcome_state: str, business_success: Optional[bool],
                                        confidence: Optional[float], source: str,
                                        evidence: Optional[dict[str, Any]] = None,
                                        external_event_id: Optional[str] = None,
                                        observed_at: Optional[str] = None) -> dict[str, Any]:
    if outcome_state not in OUTCOME_STATES:
        raise ValueError("invalid outcome_state")
    if source not in SOURCE_TRUST:
        raise ValueError("invalid outcome source")
    if confidence is not None and not 0.0 <= float(confidence) <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    if outcome_state in {"observed", "unknown"} and business_success is not None and outcome_state == "unknown":
        raise ValueError("unknown outcomes cannot claim business success")
    observation = {
        "schema_version": SCHEMA_VERSION,
        "observation_id": "obs_" + hashlib.sha256(
            f"{workspace_id}|{execution_id}|business_outcome|{external_event_id or ''}".encode()).hexdigest()[:24],
        "idempotency_key": make_observation_idempotency_key(
            workspace_id=workspace_id, execution_id=execution_id,
            observation_type="business_outcome", external_event_id=external_event_id),
        "workspace_id": workspace_id,
        "execution_id": execution_id,
        "action": None,
        "observation_type": "business_outcome",
        "execution_status": None,
        "outcome_state": outcome_state,
        "provider": None,
        "provider_object_id": None,
        "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
        "source": source,
        "source_trust": source,
        "business_success": business_success,
        "business_success_confidence": confidence,
        "attribution_status": "attributed" if business_success is not None else "observed",
        "evidence": evidence or {},
        "authority": "none",
    }
    validate_outcome_observation(observation)
    return observation


def validate_outcome_observation(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported outcome observation schema")
    required = ("observation_id", "idempotency_key", "workspace_id", "execution_id",
                "observation_type", "outcome_state", "observed_at", "source", "attribution_status")
    for key in required:
        if not value.get(key):
            raise ValueError(f"{key} is required")
    if value["observation_type"] not in OBSERVATION_TYPES:
        raise ValueError("invalid observation_type")
    if value["outcome_state"] not in OUTCOME_STATES:
        raise ValueError("invalid outcome_state")
    if value["source"] not in SOURCE_TRUST:
        raise ValueError("invalid source")
    if value.get("business_success_confidence") is not None and not 0.0 <= float(value["business_success_confidence"]) <= 1.0:
        raise ValueError("invalid business_success_confidence")
    if value.get("authority") != "none":
        raise ValueError("outcome observations cannot grant authority")
    if value["observation_type"] == "execution_receipt" and value.get("business_success") is not None:
        raise ValueError("execution receipts cannot claim business success")
    if not isinstance(value.get("evidence", {}), dict):
        raise ValueError("evidence must be an object")


def observation_fingerprint(value: dict[str, Any]) -> str:
    validate_outcome_observation(value)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
