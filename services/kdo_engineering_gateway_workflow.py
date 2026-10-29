"""Run the existing team coordinator through KDO's configured LLM gateway.

Events are recorded in the Phase 10 development SQLite event store. This does
not replace the authoritative execution ledger, Council, or Action Kernel.
"""
from __future__ import annotations
from typing import Any, Mapping

from services.kdo_engineering_agent_team import coordinate_engineering_team, EngineeringTeamReport
from services.kdo_engineering_gateway_agents import build_gateway_agents
from services.kdo_engineering_team_events import TeamEventStore


class GatewayWorkflowError(RuntimeError):
    pass


def run_gateway_team(*, task: Mapping[str, Any], objective: str,
                     acceptance_criteria: tuple[str, ...], gateway: Any,
                     event_store: TeamEventStore,
                     agent_ids: Mapping[str, str] | None = None,
                     caller: str = "code_intelligence") -> EngineeringTeamReport:
    for key in ("task_id", "tenant_id", "workspace_id"):
        if not isinstance(task.get(key), str) or not task[key].strip():
            raise GatewayWorkflowError(f"required_task_scope_missing:{key}")
    task_id, tenant_id, workspace_id = task["task_id"], task["tenant_id"], task["workspace_id"]
    event_store.append(task_id=task_id, tenant_id=tenant_id, workspace_id=workspace_id,
                       event_type="team_run_started", actor_id="engineering_orchestrator",
                       payload={"objective": objective, "acceptance_criteria": list(acceptance_criteria),
                                "authority": "planning_only"})
    try:
        agents = build_gateway_agents(gateway=gateway, workspace_id=workspace_id,
                                      agent_ids=agent_ids, caller=caller)
        # Wrap adapters so every assignment/result is persisted with scoped events.
        for role, adapter in list(agents.items()):
            def wrapped(assignment, _role=role, _adapter=adapter):
                event_store.append(task_id=task_id, tenant_id=tenant_id, workspace_id=workspace_id,
                    event_type="agent_assignment", actor_id="engineering_orchestrator",
                    payload={"role": _role, "depends_on": list(assignment.depends_on),
                             "objective": assignment.objective})
                output = _adapter(assignment)
                event_store.append(task_id=task_id, tenant_id=tenant_id, workspace_id=workspace_id,
                    event_type="agent_contribution", actor_id=str(output.get("agent_id", "unknown")),
                    payload={"role": _role, "status": output.get("status", "failed"),
                             "summary": output.get("summary", ""),
                             "evidence_authority": output.get("evidence", {}).get("evidence_authority", "unverified")
                             if isinstance(output.get("evidence"), dict) else "unverified"})
                return output
            agents[role] = wrapped
        report = coordinate_engineering_team(task_id=task_id, objective=objective,
                                             acceptance_criteria=acceptance_criteria, agents=agents)
        event_store.append(task_id=task_id, tenant_id=tenant_id, workspace_id=workspace_id,
            event_type="team_run_finished", actor_id="engineering_orchestrator",
            payload={"status": report.status, "evidence_fingerprint": report.evidence_fingerprint,
                     "release_authorized": False})
        return report
    except Exception as exc:
        event_store.append(task_id=task_id, tenant_id=tenant_id, workspace_id=workspace_id,
            event_type="team_run_failed", actor_id="engineering_orchestrator",
            payload={"error_type": type(exc).__name__, "release_authorized": False})
        raise
