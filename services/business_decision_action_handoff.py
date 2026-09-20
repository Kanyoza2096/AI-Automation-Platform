"""V65.9 decision-to-Action-Kernel handoff contract.

This module is an admission contract, not an execution path. A handoff can only
be produced from an auditable V65.7 policy evaluation. It carries no authority;
the Action Kernel remains the only component allowed to accept an executable
ActionIntent, and its own approval, contract, idempotency, and execution checks
remain authoritative.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA_VERSION = "1.0"
HANDOFF_STATES = {"ready", "approval_required", "blocked"}
EXECUTABLE_DISPOSITIONS = {"ALLOW", "APPROVAL_REQUIRED"}


def make_handoff_idempotency_key(evaluation: dict[str, Any]) -> str:
    material = "|".join(str(evaluation[k]).strip() for k in (
        "workspace_id", "evaluation_id", "policy_version"
    ))
    return "akh_" + hashlib.sha256(material.encode()).hexdigest()


def _safe_parameters(evaluation: dict[str, Any]) -> dict[str, Any]:
    # Only deterministic context is allowed through this boundary. Never copy
    # arbitrary model output, raw evidence, or free-form AI instructions.
    return {
        "customer_id": evaluation.get("customer_id"),
        "message_id": evaluation.get("message_id"),
        "correlation_id": evaluation.get("correlation_id"),
    }


def build_action_kernel_handoff(evaluation: dict[str, Any]) -> dict[str, Any]:
    from services.business_decision_policy import validate_decision_policy_evaluation
    validate_decision_policy_evaluation(evaluation)

    disposition = str(evaluation["disposition"])
    if disposition not in EXECUTABLE_DISPOSITIONS:
        state = "blocked"
        reason = "policy_disposition_does_not_permit_action_kernel_handoff"
    elif disposition == "APPROVAL_REQUIRED":
        state = "approval_required"
        reason = "policy_requires_approval_before_action_kernel_execution"
    else:
        state = "ready"
        reason = "policy_evaluation_allows_action_kernel_admission"

    action = str(evaluation["action"])
    # The action is still only a requested kernel action type. The kernel must
    # independently validate that it is registered and that its parameter/risk
    # contract matches before accepting it.
    handoff = {
        "schema_version": SCHEMA_VERSION,
        "handoff_id": "akh_" + hashlib.sha256(
            f"{evaluation['workspace_id']}|{evaluation['evaluation_id']}".encode()
        ).hexdigest()[:24],
        "idempotency_key": make_handoff_idempotency_key(evaluation),
        "evaluation_id": evaluation["evaluation_id"],
        "candidate_id": evaluation["candidate_id"],
        "understanding_id": evaluation["understanding_id"],
        "workspace_id": evaluation["workspace_id"],
        "customer_id": evaluation.get("customer_id"),
        "action_type": action,
        "intent": evaluation.get("intent"),
        "message_id": evaluation.get("message_id"),
        "risk_level": evaluation.get("risk_level"),
        "parameters": _safe_parameters(evaluation),
        "policy_version": evaluation["policy_version"],
        "policy_disposition": disposition,
        "state": state,
        "reason": reason,
        "approval_required": state == "approval_required",
        "source": "v65.9.decision_action_handoff",
        "authority": "none",
    }
    validate_action_kernel_handoff(handoff)
    return handoff


def validate_action_kernel_handoff(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported action kernel handoff schema")
    required = (
        "handoff_id", "idempotency_key", "evaluation_id", "candidate_id",
        "understanding_id", "workspace_id", "action_type", "policy_version",
        "policy_disposition", "state", "reason",
    )
    for key in required:
        if not value.get(key):
            raise ValueError(f"{key} is required")
    if value.get("policy_disposition") not in EXECUTABLE_DISPOSITIONS | {"REVIEW", "BLOCK"}:
        raise ValueError("invalid policy disposition")
    if value.get("state") not in HANDOFF_STATES:
        raise ValueError("invalid action handoff state")
    expected_approval = value.get("state") == "approval_required"
    if value.get("approval_required") != expected_approval:
        raise ValueError("approval_required must match handoff state")
    if value.get("authority") != "none":
        raise ValueError("action handoff cannot grant authority")
    if value.get("policy_disposition") in {"REVIEW", "BLOCK"} and value.get("state") != "blocked":
        raise ValueError("non-executable dispositions must be blocked")
    if value.get("policy_disposition") == "ALLOW" and value.get("state") != "ready":
        raise ValueError("ALLOW must produce a ready handoff")
    if value.get("policy_disposition") == "APPROVAL_REQUIRED" and value.get("state") != "approval_required":
        raise ValueError("APPROVAL_REQUIRED must produce an approval-required handoff")
    if not isinstance(value.get("parameters"), dict):
        raise ValueError("handoff parameters must be an object")


def handoff_fingerprint(value: dict[str, Any]) -> str:
    validate_action_kernel_handoff(value)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
