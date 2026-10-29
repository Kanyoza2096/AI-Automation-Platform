"""Phase 8 — Business Reliability convergence projector."""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Tuple

from models.business_reliability import (
    BusinessReliabilityAssessment,
    BusinessReliabilityObjective,
    ReliabilitySignal,
    ReliabilitySignalKind,
    assess_business_reliability,
)


class BusinessReliabilityProjector:
    """Converges existing KDO reliability evidence into a business read model.

    It never executes recovery, mutates health, or changes SLOs.
    """

    def assess(
        self,
        objective: BusinessReliabilityObjective,
        *,
        slo: Mapping[str, Any] | None = None,
        dependency_health: Iterable[Any] = (),
        drift: Iterable[Any] = (),
        reconciliation_debt: int = 0,
        recovery_blocked: bool = False,
    ) -> BusinessReliabilityAssessment:
        signals = []
        if slo is not None:
            status = str(slo.get("status", "unknown")).lower()
            signals.append(ReliabilitySignal(
                workspace_id=objective.workspace_id,
                resource_ref=objective.resource_ref,
                kind=ReliabilitySignalKind.SLO,
                value=status,
                healthy=True if status == "healthy" else False if status in {"degraded", "critical"} else None,
                evidence_ref=slo.get("evidence_ref"),
                signal_id="slo",
            ))
        for item in dependency_health:
            data = item.to_dict() if hasattr(item, "to_dict") else dict(item)
            state = str(data.get("state", "UNKNOWN")).upper()
            signals.append(ReliabilitySignal(
                workspace_id=objective.workspace_id,
                resource_ref=objective.resource_ref,
                kind=ReliabilitySignalKind.DEPENDENCY,
                value=state,
                healthy=state == "HEALTHY" if state else None,
                evidence_ref=data.get("evidence_ref"),
                signal_id=f"dependency:{data.get('dependency_id', 'unknown')}",
            ))
        for item in drift:
            data = item.to_dict() if hasattr(item, "to_dict") else dict(item)
            status = str(data.get("status", data.get("state", "UNKNOWN"))).upper()
            signals.append(ReliabilitySignal(
                workspace_id=objective.workspace_id,
                resource_ref=objective.resource_ref,
                kind=ReliabilitySignalKind.DRIFT,
                value=status,
                healthy=status in {"MATCHED", "HEALTHY", "NONE"},
                evidence_ref=data.get("evidence_ref"),
                signal_id=f"drift:{data.get('finding_id', data.get('resource_ref', 'unknown'))}",
            ))
        if reconciliation_debt:
            signals.append(ReliabilitySignal(
                workspace_id=objective.workspace_id,
                resource_ref=objective.resource_ref,
                kind=ReliabilitySignalKind.RECONCILIATION_DEBT,
                value=reconciliation_debt,
                healthy=reconciliation_debt <= objective.max_reconciliation_debt,
                signal_id="reconciliation_debt",
            ))
        return assess_business_reliability(
            objective,
            tuple(signals),
            reconciliation_debt=reconciliation_debt,
            recovery_blocked=recovery_blocked,
        )
