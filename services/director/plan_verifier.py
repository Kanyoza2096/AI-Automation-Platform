"""Independent, deterministic verification for advisory Director plans.

This module evaluates a generated proposal against the current bounded context.
It does not approve, create tasks, invoke capabilities, or grant authority.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping


PLAN_SCHEMA = "kdo.director.strategic-plan.v1"
CONTEXT_SCHEMA = "kdo.director.strategic-context.v1"
FINGERPRINT_FIELDS = (
    "schema_version", "generated_at", "health_summary", "evidence",
    "objectives", "capabilities", "missions", "learning", "platform_state",
)


def _fingerprint(
    context: Mapping[str, Any], *, generated_at_override: str | None = None
) -> str:
    values = {key: context.get(key) for key in FINGERPRINT_FIELDS}
    if generated_at_override is not None:
        values["generated_at"] = generated_at_override
    payload = json.dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)



def _evidence_ids(context: Mapping[str, Any]) -> set[str]:
    """Return only evidence IDs actually exposed in the bounded planner context."""
    from .planning import _planner_context
    bounded = _planner_context(context)
    evidence = bounded.get("evidence", {})
    references = evidence.get("reference_ids", []) if isinstance(evidence, Mapping) else []
    if not isinstance(references, list):
        return set()
    return {value for value in references if isinstance(value, str) and value.strip()}

def _dag_errors(steps: list[Mapping[str, Any]]) -> list[str]:
    ids = [step.get("step_id") for step in steps]
    errors: list[str] = []
    if any(not isinstance(item, str) or not item.strip() for item in ids):
        return ["step_id_missing"]
    if len(ids) != len(set(ids)):
        errors.append("duplicate_step_id")
        return errors
    known = set(ids)
    graph: dict[str, set[str]] = {}
    for step in steps:
        step_id = step["step_id"]
        dependencies = step.get("depends_on", [])
        if not isinstance(dependencies, list) or any(
            not isinstance(item, str) or not item.strip() for item in dependencies
        ):
            errors.append(f"dependencies_invalid:{step_id}")
            continue
        dependency_set = set(dependencies)
        if step_id in dependency_set or not dependency_set.issubset(known):
            errors.append(f"dependency_reference_invalid:{step_id}")
        graph[step_id] = dependency_set

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            errors.append("dependency_cycle")
            return
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, ()):
            if dependency in graph:
                visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)
    return sorted(set(errors))


def verify_strategic_plan(
    plan: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    now: datetime | None = None,
    max_age_seconds: int = 300,
) -> dict[str, Any]:
    """Return a deterministic verification report; never changes plan or system state."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int) or max_age_seconds < 1:
        raise ValueError("max_age_seconds must be a positive integer")

    if not isinstance(plan, Mapping) or not isinstance(context, Mapping):
        return {
            "schema_version": "kdo.director.plan-verification.v1",
            "plan_id": "", "verdict": "rejected",
            "checks": {"input_contract": "fail"},
            "errors": ["plan_or_context_invalid"], "warnings": [],
            "capabilities_not_ready": [], "authority": "none",
            "execution_permitted": False, "task_creation_permitted": False,
            "governance_required": True,
            "verifier": "deterministic_director_plan_verifier",
        }

    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, str] = {}

    if not isinstance(plan, Mapping) or plan.get("schema_version") != PLAN_SCHEMA:
        errors.append("plan_schema_unsupported")
    if not isinstance(context, Mapping) or context.get("schema_version") != CONTEXT_SCHEMA:
        errors.append("context_schema_unsupported")

    if plan.get("authority") != "none" or plan.get("advisory_only") is not True:
        errors.append("plan_authority_contract_violation")
    if plan.get("execution_started") is not False or plan.get("tasks_created") is not False:
        errors.append("plan_not_advisory_only")
    checks["authority_boundary"] = "pass" if not any(
        item in errors for item in ("plan_authority_contract_violation", "plan_not_advisory_only")
    ) else "fail"

    generated_at = _timestamp(context.get("generated_at"))
    if generated_at is None:
        errors.append("context_timestamp_invalid")
        checks["context_freshness"] = "fail"
    else:
        age = (current.astimezone(timezone.utc) - generated_at).total_seconds()
        if age < -30:
            errors.append("context_timestamp_in_future")
            checks["context_freshness"] = "fail"
        elif age > max_age_seconds:
            warnings.append("context_snapshot_stale")
            checks["context_freshness"] = "stale"
        else:
            checks["context_freshness"] = "pass"

    expected_fingerprint = _fingerprint(
        context,
        generated_at_override=plan.get("source_context_generated_at"),
    )
    if plan.get("source_context_fingerprint") != expected_fingerprint:
        warnings.append("plan_context_fingerprint_mismatch")
        checks["context_binding"] = "stale"
    else:
        checks["context_binding"] = "pass"

    raw_steps = plan.get("steps")
    steps = raw_steps if isinstance(raw_steps, list) else []
    if not steps:
        errors.append("plan_steps_missing")
    elif any(not isinstance(step, Mapping) for step in steps):
        errors.append("plan_step_invalid")
        steps = []

    dag_errors = _dag_errors(steps)
    errors.extend(dag_errors)
    checks["dependency_graph"] = "fail" if dag_errors else "pass"

    capability_context = context.get("capabilities", {})
    catalogue = capability_context.get("items", []) if isinstance(capability_context, Mapping) else []
    if not isinstance(catalogue, list):
        catalogue = []
        errors.append("capability_catalogue_invalid")
    capabilities = {
        item.get("name"): item for item in catalogue
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }

    unavailable: list[str] = []
    uncited_evidence: list[str] = []
    unknown_platform_readiness: list[str] = []
    valid_evidence_ids = _evidence_ids(context)
    for step in steps:
        step_id = str(step.get("step_id", ""))[:100]
        name = step.get("capability")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"capability_name_invalid:{step_id}")
            continue
        capability = capabilities.get(name)
        if capability is None:
            errors.append(f"unknown_capability:{name}")
            continue
        if step.get("sector") != capability.get("sector"):
            errors.append(f"capability_sector_mismatch:{name}")
        if not capability.get("enabled") or not capability.get("handler_bound"):
            unavailable.append(str(name))
        for field in ("target", "verification_method"):
            value = step.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{field}_missing:{step_id}")

        criteria = step.get("acceptance_criteria")
        deliverables = step.get("deliverables")
        if not isinstance(criteria, list) or not criteria or any(
            not isinstance(value, str) or not value.strip() for value in criteria
        ):
            errors.append(f"acceptance_criteria_missing:{step.get('step_id')}")
        if not isinstance(deliverables, list) or not deliverables or any(
            not isinstance(value, str) or not value.strip() for value in deliverables
        ):
            errors.append(f"deliverables_missing:{step.get('step_id')}")

        refs = step.get("evidence_refs")
        if refs is None:
            uncited_evidence.append(f"evidence_refs:{step.get('step_id')}")
        elif not isinstance(refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            errors.append(f"evidence_refs_invalid:{step.get('step_id')}")
        elif not refs:
            warnings.append(f"evidence_refs_empty:{step.get('step_id')}")
        elif any(ref not in valid_evidence_ids for ref in refs):
            errors.append(f"evidence_reference_unknown:{step.get('step_id')}")

    if unavailable:
        warnings.extend(f"capability_not_ready:{name}" for name in sorted(set(unavailable)))
    checks["capability_contract"] = "fail" if any(
        item.startswith(("unknown_capability:", "capability_sector_mismatch:")) for item in errors
    ) else ("blocked" if unavailable else "pass")

    objective = plan.get("objective") if isinstance(plan.get("objective"), Mapping) else {}
    success_criteria = objective.get("success_criteria", [])
    if not isinstance(success_criteria, list) or not success_criteria:
        errors.append("objective_success_criteria_missing")
        checks["objective_coverage"] = "fail"
    else:
        if any(not isinstance(item, str) or not item.strip() for item in success_criteria):
            errors.append("objective_success_criteria_invalid")
        criterion_ids = {
            f"criterion:{index}" for index, _ in enumerate(success_criteria)
        }
        mapped: set[str] = set()
        has_mapping = True
        for step in steps:
            refs = step.get("criterion_refs")
            if not isinstance(refs, list):
                has_mapping = False
                continue
            if any(not isinstance(ref, str) or ref not in criterion_ids for ref in refs):
                errors.append(f"unknown_criterion_reference:{step.get('step_id')}")
            mapped.update(ref for ref in refs if isinstance(ref, str) and ref in criterion_ids)
        missing = sorted(criterion_ids - mapped)
        if not has_mapping:
            warnings.append("objective_criterion_mapping_not_supplied")
        if missing:
            warnings.append("objective_criteria_unmapped")
        checks["objective_coverage"] = (
            "pass" if has_mapping and not missing else "unverified"
        )

    if uncited_evidence:
        warnings.append("plan_step_evidence_not_cited")

    # Current Platform State is advisory corroboration. Absence is unknown, never healthy.
    platform_state = context.get("platform_state", {})
    platform_capabilities = platform_state.get("capabilities", []) if isinstance(platform_state, Mapping) else []
    if not isinstance(platform_capabilities, list):
        platform_capabilities = []
    platform_caps = {
        item.get("capability_id"): item
        for item in platform_capabilities
        if isinstance(item, Mapping) and isinstance(item.get("capability_id"), str)
    }
    for step in steps:
        name = step.get("capability")
        if not isinstance(name, str) or not name.strip():
            unknown_platform_readiness.append(f"platform_state:{step.get('step_id', 'unknown')}")
            continue
        state = platform_caps.get(name)
        if state is None:
            unknown_platform_readiness.append(f"platform_state:{name}")
        elif state.get("readiness") != "healthy":
            warnings.append(f"platform_capability_{state.get('readiness', 'unknown')}:{name}")
    if unknown_platform_readiness:
        checks["platform_readiness"] = "unknown"
        warnings.append("platform_readiness_not_fully_observed")
    else:
        checks["platform_readiness"] = "pass"

    if errors:
        verdict = "rejected"
    elif checks.get("context_freshness") == "stale" or checks.get("context_binding") == "stale":
        verdict = "replan_required"
    elif unavailable:
        verdict = "blocked"
    elif warnings:
        verdict = "review_required"
    else:
        verdict = "verified_for_governance_review"

    return {
        "schema_version": "kdo.director.plan-verification.v1",
        "plan_id": str(plan.get("plan_id", ""))[:100],
        "verdict": verdict,
        "checks": checks,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "capabilities_not_ready": sorted(set(unavailable)),
        "authority": "none",
        "execution_permitted": False,
        "task_creation_permitted": False,
        "governance_required": True,
        "verifier": "deterministic_director_plan_verifier",
    }


__all__ = ["verify_strategic_plan"]
