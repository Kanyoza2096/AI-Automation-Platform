"""Dynamic specialist-role registry and task-to-team selection for KDO.

Role definitions are capability metadata, not claims that an agent/provider is
installed. Runtime adapters must be explicitly registered by the host.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class AgentRole:
    role_id: str
    group: str
    description: str
    capabilities: frozenset[str]
    default_enabled: bool = True


ROLE_CATALOGUE = (
    AgentRole("engineering_manager", "leadership", "Coordinates assignments, dependencies, and escalation.", frozenset({"coordination", "scheduling"})),
    AgentRole("product_analyst", "leadership", "Converts user needs into stories and acceptance criteria.", frozenset({"requirements", "acceptance_criteria"})),
    AgentRole("software_architect", "leadership", "Defines boundaries, interfaces, and technical decisions.", frozenset({"architecture", "interfaces"})),
    AgentRole("planner", "leadership", "Decomposes objectives into ordered engineering tasks.", frozenset({"planning", "decomposition"})),
    AgentRole("frontend_engineer", "implementation", "Implements UI, accessibility, and client-side behavior.", frozenset({"frontend", "ui", "accessibility"})),
    AgentRole("backend_engineer", "implementation", "Implements APIs, service logic, and backend behavior.", frozenset({"backend", "api", "services"})),
    AgentRole("database_engineer", "implementation", "Handles schemas, migrations, transactions, and query design.", frozenset({"database", "schema", "migrations"})),
    AgentRole("integration_engineer", "implementation", "Connects verified internal and external interfaces.", frozenset({"integration", "api"})),
    AgentRole("refactoring_engineer", "implementation", "Improves maintainability while preserving behavior.", frozenset({"refactoring", "maintainability"})),
    AgentRole("test_engineer", "quality", "Creates and runs tests with explicit result evidence.", frozenset({"tests", "test_evidence"})),
    AgentRole("qa_engineer", "quality", "Validates acceptance criteria and end-to-end workflows.", frozenset({"qa", "e2e"})),
    AgentRole("debugging_engineer", "quality", "Reproduces defects and investigates root causes.", frozenset({"debugging", "root_cause"})),
    AgentRole("performance_engineer", "quality", "Evaluates latency, throughput, and resource use.", frozenset({"performance", "profiling"})),
    AgentRole("independent_reviewer", "quality", "Reviews changes independently from implementation.", frozenset({"review", "independent_review"})),
    AgentRole("security_engineer", "governance", "Performs threat modeling and secure-code analysis.", frozenset({"security", "threat_model"})),
    AgentRole("authorization_auditor", "governance", "Checks permission boundaries and policy enforcement.", frozenset({"authorization", "policy_audit"})),
    AgentRole("risk_analyst", "governance", "Identifies release risks and compliance obligations.", frozenset({"risk", "compliance"})),
    AgentRole("evidence_auditor", "governance", "Checks traceability and evidence completeness.", frozenset({"evidence", "audit"})),
    AgentRole("devops_engineer", "operations", "Maintains CI/CD and deployment automation.", frozenset({"cicd", "deployment"})),
    AgentRole("infrastructure_engineer", "operations", "Handles environments, containers, and infrastructure.", frozenset({"infrastructure", "containers"})),
    AgentRole("reliability_engineer", "operations", "Handles retries, recovery, and fault tolerance.", frozenset({"reliability", "recovery", "idempotency"})),
    AgentRole("observability_engineer", "operations", "Improves logs, metrics, traces, and diagnostics.", frozenset({"observability", "telemetry"})),
    AgentRole("ai_ml_engineer", "ai_platform", "Integrates and evaluates models and agent behavior.", frozenset({"ai", "models", "evaluation"})),
    AgentRole("context_engineer", "ai_platform", "Builds bounded task context and agent instructions.", frozenset({"context", "prompt_design"})),
    AgentRole("tooling_engineer", "ai_platform", "Builds controlled tool interfaces and adapters.", frozenset({"tools", "tool_adapters"})),
    AgentRole("documentation_engineer", "ai_platform", "Maintains technical documentation and guides.", frozenset({"documentation"})),
    AgentRole("agent_evaluation_engineer", "ai_platform", "Evaluates agent outcomes against repeatable benchmarks.", frozenset({"agent_eval", "benchmarks"})),
)

ROLE_BY_ID = {role.role_id: role for role in ROLE_CATALOGUE}


class AgentRegistryError(ValueError):
    pass


class AgentRegistry:
    """Registry metadata plus explicitly installed runtime adapters."""
    def __init__(self, roles: Iterable[AgentRole] = ROLE_CATALOGUE):
        self._roles = {}
        self._adapters = {}
        for role in roles:
            if role.role_id in self._roles:
                raise AgentRegistryError("duplicate_role_id:" + role.role_id)
            self._roles[role.role_id] = role

    def roles(self) -> tuple[AgentRole, ...]:
        return tuple(self._roles[k] for k in sorted(self._roles))

    def register_adapter(self, role_id: str, adapter) -> None:
        if role_id not in self._roles:
            raise AgentRegistryError("unknown_role:" + role_id)
        if not callable(adapter):
            raise AgentRegistryError("adapter_must_be_callable")
        self._adapters[role_id] = adapter

    def adapter(self, role_id: str):
        return self._adapters.get(role_id)

    def installed_roles(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))

    def select(self, capabilities: Iterable[str], *, required: Iterable[str] = ()) -> tuple[str, ...]:
        wanted = frozenset(capabilities)
        chosen = {r.role_id for r in self._roles.values() if r.capabilities & wanted}
        chosen.update(required)
        unknown = chosen - self._roles.keys()
        if unknown:
            raise AgentRegistryError("unknown_required_roles:" + ",".join(sorted(unknown)))
        return tuple(sorted(chosen))


TASK_CAPABILITY_MAP = {
    "feature": ({"requirements", "architecture", "planning", "frontend", "backend", "tests", "review"}, {"product_analyst", "software_architect", "planner", "test_engineer", "independent_reviewer"}),
    "bugfix": ({"debugging", "backend", "frontend", "tests", "review"}, {"debugging_engineer", "planner", "test_engineer", "independent_reviewer"}),
    "frontend": ({"frontend", "ui", "accessibility", "tests", "review"}, {"frontend_engineer", "test_engineer", "independent_reviewer"}),
    "backend": ({"backend", "api", "services", "database", "tests", "review"}, {"backend_engineer", "test_engineer", "independent_reviewer"}),
    "security": ({"security", "authorization", "policy_audit", "review"}, {"security_engineer", "authorization_auditor", "independent_reviewer"}),
    "reliability": ({"reliability", "recovery", "idempotency", "observability", "tests"}, {"reliability_engineer", "observability_engineer", "test_engineer"}),
    "ai_agent": ({"ai", "models", "context", "tools", "agent_eval", "review"}, {"ai_ml_engineer", "context_engineer", "tooling_engineer", "agent_evaluation_engineer", "independent_reviewer"}),
    "documentation": ({"documentation", "architecture", "review"}, {"documentation_engineer", "independent_reviewer"}),
}


def plan_team(task_type: str, registry: AgentRegistry | None = None) -> dict:
    registry = registry or AgentRegistry()
    if task_type not in TASK_CAPABILITY_MAP:
        raise AgentRegistryError("unsupported_task_type:" + task_type)
    capabilities, required = TASK_CAPABILITY_MAP[task_type]
    selected = registry.select(capabilities, required=required)
    return {
        "task_type": task_type,
        "selected_roles": list(selected),
        "installed_roles": [r for r in selected if registry.adapter(r) is not None],
        "unavailable_roles": [r for r in selected if registry.adapter(r) is None],
        "ready": all(registry.adapter(r) is not None for r in selected),
        "execution_authorized": False,
    }
