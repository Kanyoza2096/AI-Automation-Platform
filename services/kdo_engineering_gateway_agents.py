"""Provider-neutral engineering role adapters over KDO's existing LLM gateway.

The gateway produces bounded interpretation/proposal evidence only. This module
never turns model output into ActionIntent, never executes tools, and never
confers authorization. A host must inject a configured gateway instance.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping
import json

from services.kdo_engineering_agent_team import AgentAssignment, AgentTeamError
from services.kdo_engineering_prompt_assembler import (
    EngineeringPromptAssembler, EngineeringTaskAssignment, PromptAssemblyError,
)


_OUTPUT_KEYS = ("status", "summary", "evidence")
_ALLOWED_STATUSES = {"succeeded", "failed", "uncertain"}


class GatewayAgentError(RuntimeError):
    pass


def make_gateway_agent(*, gateway: Any, role: str, agent_id: str,
                       workspace_id: str, caller: str = "code_intelligence",
                       max_tokens: int = 1200,
                       prompt_assembler: EngineeringPromptAssembler | None = None,
                       policy_version: str = "1") -> Callable[[AgentAssignment], Mapping[str, Any]]:
    """Build one role adapter using ``LLMGateway.complete_structured``.

    ``agent_id`` is host-assigned, not model-selected. Invalid/truncated or
    failed responses become explicit failures; no defaults claim success.
    """
    if gateway is None or not callable(getattr(gateway, "complete_structured", None)):
        raise GatewayAgentError("configured_gateway_required")
    if not all(isinstance(v, str) and v.strip() for v in (role, agent_id, workspace_id, caller)):
        raise GatewayAgentError("role_agent_workspace_and_caller_required")
    if not isinstance(max_tokens, int) or not 128 <= max_tokens <= 8000:
        raise GatewayAgentError("max_tokens_out_of_range")

    assembler = prompt_assembler or EngineeringPromptAssembler()

    def invoke(assignment: AgentAssignment) -> Mapping[str, Any]:
        if assignment.role != role:
            return {"agent_id": agent_id, "status": "failed", "summary": "role assignment mismatch",
                    "evidence": {"error": "role_assignment_mismatch"}}
        raw_context = dict(assignment.context or {})
        acceptance = raw_context.get("acceptance_criteria", [])
        deliverables = raw_context.get("deliverables", [])
        policy_refs = raw_context.get("policy_refs", [])
        try:
            task = EngineeringTaskAssignment(
                task_id=assignment.task_id,
                role=assignment.role,
                objective=assignment.objective,
                acceptance_criteria=tuple(acceptance) if isinstance(acceptance, (list, tuple)) else (),
                depends_on=tuple(assignment.depends_on),
                deliverables=tuple(deliverables) if isinstance(deliverables, (list, tuple)) else (),
                context={k: v for k, v in raw_context.items() if k not in {"acceptance_criteria", "deliverables", "policy_refs"}},
                policy_refs=tuple(policy_refs) if isinstance(policy_refs, (list, tuple)) else (),
            )
            prompt, prompt_manifest = assembler.assemble(
                task, policy_version=policy_version,
                model_config={"temperature": 0.1, "max_tokens": max_tokens},
            )
        except (PromptAssemblyError, TypeError, ValueError) as exc:
            return {"agent_id": agent_id, "status": "failed", "summary": "invalid task assignment",
                    "evidence": {"error": str(exc) if isinstance(exc, PromptAssemblyError) else "invalid_task_assignment"}}
        schema = json.dumps(assembler.output_schema, sort_keys=True, separators=(",", ":"))
        try:
            result = gateway.complete_structured(
                prompt, schema_hint=schema, workspace_id=workspace_id,
                temperature=0.1, max_tokens=max_tokens, required_keys=list(_OUTPUT_KEYS),
                caller=caller,
            )
        except Exception as exc:
            return {"agent_id": agent_id, "status": "failed", "summary": "gateway call failed",
                    "evidence": {"error_type": type(exc).__name__}}
        if not getattr(result, "ok", False):
            return {"agent_id": agent_id, "status": "failed", "summary": "gateway returned no valid response",
                    "evidence": {"gateway_reason": str(getattr(result, "reason", "unknown")),
                                 "request_id": str(getattr(result, "request_id", "") or ""),
                                 "provider": str(getattr(result, "provider", "") or "")}}
        data = getattr(result, "data", None)
        if not isinstance(data, dict) or any(k not in data for k in _OUTPUT_KEYS):
            return {"agent_id": agent_id, "status": "failed", "summary": "invalid structured response",
                    "evidence": {"error": "invalid_gateway_schema"}}
        status = data.get("status")
        summary = data.get("summary")
        evidence = data.get("evidence")
        if status not in _ALLOWED_STATUSES or not isinstance(summary, str) or not summary.strip() or not isinstance(evidence, dict):
            return {"agent_id": agent_id, "status": "failed", "summary": "invalid structured response values",
                    "evidence": {"error": "invalid_gateway_values"}}
        # Evidence is explicitly marked as model-reported and not authoritative.
        return {"agent_id": agent_id, "status": status, "summary": summary.strip(),
                "evidence": {"model_reported": evidence,
                             "evidence_authority": "unverified_model_output",
                             "gateway_request_id": str(getattr(result, "request_id", "") or ""),
                             "provider": str(getattr(result, "provider", "") or ""),
                             "model": str(getattr(result, "model", "") or ""),
                             "finish_reason": getattr(result, "finish_reason", None),
                             "prompt_manifest": prompt_manifest}}
    return invoke


def build_gateway_agents(*, gateway: Any, workspace_id: str,
                         agent_ids: Mapping[str, str] | None = None,
                         caller: str = "code_intelligence") -> dict[str, Callable]:
    """Build the four coordinator roles; all use one injected gateway policy path."""
    ids = dict(agent_ids or {r: f"gateway:{r}" for r in ("planner", "implementer", "test_engineer", "reviewer")})
    roles = ("planner", "implementer", "test_engineer", "reviewer")
    if any(not isinstance(ids.get(r), str) or not ids[r].strip() for r in roles):
        raise GatewayAgentError("all_role_agent_ids_required")
    if ids["implementer"] == ids["reviewer"]:
        raise GatewayAgentError("reviewer_must_have_distinct_agent_identity")
    return {role: make_gateway_agent(gateway=gateway, role=role, agent_id=ids[role],
                                     workspace_id=workspace_id, caller=caller) for role in roles}
