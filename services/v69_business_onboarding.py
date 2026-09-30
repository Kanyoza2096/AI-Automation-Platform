"""V69 onboarding state machine built on the integration contracts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from services.business_integration_platform import (
    BusinessOperatingIdentity, ConnectorContract, ConnectionState,
    IntegrationConnection, validate_objective_capabilities,
)


@dataclass(frozen=True)
class OnboardingAssessment:
    workspace_id: str
    stage: str
    ready: bool
    blockers: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()


def assess_onboarding(
    identity: BusinessOperatingIdentity,
    connections: Sequence[IntegrationConnection],
    connectors: Sequence[ConnectorContract],
) -> OnboardingAssessment:
    """Read-only onboarding assessment. It never changes connection state."""
    blockers: list[str] = []
    next_actions: list[str] = []
    tenant_connections = [c for c in connections if c.workspace_id == identity.workspace_id]
    if not tenant_connections:
        return OnboardingAssessment(identity.workspace_id, "connect", False,
                                    ("no tenant-scoped integrations connected",),
                                    ("connect at least one integration",))

    connector_map = {c.connector_key: c for c in connectors}
    for connection in tenant_connections:
        contract = connector_map.get(connection.connector_key)
        if contract is None:
            blockers.append(f"missing connector contract:{connection.connector_key}")
            continue
        if not contract.tenant_scoped:
            blockers.append(f"connector not tenant scoped:{connection.connector_key}")
        if connection.state in {ConnectionState.REVOKED, ConnectionState.SUSPENDED, ConnectionState.DEGRADED}:
            blockers.append(f"connection unavailable:{connection.connector_key}:{connection.state.value}")

    required = []
    for objective in identity.objectives:
        # Objective-to-capability mapping is intentionally explicit metadata,
        # never inferred into authority. Consumers can supply actual mappings.
        required.extend(identity.metadata.get("objective_capabilities", {}).get(objective, []))
    capability_result = validate_objective_capabilities(identity.workspace_id, required, tenant_connections)
    blockers.extend(f"missing capability:{x}" for x in capability_result["missing"])

    if blockers:
        return OnboardingAssessment(identity.workspace_id, "validation", False,
                                    tuple(sorted(set(blockers))),
                                    ("resolve onboarding blockers before enabling execution",))
    if any(c.state == ConnectionState.EXECUTION_ENABLED for c in tenant_connections):
        return OnboardingAssessment(identity.workspace_id, "operating", True, (),
                                    ("continue outcome measurement and connector health monitoring",))
    if any(c.state == ConnectionState.SUGGEST_ENABLED for c in tenant_connections):
        return OnboardingAssessment(identity.workspace_id, "suggest", True, (),
                                    ("observe suggestions and certify bounded execution when appropriate",))
    return OnboardingAssessment(identity.workspace_id, "observe", True, (),
                                ("validate mappings and connector certification before suggestion/execution",))
