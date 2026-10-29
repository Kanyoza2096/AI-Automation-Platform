"""Role-based AI engineering team coordination with explicit evidence boundaries.

The coordinator does not grant authority, execute code, or call providers. Concrete
agent adapters are injected by the host application. Independent review cannot be
satisfied by the implementer identity.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Callable, Mapping
import hashlib
import json

REQUIRED_ROLES = ("planner", "implementer", "test_engineer", "reviewer")

class AgentTeamError(RuntimeError):
    pass

@dataclass(frozen=True)
class AgentAssignment:
    task_id: str
    role: str
    objective: str
    context: Mapping[str, Any]
    depends_on: tuple[str, ...] = ()

@dataclass(frozen=True)
class AgentContribution:
    role: str
    agent_id: str
    status: str
    summary: str
    evidence: Mapping[str, Any]

@dataclass(frozen=True)
class EngineeringTeamReport:
    task_id: str
    status: str
    contributions: tuple[AgentContribution, ...]
    evidence_fingerprint: str
    release_authorized: bool = False
    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "status": self.status,
                "contributions": [asdict(x) for x in self.contributions],
                "evidence_fingerprint": self.evidence_fingerprint,
                "release_authorized": False}

def coordinate_engineering_team(*, task_id: str, objective: str,
                                acceptance_criteria: tuple[str, ...],
                                agents: Mapping[str, Callable[[AgentAssignment], Mapping[str, Any]]]
                                ) -> EngineeringTeamReport:
    """Run injected role agents sequentially and produce an auditable report.

    Role output schema: agent_id, status='succeeded', summary, evidence.
    The flow is planner -> implementer -> test engineer -> independent reviewer.
    It does not claim parallel execution, provider integration, approval, or release.
    """
    if not task_id.strip() or not objective.strip() or not acceptance_criteria:
        raise AgentTeamError("task_objective_and_acceptance_criteria_required")
    missing = [role for role in REQUIRED_ROLES if not callable(agents.get(role))]
    if missing:
        raise AgentTeamError("missing_agent_roles:" + ",".join(missing))
    outputs: dict[str, Mapping[str, Any]] = {}
    contributions: list[AgentContribution] = []
    sequence = (("planner", ()), ("implementer", ("planner",)),
                ("test_engineer", ("planner", "implementer")),
                ("reviewer", ("planner", "implementer", "test_engineer")))
    for role, dependencies in sequence:
        assignment = AgentAssignment(task_id, role, objective,
            {"acceptance_criteria": list(acceptance_criteria),
             "prior_contributions": {d: dict(outputs[d]) for d in dependencies}}, dependencies)
        try:
            raw = agents[role](assignment)
        except Exception as exc:
            raw = {"agent_id": "unknown", "status": "failed", "summary": "agent invocation failed",
                   "evidence": {"error_type": type(exc).__name__}}
        if not isinstance(raw, Mapping):
            raw = {"agent_id": "unknown", "status": "failed", "summary": "agent returned non-mapping output", "evidence": {}}
        agent_id = str(raw.get("agent_id") or "").strip()
        status = str(raw.get("status") or "failed").strip().lower()
        summary = str(raw.get("summary") or "").strip()
        evidence = raw.get("evidence", {})
        if not isinstance(evidence, Mapping):
            evidence = {"invalid_evidence_type": type(evidence).__name__}
            status = "failed"
        if not agent_id or not summary:
            status = "failed"
        outputs[role] = {"agent_id": agent_id, "status": status, "summary": summary,
                         "evidence": dict(evidence)}
        contributions.append(AgentContribution(role, agent_id or "unknown", status,
                                               summary or "No summary supplied", dict(evidence)))
        if status != "succeeded":
            break
    roles = {x.role: x for x in contributions}
    if len(contributions) != len(REQUIRED_ROLES) or any(
        roles.get(role) is None or roles[role].status != "succeeded" for role in REQUIRED_ROLES):
        final_status = "blocked"
    else:
        independent = roles["reviewer"].agent_id != roles["implementer"].agent_id
        review = roles["reviewer"].evidence
        approved = review.get("review_status") == "approved" and bool(review.get("review_id"))
        tests_passed = roles["test_engineer"].evidence.get("tests_passed") is True
        final_status = "awaiting_authorization" if independent and approved and tests_passed else "blocked"
    serial = json.dumps({"task_id": task_id, "objective": objective,
        "contributions": [asdict(x) for x in contributions], "status": final_status,
        "release_authorized": False}, sort_keys=True, separators=(",", ":"), default=str)
    return EngineeringTeamReport(task_id, final_status, tuple(contributions),
                                 hashlib.sha256(serial.encode()).hexdigest(), False)
