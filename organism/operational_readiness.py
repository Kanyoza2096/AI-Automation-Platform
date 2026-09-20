"""Operational readiness model for controlled staging promotion.

This layer reports readiness; it never claims that external infrastructure is
healthy unless an authoritative probe explicitly supplies that fact.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class ReadinessStatus(str, Enum):
    READY = "ready"
    BLOCKED = "blocked"
    DEGRADED = "degraded"
    UNOBSERVABLE = "unobservable"


@dataclass(frozen=True)
class ReadinessReport:
    workspace_id: str
    status: ReadinessStatus
    checks: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def promotable(self) -> bool:
        return self.status is ReadinessStatus.READY


def assess_readiness(
    workspace_id: str,
    *,
    integrity_valid: bool,
    dependencies: Mapping[str, bool | None],
    security_posture: float = 1.0,
) -> ReadinessReport:
    """Assess promotion readiness from supplied evidence.

    ``True`` means an authoritative probe succeeded, ``False`` means it
    failed, and ``None`` means the dependency was not observed. Unknown
    external state is never silently converted to healthy.
    """
    workspace_id = str(workspace_id).strip()
    checks = ["workspace", "organism_integrity", "dependency_evidence", "security_posture"]
    blockers: list[str] = []
    warnings: list[str] = []
    if not workspace_id:
        blockers.append("workspace_id is empty")
    if not integrity_valid:
        blockers.append("organism integrity is invalid")
    if not 0.0 <= float(security_posture) <= 1.0:
        blockers.append("security_posture must be between 0 and 1")
    elif security_posture < 0.7:
        blockers.append("security posture below promotion threshold")

    unknown = [name for name, state in dependencies.items() if state is None]
    failed = [name for name, state in dependencies.items() if state is False]
    if failed:
        blockers.extend(f"dependency unavailable: {name}" for name in failed)
    if unknown:
        warnings.extend(f"dependency unobserved: {name}" for name in unknown)

    if blockers:
        status = ReadinessStatus.BLOCKED
    elif unknown:
        status = ReadinessStatus.UNOBSERVABLE
    elif warnings:
        status = ReadinessStatus.DEGRADED
    else:
        status = ReadinessStatus.READY
    return ReadinessReport(workspace_id, status, tuple(checks), tuple(blockers), tuple(warnings))


__all__ = ["ReadinessReport", "ReadinessStatus", "assess_readiness"]
