"""Bounded, advisory strategic objective planning for KDO Director."""
from __future__ import annotations
import hashlib
import json
import uuid
from typing import Any, Callable, Mapping


class StrategicPlanningError(ValueError):
    """A proposed strategic plan failed its contract or evidence checks."""


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _strings(value: Any, field: str, maximum: int = 20) -> list[str]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= maximum:
        raise StrategicPlanningError(f"{field}_invalid")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise StrategicPlanningError(f"{field}_invalid")
    return [item.strip() for item in value]


def _validate_dag(steps: list[dict[str, Any]]) -> None:
    ids = [step["step_id"] for step in steps]
    if len(ids) != len(set(ids)):
        raise StrategicPlanningError("duplicate_step_id")
    graph = {step["step_id"]: set(step["depends_on"]) for step in steps}
    known = set(ids)
    for step_id, dependencies in graph.items():
        if step_id in dependencies or not dependencies.issubset(known):
            raise StrategicPlanningError("invalid_plan_dependency")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise StrategicPlanningError("plan_dependency_cycle")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


def build_strategic_plan(*, objective: Mapping[str, Any], context: Mapping[str, Any],
                         generator: Callable[..., Mapping[str, Any]]) -> dict[str, Any]:
    """Generate and validate a bounded plan; never executes or creates tasks."""
    if not isinstance(objective, Mapping):
        raise StrategicPlanningError("objective_invalid")
    title, description = objective.get("title"), objective.get("description")
    if not isinstance(title, str) or not title.strip():
        raise StrategicPlanningError("objective_title_required")
    if not isinstance(description, str) or not description.strip():
        raise StrategicPlanningError("objective_description_required")
    objective_id = objective.get("objective_id") or f"obj_{uuid.uuid4().hex[:12]}"
    if not isinstance(objective_id, str) or not objective_id.strip():
        raise StrategicPlanningError("objective_id_invalid")
    criteria = _strings(objective.get("success_criteria"), "success_criteria")
    constraints = objective.get("constraints", [])
    if not isinstance(constraints, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in constraints
    ):
        raise StrategicPlanningError("objective_constraints_invalid")
    normalized_objective = {
        "objective_id": objective_id.strip(), "title": title.strip(),
        "description": description.strip(), "success_criteria": criteria,
        "constraints": [item.strip() for item in constraints],
    }

    if context.get("schema_version") != "kdo.director.strategic-context.v1":
        raise StrategicPlanningError("strategic_context_version_unsupported")
    if context.get("authority") != "none":
        raise StrategicPlanningError("strategic_context_authority_violation")
    catalogue = context.get("capabilities", {}).get("items", [])
    if not isinstance(catalogue, list):
        raise StrategicPlanningError("capability_catalogue_invalid")
    capabilities = {
        item.get("name"): item for item in catalogue
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }

    generated = generator(objective=normalized_objective, context=context)
    if not isinstance(generated, Mapping) or set(generated) - {"summary", "steps"}:
        raise StrategicPlanningError("planner_output_invalid")
    summary, raw_steps = generated.get("summary"), generated.get("steps")
    if not isinstance(summary, str) or not summary.strip():
        raise StrategicPlanningError("plan_summary_required")
    if not isinstance(raw_steps, (list, tuple)) or not 1 <= len(raw_steps) <= 12:
        raise StrategicPlanningError("plan_step_count_invalid")

    allowed = {"step_id", "capability", "sector", "objective", "depends_on",
               "acceptance_criteria", "deliverables", "rationale"}
    steps = []
    for raw in raw_steps:
        if not isinstance(raw, Mapping) or set(raw) - allowed:
            raise StrategicPlanningError("plan_step_fields_invalid")
        step_id, cap_name = raw.get("step_id"), raw.get("capability")
        step_objective, sector, rationale = (
            raw.get("objective"), raw.get("sector"), raw.get("rationale")
        )
        if not all(isinstance(v, str) and v.strip() for v in (
            step_id, cap_name, step_objective, sector, rationale
        )):
            raise StrategicPlanningError("plan_step_required_field_missing")
        capability = capabilities.get(cap_name.strip())
        if capability is None:
            raise StrategicPlanningError("plan_references_unknown_capability")
        if sector.strip() != capability.get("sector"):
            raise StrategicPlanningError("plan_capability_sector_mismatch")
        dependencies = raw.get("depends_on", [])
        if not isinstance(dependencies, (list, tuple)) or any(
            not isinstance(v, str) or not v.strip() for v in dependencies
        ):
            raise StrategicPlanningError("plan_dependencies_invalid")
        steps.append({
            "step_id": step_id.strip(), "capability": cap_name.strip(),
            "sector": sector.strip(), "objective": step_objective.strip(),
            "depends_on": [v.strip() for v in dependencies],
            "acceptance_criteria": _strings(raw.get("acceptance_criteria"), "step_acceptance_criteria"),
            "deliverables": _strings(raw.get("deliverables"), "step_deliverables"),
            "rationale": rationale.strip(),
            "capability_readiness": {
                "enabled": bool(capability.get("enabled")),
                "handler_bound": bool(capability.get("handler_bound")),
                "disposition": (
                    "candidate_for_later_governed_admission"
                    if capability.get("enabled") and capability.get("handler_bound")
                    else "advisory_only_capability_not_ready"
                ),
            },
            "authority": "none",
        })
    _validate_dag(steps)
    context_fingerprint = _fingerprint({
        key: context.get(key) for key in (
            "schema_version", "generated_at", "health_summary", "evidence",
            "objectives", "capabilities", "missions", "learning"
        )
    })
    return {
        "schema_version": "kdo.director.strategic-plan.v1",
        "plan_id": f"dplan_{uuid.uuid4().hex[:16]}",
        "objective": normalized_objective, "summary": summary.strip(),
        "steps": steps, "step_count": len(steps),
        "dependency_edge_count": sum(len(step["depends_on"]) for step in steps),
        "source_context_schema_version": context["schema_version"],
        "source_context_generated_at": context.get("generated_at"),
        "source_context_fingerprint": context_fingerprint,
        "status": "proposed", "advisory_only": True, "authority": "none",
        "execution_started": False, "tasks_created": False,
        "provider_calls_made_by_director": False,
        "governance_required_before_execution": True,
        "limitations": [
            "A generated plan is a proposal, not an approved decision or executable task graph.",
            "Capability readiness is descriptive and does not grant runtime permission.",
            "Consequential steps must pass existing governance and Action Kernel admission.",
            "The plan is based on a bounded platform snapshot and may become stale.",
        ],
    }
