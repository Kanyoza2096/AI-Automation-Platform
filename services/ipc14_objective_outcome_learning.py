"""IPC-14 — objective-aware outcome verification and closed-loop learning.

Execution truth answers whether an action ran. IPC-14 separately verifies whether
measured business evidence advanced the bound objective. Only verified objective
outcomes may produce objective-level learning; learning remains advisory and
cannot mutate policy, approval, autonomy, permissions, or execution authority.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "1.0"
ALLOWED_OPERATORS = {">", ">=", "=", "==", "<", "<=", "exists", "in"}


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(value), sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric outcome metric")
    return float(value)


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    if operator not in ALLOWED_OPERATORS:
        raise ValueError("unsupported success criterion operator")
    if operator == "exists":
        return actual is not None
    if operator == "in":
        if not isinstance(expected, (list, tuple, set)):
            raise ValueError("in criterion requires an array")
        return actual in expected
    if operator in {">", ">=", "<", "<="}:
        a, e = _number(actual), _number(expected)
        return {">": a > e, ">=": a >= e, "<": a < e, "<=": a <= e}[operator]
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return actual == expected
    return actual == expected


def verify_objective_outcome(
    *,
    observation: Mapping[str, Any],
    execution_context: Mapping[str, Any],
    attribution: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify objective progress from independent business evidence."""
    if observation.get("observation_type") != "business_outcome":
        raise ValueError("IPC-14 requires a business_outcome observation")
    ws = str(observation.get("workspace_id") or "")
    if not ws or ws != str(execution_context.get("workspace_id") or ""):
        raise ValueError("objective outcome workspace mismatch")
    objective_id = str(execution_context.get("objective_id") or "")
    if not objective_id or str(observation.get("objective_id") or "") != objective_id:
        raise ValueError("objective outcome binding mismatch")
    if str(attribution.get("workspace_id") or "") != ws:
        raise ValueError("attribution workspace mismatch")
    if attribution.get("attribution_status") != "attributed":
        raise ValueError("objective progress requires attributed outcome")
    if str(attribution.get("execution_id") or "") != str(execution_context.get("run_id") or "") and observation.get("execution_id") != attribution.get("execution_id"):
        raise ValueError("objective outcome execution lineage mismatch")
    if execution_context.get("authority") != "none" or attribution.get("authority") != "none":
        raise ValueError("objective outcome cannot grant authority")

    criteria = execution_context.get("success_criteria") or {}
    if not isinstance(criteria, Mapping):
        raise ValueError("success_criteria must be an object")
    metrics = observation.get("metrics") or {}
    if not isinstance(metrics, Mapping):
        raise ValueError("business outcome metrics must be an object")

    checks: list[dict[str, Any]] = []
    for metric, rule in criteria.items():
        if not isinstance(rule, Mapping):
            raise ValueError("each success criterion must be an object")
        operator = str(rule.get("operator") or "")
        expected = rule.get("target")
        actual = metrics.get(metric)
        passed = _compare(actual, operator, expected)
        checks.append({"metric": str(metric), "actual": actual, "operator": operator, "target": expected, "passed": passed})

    evidence_confidence = float(observation.get("business_success_confidence") or 0.0)
    criteria_passed = bool(checks) and all(c["passed"] for c in checks)
    business_success = observation.get("business_success")
    verified_success = criteria_passed and business_success is True and evidence_confidence >= 0.5
    if verified_success:
        state = "objective_progress"
    elif checks and business_success is False and evidence_confidence >= 0.5:
        state = "objective_regression"
    else:
        state = "objective_uncertain"

    result = {
        "schema_version": SCHEMA_VERSION,
        "verification_id": "ov_" + _fingerprint({"workspace_id": ws, "observation_id": observation.get("observation_id"), "objective_id": objective_id})[:24],
        "workspace_id": ws,
        "objective_id": objective_id,
        "execution_id": attribution["execution_id"],
        "observation_id": observation["observation_id"],
        "attribution_id": attribution.get("attribution_id"),
        "state": state,
        "criteria_checks": checks,
        "criteria_passed": criteria_passed,
        "business_success": business_success,
        "confidence": max(0.0, min(1.0, evidence_confidence)),
        "evidence": {"source": observation.get("source"), "attribution_method": attribution.get("attribution_method")},
        "authority": "none",
    }
    result["fingerprint"] = _fingerprint(result)
    validate_objective_outcome(result)
    return result


def build_objective_learning_signal(verification: Mapping[str, Any], *, target_type: str = "customer_outcome") -> dict[str, Any]:
    """Convert verified objective progress/regression into advisory learning."""
    validate_objective_outcome(verification)
    if target_type not in {"customer_outcome", "workflow", "decision", "action"}:
        raise ValueError("invalid learning target_type")
    state = verification["state"]
    learning_state = {"objective_progress": "positive", "objective_regression": "negative", "objective_uncertain": "uncertain"}[state]
    result = {
        "schema_version": SCHEMA_VERSION,
        "signal_id": "objlearn_" + _fingerprint({"verification_id": verification["verification_id"], "target_type": target_type})[:24],
        "workspace_id": verification["workspace_id"],
        "objective_id": verification["objective_id"],
        "execution_id": verification["execution_id"],
        "observation_id": verification["observation_id"],
        "verification_id": verification["verification_id"],
        "target_type": target_type,
        "target_id": verification["objective_id"],
        "learning_state": learning_state,
        "confidence": verification["confidence"],
        "evidence": {"verification_fingerprint": verification["fingerprint"], "criteria_passed": verification["criteria_passed"]},
        "lesson": {
            "positive": "Verified evidence indicates progress toward the bound objective; retain as advisory learning.",
            "negative": "Verified evidence indicates regression against the bound objective; retain for diagnosis and replanning.",
            "uncertain": "Objective progress is not sufficiently verified; do not learn a causal success pattern.",
        }[learning_state],
        "advisory_only": True,
        "authority": "none",
        "policy_effect": "none",
        "approval_effect": "none",
        "execution_effect": "none",
    }
    validate_objective_learning(result)
    return result


def validate_objective_outcome(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported objective outcome schema")
    for key in ("verification_id", "workspace_id", "objective_id", "execution_id", "observation_id", "state"):
        if not value.get(key):
            raise ValueError(f"{key} is required")
    if value["state"] not in {"objective_progress", "objective_regression", "objective_uncertain"}:
        raise ValueError("invalid objective outcome state")
    if value.get("authority") != "none":
        raise ValueError("objective outcome cannot grant authority")
    if not isinstance(value.get("criteria_checks"), list):
        raise ValueError("criteria_checks must be an array")


def validate_objective_learning(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported objective learning schema")
    for key in ("signal_id", "workspace_id", "objective_id", "verification_id", "learning_state", "lesson"):
        if not value.get(key):
            raise ValueError(f"{key} is required")
    if value["learning_state"] not in {"positive", "negative", "uncertain"}:
        raise ValueError("invalid objective learning state")
    if value.get("advisory_only") is not True or value.get("authority") != "none":
        raise ValueError("objective learning must remain advisory")
    for key in ("policy_effect", "approval_effect", "execution_effect"):
        if value.get(key) != "none":
            raise ValueError("learning cannot mutate governance or execution")
