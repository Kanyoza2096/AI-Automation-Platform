"""R13-J Meta-Agent production certification assessment.

This is an evidence-based certification *assessment* for the Meta Business Agent
release train. It does not deploy, certify production infrastructure, activate
providers, authorize actions, or replace existing governance/certification
systems.
"""
from __future__ import annotations

from typing import Any, Iterable

REQUIRED_STAGES = (
    "R13-A",
    "R13-B",
    "R13-C",
    "R13-D",
    "R13-E",
    "R13-F",
    "R13-G",
    "R13-H",
    "R13-I",
)

FORBIDDEN_BYPASSES = {
    "direct_provider_execution",
    "direct_knowledge_confirmation",
    "direct_knowledge_mutation",
    "direct_provider_activation",
    "governance_bypass",
    "action_kernel_bypass",
    "connector_fabric_bypass",
}

REQUIRED_BOUNDARIES = {
    "knowledge_confirmation_authority": "existing_knowledge_plane_confirmation",
    "runtime_authority": "existing_agent_runtime",
    "action_authority": "existing_action_kernel",
    "provider_authority": "existing_connector_fabric",
    "provider_certification_authority": "existing_connector_certification",
    "production_certification_authority": "external_release_certification",
}


def _bool(value: Any) -> bool:
    return value is True


def _stage_map(stage_results: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("stage")): dict(item)
        for item in stage_results
        if isinstance(item, dict) and item.get("stage")
    }


def assess_meta_agent_certification(
    *,
    stage_results: Iterable[dict[str, Any]],
    boundary_assertions: dict[str, Any] | None = None,
    safety_assertions: dict[str, Any] | None = None,
    integration_trace: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assess release evidence for the Meta-Agent chain.

    A certification candidate requires every R13 stage to report PASS, every
    architectural boundary assertion to hold, and no forbidden bypass to appear
    in the supplied integration trace. The function is deterministic and
    read-only; the result is evidence for an existing release authority.
    """
    stages = _stage_map(stage_results)
    boundaries = dict(boundary_assertions or {})
    safety = dict(safety_assertions or {})
    trace = [dict(item) for item in (integration_trace or ()) if isinstance(item, dict)]

    missing_stages = [stage for stage in REQUIRED_STAGES if stage not in stages]
    failed_stages = [
        stage for stage in REQUIRED_STAGES
        if stage in stages and stages[stage].get("status") != "PASS"
    ]

    missing_boundaries = [
        key for key, expected in REQUIRED_BOUNDARIES.items()
        if boundaries.get(key) != expected
    ]

    failed_safety = [key for key, value in safety.items() if not _bool(value)]

    observed_bypasses: list[str] = []
    for event in trace:
        bypass = event.get("bypass")
        if bypass in FORBIDDEN_BYPASSES:
            observed_bypasses.append(str(bypass))
    observed_bypasses = list(dict.fromkeys(observed_bypasses))

    blocking_reasons: list[str] = []
    if missing_stages:
        blocking_reasons.append("MISSING_STAGE_EVIDENCE")
    if failed_stages:
        blocking_reasons.append("FAILED_STAGE_EVIDENCE")
    if missing_boundaries:
        blocking_reasons.append("BOUNDARY_ASSERTION_FAILURE")
    if failed_safety:
        blocking_reasons.append("SAFETY_ASSERTION_FAILURE")
    if observed_bypasses:
        blocking_reasons.append("FORBIDDEN_ARCHITECTURAL_BYPASS")

    candidate = not blocking_reasons

    return {
        "schema": "KDO.R13J.MetaAgentCertificationAssessment/v1",
        "assessment": "CERTIFICATION_CANDIDATE" if candidate else "BLOCKED",
        "read_only": True,
        "scope": "Meta Business Agent R13 release train",
        "required_stages": list(REQUIRED_STAGES),
        "stage_summary": {
            "required": len(REQUIRED_STAGES),
            "reported": len(stages),
            "missing": missing_stages,
            "failed": failed_stages,
            "passed": [
                stage for stage in REQUIRED_STAGES
                if stage in stages and stages[stage].get("status") == "PASS"
            ],
        },
        "boundary_summary": {
            "required": dict(REQUIRED_BOUNDARIES),
            "missing_or_incorrect": missing_boundaries,
        },
        "safety_summary": {
            "failed_assertions": failed_safety,
            "observed_forbidden_bypasses": observed_bypasses,
        },
        "blocking_reasons": blocking_reasons,
        "authority": {
            "this_assessment_can_certify_production": False,
            "release_authority": "existing_production_certification_process",
            "execution_authority": "existing_action_kernel_and_connector_fabric",
            "knowledge_authority": "existing_knowledge_plane_confirmation",
        },
        "safety": {
            "does_not_deploy": True,
            "does_not_activate_provider": True,
            "does_not_authorize_action": True,
            "does_not_execute_provider": True,
            "does_not_mutate_knowledge": True,
            "does_not_replace_agent_runtime": True,
            "does_not_replace_action_kernel": True,
            "does_not_replace_connector_fabric": True,
            "does_not_self_certify_production": True,
        },
    }
