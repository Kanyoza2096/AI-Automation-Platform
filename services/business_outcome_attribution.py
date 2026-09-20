"""V66.2 — conservative business-outcome attribution and measurement.

Attribution answers *which governed execution, if any, can be linked to an
observed business outcome*. It does not authorize actions, rewrite execution
truth, or mutate policy. Ambiguous evidence is deliberately left unresolved.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from services.v68_14_business_outcome_tenant_integrity import filter_same_workspace_candidates

SCHEMA_VERSION = "1.0"
ATTRIBUTION_STATES = {"attributed", "unattributed", "review_required"}
ATTRIBUTION_METHODS = {"exact_execution_id", "external_event_id", "none", "ambiguous"}


@dataclass(frozen=True)
class AttributionCandidate:
    execution_id: str
    workspace_id: str
    action: Optional[str] = None
    decision_id: Optional[str] = None
    evaluation_id: Optional[str] = None
    candidate_id: Optional[str] = None
    handoff_id: Optional[str] = None
    observed_at: Optional[str] = None
    external_event_ids: tuple[str, ...] = ()


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _candidate(value: Any) -> AttributionCandidate:
    external = _get(value, "external_event_ids", ()) or ()
    if isinstance(external, str):
        external = (external,)
    return AttributionCandidate(
        execution_id=str(_get(value, "execution_id", "")),
        workspace_id=str(_get(value, "workspace_id", "")),
        action=_get(value, "action"),
        decision_id=_get(value, "decision_id"),
        evaluation_id=_get(value, "evaluation_id"),
        candidate_id=_get(value, "candidate_id"),
        handoff_id=_get(value, "handoff_id"),
        observed_at=_get(value, "observed_at"),
        external_event_ids=tuple(str(x) for x in external),
    )


def _validate_observation(observation: dict[str, Any]) -> None:
    if not isinstance(observation, dict):
        raise ValueError("business outcome observation must be an object")
    required = ("observation_id", "workspace_id", "execution_id", "observation_type")
    for key in required:
        if not observation.get(key):
            raise ValueError(f"{key} is required")
    if observation.get("observation_type") != "business_outcome":
        raise ValueError("V66.2 attribution requires a business_outcome observation")
    if observation.get("authority") != "none":
        raise ValueError("outcome attribution cannot grant authority")


def _attribution_id(workspace_id: str, observation_id: str) -> str:
    return "attr_" + hashlib.sha256(f"{workspace_id}|{observation_id}".encode()).hexdigest()[:24]


def _fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def attribute_business_outcome(
    observation: dict[str, Any],
    candidates: Iterable[Any],
    *,
    external_event_id: Optional[str] = None,
    attribution_window: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Attribute an observed business outcome to exactly one execution.

    Priority is explicit and deterministic:
      1. an exact execution_id already carried by the observation;
      2. an explicit external_event_id mapping;
      3. otherwise no attribution.

    If a mapping produces multiple valid executions, attribution is
    ``review_required`` rather than guessing by time, intent, or action name.
    """
    _validate_observation(observation)
    workspace_id = str(observation["workspace_id"])
    observation_id = str(observation["observation_id"])
    obs_execution_id = str(observation.get("execution_id") or "")
    normalized = [_candidate(c) for c in filter_same_workspace_candidates(observation, candidates)]
    normalized = [c for c in normalized if c.execution_id]

    exact = [c for c in normalized if c.execution_id == obs_execution_id]
    method = "exact_execution_id"
    matches = exact

    if not matches and external_event_id:
        matches = [c for c in normalized if external_event_id in c.external_event_ids]
        method = "external_event_id"

    if len(matches) == 1:
        selected = matches[0]
        state = "attributed"
        status = "attributed"
    elif len(matches) > 1:
        selected = None
        state = "review_required"
        status = "ambiguous"
        method = "ambiguous"
    else:
        selected = None
        state = "unattributed"
        status = "unattributed"
        method = "none"

    result = {
        "schema_version": SCHEMA_VERSION,
        "attribution_id": _attribution_id(workspace_id, observation_id),
        "observation_id": observation_id,
        "workspace_id": workspace_id,
        "execution_id": selected.execution_id if selected else None,
        "decision_id": selected.decision_id if selected else None,
        "evaluation_id": selected.evaluation_id if selected else None,
        "candidate_id": selected.candidate_id if selected else None,
        "handoff_id": selected.handoff_id if selected else None,
        "outcome_state": observation.get("outcome_state"),
        "business_success": observation.get("business_success"),
        "confidence": observation.get("business_success_confidence"),
        "attribution_status": state,
        "attribution_method": method,
        "attribution_window": attribution_window or {},
        "external_event_id": external_event_id,
        "evidence": {
            "observation_id": observation_id,
            "candidate_count": len(matches),
            "source": observation.get("source"),
        },
        "authority": "none",
    }
    result["fingerprint"] = _fingerprint(result)
    validate_attribution(result)
    return result


def validate_attribution(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported attribution schema")
    for key in ("attribution_id", "observation_id", "workspace_id", "attribution_status", "attribution_method"):
        if not value.get(key):
            raise ValueError(f"{key} is required")
    if value["attribution_status"] not in ATTRIBUTION_STATES:
        raise ValueError("invalid attribution_status")
    if value["attribution_method"] not in ATTRIBUTION_METHODS:
        raise ValueError("invalid attribution_method")
    if value.get("confidence") is not None and not 0 <= float(value["confidence"]) <= 1:
        raise ValueError("confidence must be between 0 and 1")
    if value.get("authority") != "none":
        raise ValueError("attribution cannot grant authority")
    if value["attribution_status"] == "attributed" and not value.get("execution_id"):
        raise ValueError("attributed outcomes require an execution_id")
    if value["attribution_status"] != "attributed" and value.get("execution_id") is not None:
        raise ValueError("non-attributed outcomes cannot carry a selected execution_id")


def measure_outcomes(
    observations: Iterable[dict[str, Any]],
    attributions: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Produce descriptive V66 metrics only; never changes policy/authority."""
    obs = list(observations)
    attrs = list(attributions)
    executions = [o for o in obs if o.get("observation_type") == "execution_receipt"]
    business = [o for o in obs if o.get("observation_type") == "business_outcome"]
    successful_exec = sum(o.get("execution_status") == "succeeded" for o in executions)
    known_business = [o for o in business if o.get("business_success") is not None]
    successful_business = sum(o.get("business_success") is True for o in known_business)
    attributed = [a for a in attrs if a.get("attribution_status") == "attributed"]
    review = [a for a in attrs if a.get("attribution_status") == "review_required"]
    confidence_values = [float(o["business_success_confidence"]) for o in known_business if o.get("business_success_confidence") is not None]

    def rate(n: int, d: int) -> Optional[float]:
        return (n / d) if d else None

    result = {
        "schema_version": SCHEMA_VERSION,
        "execution_count": len(executions),
        "execution_success_count": successful_exec,
        "execution_success_rate": rate(successful_exec, len(executions)),
        "business_outcome_count": len(business),
        "known_business_outcome_count": len(known_business),
        "business_success_count": successful_business,
        "business_success_rate": rate(successful_business, len(known_business)),
        "attributed_outcome_count": len(attributed),
        "attributed_conversion_rate": rate(sum(a.get("business_success") is True for a in attributed), len(attributed)),
        "review_required_attribution_count": len(review),
        "unknown_or_uncertain_count": sum(o.get("outcome_state") in {"unknown", "uncertain"} for o in obs),
        "average_business_outcome_confidence": rate(sum(confidence_values), len(confidence_values)),
        "authority": "none",
    }
    validate_measurement(result)
    return result


def validate_measurement(value: dict[str, Any]) -> None:
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported measurement schema")
    if value.get("authority") != "none":
        raise ValueError("measurements cannot grant authority")
    for key in ("execution_success_rate", "business_success_rate", "attributed_conversion_rate", "average_business_outcome_confidence"):
        if value.get(key) is not None and not 0 <= float(value[key]) <= 1:
            raise ValueError(f"invalid {key}")
