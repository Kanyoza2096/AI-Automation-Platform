"""Workspace-native governed planning for verified Director website evaluations.

This v1 planner is deterministic: it converts a verified advisory objective into
reviewable specialist task specifications. It has no tool access and never
creates executable tasks, content, publications, or deployments.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


PLAN_SCHEMA = "kdo.director.workspace-plan.v1"
EVALUATION_SCHEMA = "kdo.director.website-evaluation.v1"

# Stable, versioned role identity is deliberately separate from task-specific
# requirements assembled from each verified evaluation.
ROLE_CATALOGUE = {
    "business_analyst": {
        "role_version": "1.0",
        "title": "Business Analyst",
        "responsibilities": (
            "Translate the approved business objective into a bounded analysis; "
            "separate source facts, inferences, and unknowns."
        ),
        "prohibited": ("Do not execute work, alter systems, generate public content, or publish.",),
    },
    "evidence_verifier": {
        "role_version": "1.0",
        "title": "Evidence Verifier",
        "responsibilities": (
            "Check that each proposed conclusion is traceable to the supplied evidence references; "
            "identify unsupported claims and missing evidence."
        ),
        "prohibited": ("Do not invent evidence, browse outside the supplied scope, or execute actions.",),
    },
    "independent_reviewer": {
        "role_version": "1.0",
        "title": "Independent Plan Reviewer",
        "responsibilities": (
            "Review objective alignment, evidence quality, dependencies, constraints, and acceptance criteria."
        ),
        "prohibited": ("Do not approve on behalf of a human, execute steps, generate content, or publish.",),
    },
}


class WorkspacePlanningError(ValueError):
    """A workspace evaluation or proposed plan failed its contract."""


def _text(value: Any, field: str, limit: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise WorkspacePlanningError(f"{field}_invalid")
    return value.strip()


def _string_list(value: Any, field: str, minimum: int = 0, maximum: int = 12) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise WorkspacePlanningError(f"{field}_invalid")
    return [_text(item, field, 500) for item in value]


def _validate_evaluation(evaluation: Mapping[str, Any], workspace_id: str) -> dict:
    if not isinstance(evaluation, Mapping):
        raise WorkspacePlanningError("evaluation_invalid")
    if evaluation.get("schema_version") != EVALUATION_SCHEMA:
        raise WorkspacePlanningError("evaluation_schema_unsupported")
    if evaluation.get("workspace_id") != workspace_id:
        raise WorkspacePlanningError("evaluation_workspace_mismatch")
    if evaluation.get("record_scope") != "workspace" or evaluation.get("status") != "evaluated":
        raise WorkspacePlanningError("evaluation_not_completed_workspace_record")
    if evaluation.get("action_warranted") is not True:
        raise WorkspacePlanningError("evaluation_does_not_warrant_planning")
    if (
        evaluation.get("authority") != "none"
        or evaluation.get("advisory_only") is not True
        or evaluation.get("tasks_created") is not False
        or evaluation.get("content_generation_started") is not False
        or evaluation.get("public_publishing_authorized") is not False
        or evaluation.get("deployment_authorized") is not False
    ):
        raise WorkspacePlanningError("evaluation_authority_contract_invalid")
    objective = evaluation.get("recommended_objective")
    if not isinstance(objective, Mapping):
        raise WorkspacePlanningError("evaluation_objective_missing")
    normalized = {
        "title": _text(objective.get("title"), "objective_title", 200),
        "description": _text(objective.get("description"), "objective_description", 1200),
        "success_criteria": _string_list(objective.get("success_criteria"), "success_criteria", 1, 8),
        "constraints": _string_list(objective.get("constraints"), "constraints", 0, 12),
    }
    refs = _string_list(evaluation.get("evidence_refs"), "evidence_refs", 1, 2)
    if len(refs) != len(set(refs)):
        raise WorkspacePlanningError("evaluation_evidence_refs_not_unique")
    for key in ("evaluation_id", "integration_id", "event_id", "context_hash"):
        _text(evaluation.get(key), key, 256)
    if evaluation["context_hash"] and (
        len(evaluation["context_hash"]) != 64
        or any(ch not in "0123456789abcdef" for ch in evaluation["context_hash"])
    ):
        raise WorkspacePlanningError("evaluation_context_hash_invalid")
    return {**dict(evaluation), "recommended_objective": normalized, "evidence_refs": refs}


def build_workspace_plan(evaluation: Mapping[str, Any], *, workspace_id: str,
                         requested_by: str) -> dict:
    """Build a deterministic proposal; does not persist, approve, or execute it."""
    workspace_id = _text(workspace_id, "workspace_id", 128)
    requested_by = _text(requested_by, "requested_by", 256)
    source = _validate_evaluation(evaluation, workspace_id)
    objective = source["recommended_objective"]
    seed = ":".join((
        workspace_id, source["integration_id"], source["event_id"],
        source["evaluation_id"], source["context_hash"],
    ))
    plan_id = "workspace_plan_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    evidence = source["evidence_refs"]

    step_definitions = [
        (
            "step_01_analysis", "business_analyst",
            "Analyze the verified business objective",
            "Produce a bounded analysis of the objective, implications, known facts, and unknowns.",
            "A concise analysis maps the objective to each success criterion and identifies unresolved assumptions.",
            [objective["title"], *source.get("business_implications", [])],
        ),
        (
            "step_02_evidence", "evidence_verifier",
            "Verify evidence and traceability",
            "Assess whether the supplied evidence references support the proposed analysis; list gaps without inventing evidence.",
            "Every factual claim is linked to supplied evidence or explicitly marked unverified.",
            evidence,
        ),
        (
            "step_03_independent_review", "independent_reviewer",
            "Independently review the proposed work",
            "Review scope, dependencies, success criteria, constraints, and the evidence assessment.",
            "The review records pass/fail findings for every constraint and success criterion; no action is executed.",
            objective["success_criteria"],
        ),
    ]
    steps = []
    for step_id, role_id, title, task_objective, acceptance, refs in step_definitions:
        role = ROLE_CATALOGUE[role_id]
        steps.append({
            "step_id": step_id,
            "role_id": role_id,
            "role_version": role["role_version"],
            "role_identity": {
                "title": role["title"],
                "responsibilities": list(role["responsibilities"]),
                "prohibited": list(role["prohibited"]),
            },
            "task_spec": {
                "title": title,
                "objective": task_objective,
                "project_context": {
                    "workspace_id": workspace_id,
                    "integration_id": source["integration_id"],
                    "source_event_id": source["event_id"],
                    "source_evaluation_id": source["evaluation_id"],
                    "evidence_refs": refs,
                },
                "success_criteria": [acceptance],
                "constraints": objective["constraints"] + [
                    "No external changes; keep all work within the authorized workspace.",
                    "No task execution, content generation, public publishing, or deployment.",
                ],
                "tool_permissions": [],
                "deliverables": ["reviewable written findings", "evidence and uncertainty notes"],
            },
            "depends_on": [] if step_id == "step_01_analysis" else (
                ["step_01_analysis"] if step_id == "step_02_evidence"
                else ["step_01_analysis", "step_02_evidence"]
            ),
            "status": "proposed",
        })

    plan = {
        "schema_version": PLAN_SCHEMA,
        "plan_id": plan_id,
        "workspace_id": workspace_id,
        "source": {
            "evaluation_id": source["evaluation_id"],
            "integration_id": source["integration_id"],
            "event_id": source["event_id"],
            "context_hash": source["context_hash"],
            "evidence_refs": evidence,
        },
        "objective": objective,
        "steps": steps,
        "status": "proposed",
        "approval_status": "pending",
        "approval_required": True,
        "requested_by": requested_by,
        "authority": "none",
        "advisory_only": True,
        "tasks_created": False,
        "content_generation_started": False,
        "public_publishing_authorized": False,
        "deployment_authorized": False,
    }
    return plan


def propose_workspace_plan(supabase: Any, *, workspace_id: str,
                           evaluation_id: str, requested_by: str) -> dict:
    """Load one completed tenant evaluation and persist its idempotent proposal."""
    workspace_id = _text(workspace_id, "workspace_id", 128)
    evaluation_id = _text(evaluation_id, "evaluation_id", 128)
    requested_by = _text(requested_by, "requested_by", 256)
    response = (
        supabase.table("director_website_evaluations")
        .select("evaluation_id,workspace_id,integration_id,event_id,context_hash,status,payload")
        .eq("workspace_id", workspace_id)
        .eq("evaluation_id", evaluation_id)
        .eq("status", "evaluated")
        .limit(1)
        .execute()
    )
    rows = response.data or []
    if len(rows) != 1:
        raise WorkspacePlanningError("completed_workspace_evaluation_not_found")
    row = rows[0]
    if row.get("workspace_id") != workspace_id or row.get("evaluation_id") != evaluation_id:
        raise WorkspacePlanningError("evaluation_row_scope_mismatch")
    payload = row.get("payload")
    if not isinstance(payload, dict):
        raise WorkspacePlanningError("evaluation_payload_invalid")
    plan = build_workspace_plan(payload, workspace_id=workspace_id, requested_by=requested_by)
    if (
        plan["source"]["integration_id"] != row.get("integration_id")
        or plan["source"]["event_id"] != row.get("event_id")
        or plan["source"]["context_hash"] != row.get("context_hash")
    ):
        raise WorkspacePlanningError("evaluation_row_payload_mismatch")
    result = supabase.rpc(
        "create_director_workspace_plan",
        {
            "p_plan_id": plan["plan_id"],
            "p_workspace_id": workspace_id,
            "p_source_evaluation_id": evaluation_id,
            "p_requested_by": requested_by,
            "p_plan": plan,
        },
    ).execute()
    data = result.data
    if data is not True and data != [True]:
        raise WorkspacePlanningError("workspace_plan_persistence_not_confirmed")
    return plan
