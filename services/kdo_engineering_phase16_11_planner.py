"""Phase 16.11: structured AI planning through KDO's existing LLM gateway.

Planning is proposal-only: it cannot execute commands, modify repositories, approve
plans, or grant release authority. The resulting steps must still be persisted and
reviewed through the existing revision-bound approval path.
"""
from __future__ import annotations
import json
from typing import Any, Mapping

from services.kdo_engineering_prompt_assembler import (
    EngineeringPromptAssembler, EngineeringTaskAssignment, PromptAssemblyError,
)

class EngineeringPlannerError(RuntimeError):
    pass

PLAN_SCHEMA = {
    "type": "object",
    "required": ["status", "summary", "evidence"],
    "properties": {
        "status": {"type": "string", "enum": ["succeeded", "failed", "uncertain"]},
        "summary": {"type": "string", "minLength": 1},
        "evidence": {"type": "object"},
    },
    "additionalProperties": False,
}

def make_engineering_plan_generator(
    gateway: Any, *, prompt_assembler: EngineeringPromptAssembler | None = None,
    policy_version: str = "1",
):
    """Create a fail-closed planner adapter backed by LLMGateway.complete_structured."""
    if gateway is None or not callable(getattr(gateway, "complete_structured", None)):
        raise EngineeringPlannerError("configured_llm_gateway_required")

    assembler = prompt_assembler or EngineeringPromptAssembler(output_schema=PLAN_SCHEMA)

    def generate(*, task: Mapping[str, Any], workspace_id: str) -> dict[str, Any]:
        if not isinstance(task, Mapping) or not workspace_id:
            raise EngineeringPlannerError("task_and_workspace_required")
        objective = task.get("objective")
        criteria = task.get("acceptance_criteria")
        if not isinstance(objective, str) or not objective.strip():
            raise EngineeringPlannerError("task_objective_required")
        if not isinstance(criteria, (list, tuple)) or not criteria or any(not isinstance(x, str) or not x.strip() for x in criteria):
            raise EngineeringPlannerError("acceptance_criteria_required")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise EngineeringPlannerError("task_id_required")
        planning_contract = {
            "response_contract": "Return 3-12 concise, ordered, bounded steps in evidence.steps.",
            "include_when_relevant": [
                "repository inspection",
                "implementation",
                "targeted tests",
                "independent verification",
            ],
            "prohibited_plan_actions": [
                "secrets or credential exposure",
                "destructive actions without an explicit reviewed requirement",
                "production deployment or git push",
                "claims that work or tests have already run",
            ],
            "authority": "proposal_only; no repository writes, command execution, approval, or release authority",
        }
        assignment = EngineeringTaskAssignment(
            task_id=task_id.strip(),
            role="planner",
            objective=objective.strip(),
            acceptance_criteria=tuple(x.strip() for x in criteria),
            context={"planning_contract": planning_contract},
            policy_refs=("engineering-planning-policy",),
        )
        try:
            prompt, prompt_manifest = assembler.assemble(
                assignment,
                policy_version=policy_version,
                model_config={"temperature": 0.1, "max_tokens": 1600},
            )
        except PromptAssemblyError as exc:
            raise EngineeringPlannerError("planner_prompt_assembly_failed") from exc
        result = gateway.complete_structured(
            prompt, schema_hint=json.dumps(assembler.output_schema, sort_keys=True, separators=(",", ":")),
            workspace_id=workspace_id, temperature=0.1, max_tokens=1600,
            required_keys=["status", "summary", "evidence"], caller="code_intelligence",
        )
        if not getattr(result, "ok", False):
            raise EngineeringPlannerError("llm_planning_unavailable")
        data = getattr(result, "data", None)
        if not isinstance(data, dict) or data.get("status") != "succeeded":
            raise EngineeringPlannerError("llm_planning_not_successful")
        evidence = data.get("evidence")
        steps = evidence.get("steps") if isinstance(evidence, dict) else None
        if not isinstance(steps, list) or not 3 <= len(steps) <= 12 or any(
            not isinstance(s, str) or not s.strip() or len(s.strip()) > 2000 for s in steps
        ):
            raise EngineeringPlannerError("planner_output_steps_invalid")
        normalized = [s.strip() for s in steps]
        return {"steps": normalized, "summary": str(data.get("summary", "")).strip(),
                "planner": {"provider": str(getattr(result, "provider", "") or "unknown"),
                            "model": str(getattr(result, "model", "") or "unknown"),
                            "request_id": str(getattr(result, "request_id", "") or ""),
                            "evidence_authority": "model_proposal_unverified",
                            "prompt_manifest": prompt_manifest},
                "approval_required": True, "execution_started": False, "release_authorized": False}
    return generate
