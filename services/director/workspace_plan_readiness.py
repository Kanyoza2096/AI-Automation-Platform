"""Deterministic, non-executing readiness checks for governed workspace plans.

This module never grants capabilities or starts work. A positive review result
means only that a plan passed structural/governance preflight; execution remains
blocked until a separately designed, explicitly authorized execution boundary exists.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


READINESS_SCHEMA = "kdo.director.workspace-plan-readiness.v1"


class PlanReadinessError(ValueError):
    """A readiness request could not be evaluated safely."""


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def assess_workspace_plan_readiness(
    plan: Mapping[str, Any],
    *,
    persisted_workspace_id: str,
    persisted_status: str,
    approval_status: str,
    requested_by: str,
    approved_by: str | None,
) -> dict:
    """Return a deterministic advisory preflight report; never execute or authorize.

    Inputs for persistence and approval must come from the durable workspace-plan
    record, not caller-supplied request fields.
    """
    if not isinstance(plan, Mapping):
        raise PlanReadinessError("plan_invalid")

    blockers: list[str] = []
    warnings: list[str] = []

    plan_id = plan.get("plan_id")
    workspace_id = plan.get("workspace_id")
    if not _nonempty(plan_id):
        blockers.append("plan_id_missing")
    if not _nonempty(workspace_id) or workspace_id != persisted_workspace_id:
        blockers.append("workspace_scope_mismatch")
    if not _nonempty(persisted_workspace_id):
        blockers.append("persisted_workspace_missing")

    if plan.get("schema_version") != "kdo.director.workspace-plan.v1":
        blockers.append("plan_schema_unsupported")
    if plan.get("authority") != "none" or plan.get("advisory_only") is not True:
        blockers.append("plan_authority_contract_invalid")
    for field in (
        "tasks_created", "content_generation_started",
        "public_publishing_authorized", "deployment_authorized",
    ):
        if plan.get(field) is not False:
            blockers.append(f"{field}_must_be_false")

    if persisted_status not in {"proposed", "approved", "rejected"}:
        blockers.append("persisted_status_invalid")
    if plan.get("status") != persisted_status:
        blockers.append("persisted_plan_status_mismatch")
    if approval_status not in {"pending", "approved", "rejected"}:
        blockers.append("approval_status_invalid")
    if plan.get("approval_status") != approval_status:
        blockers.append("approval_status_mismatch")
    if plan.get("approval_required") is not True:
        blockers.append("independent_approval_required")

    if not _nonempty(requested_by) or plan.get("requested_by") != requested_by:
        blockers.append("requester_provenance_mismatch")
    if approval_status == "pending":
        if approved_by is not None:
            blockers.append("pending_plan_has_approver")
        blockers.append("independent_human_approval_pending")
    elif approval_status == "approved":
        if not _nonempty(approved_by):
            blockers.append("approved_plan_missing_reviewer")
        elif approved_by == requested_by:
            blockers.append("requester_cannot_approve_own_plan")
        if persisted_status != "approved":
            blockers.append("approved_status_not_persisted")
    elif approval_status == "rejected":
        if not _nonempty(approved_by):
            blockers.append("rejected_plan_missing_reviewer")
        elif approved_by == requested_by:
            blockers.append("requester_cannot_reject_own_plan")
        blockers.append("plan_rejected")

    steps = plan.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 12:
        blockers.append("plan_steps_invalid")
        steps = []

    seen: set[str] = set()
    for index, step in enumerate(steps):
        prefix = f"step_{index + 1}"
        if not isinstance(step, Mapping):
            blockers.append(f"{prefix}_invalid")
            continue
        step_id = step.get("step_id")
        if not _nonempty(step_id) or step_id in seen:
            blockers.append(f"{prefix}_identity_invalid")
            continue
        seen.add(step_id)
        if not _nonempty(step.get("role_id")) or not _nonempty(step.get("role_version")):
            blockers.append(f"{prefix}_role_identity_missing")
        if not isinstance(step.get("role_identity"), Mapping):
            blockers.append(f"{prefix}_role_definition_missing")
        spec = step.get("task_spec")
        if not isinstance(spec, Mapping):
            blockers.append(f"{prefix}_task_spec_missing")
            continue
        for field in ("title", "objective"):
            if not _nonempty(spec.get(field)):
                blockers.append(f"{prefix}_{field}_missing")
        if not isinstance(spec.get("success_criteria"), list) or not spec["success_criteria"]:
            blockers.append(f"{prefix}_success_criteria_missing")
        if not isinstance(spec.get("constraints"), list) or not spec["constraints"]:
            blockers.append(f"{prefix}_constraints_missing")
        if not isinstance(spec.get("deliverables"), list) or not spec["deliverables"]:
            blockers.append(f"{prefix}_deliverables_missing")
        if spec.get("tool_permissions") != []:
            blockers.append(f"{prefix}_tool_permissions_not_empty")
        if step.get("status") != "proposed":
            blockers.append(f"{prefix}_not_proposed")

    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            continue
        dependencies = step.get("depends_on")
        if not isinstance(dependencies, list):
            blockers.append(f"step_{index + 1}_dependencies_invalid")
            continue
        if any(dep not in seen or dep == step.get("step_id") for dep in dependencies):
            blockers.append(f"step_{index + 1}_dependency_reference_invalid")
        # Plans are generated in topological order; dependencies must precede dependants.
        prior_ids = {item.get("step_id") for item in steps[:index] if isinstance(item, Mapping)}
        if any(dep not in prior_ids for dep in dependencies):
            blockers.append(f"step_{index + 1}_dependency_order_invalid")

    if not isinstance(plan.get("source"), Mapping):
        blockers.append("source_provenance_missing")
    else:
        for field in ("evaluation_id", "integration_id", "event_id", "context_hash"):
            if not _nonempty(plan["source"].get(field)):
                blockers.append(f"source_{field}_missing")
        refs = plan["source"].get("evidence_refs")
        if not isinstance(refs, list) or not refs or any(not _nonempty(ref) for ref in refs):
            blockers.append("source_evidence_refs_missing")

    # v1 deliberately has no execution-capability grant model. Even a reviewed
    # plan can only be marked structurally reviewable, never executable.
    warnings.append("execution_capabilities_not_implemented")
    review_ready = not blockers
    report_seed = {
        "plan_id": plan_id,
        "workspace_id": persisted_workspace_id,
        "persisted_status": persisted_status,
        "approval_status": approval_status,
        "requested_by": requested_by,
        "approved_by": approved_by,
        "blockers": sorted(set(blockers)),
    }
    readiness_id = "plan_readiness_" + hashlib.sha256(
        json.dumps(report_seed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return {
        "schema_version": READINESS_SCHEMA,
        "readiness_id": readiness_id,
        "plan_id": plan_id,
        "workspace_id": persisted_workspace_id,
        "record_scope": "workspace",
        "review_ready": review_ready,
        "execution_ready": False,
        "readiness_status": (
            "reviewable_non_executable" if review_ready
            else "blocked"
        ),
        "blockers": sorted(set(blockers)),
        "warnings": warnings,
        "authority": "none",
        "advisory_only": True,
        "capabilities_granted": [],
        "tasks_created": False,
        "content_generation_started": False,
        "public_publishing_authorized": False,
        "deployment_authorized": False,
    }
