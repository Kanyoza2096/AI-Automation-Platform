"""Production hardening contracts for the integrated organism.

This module is intentionally dependency-light.  It validates the *shape and
security boundaries* of a derived organism cycle; it does not certify external
infrastructure or replace Guardian, DecisionGate, or the durable execution
ledger.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


FORBIDDEN_AUTHORITY_KEYS = frozenset({
    "credential", "credentials", "secret", "token", "password",
    "permission", "permissions", "handler", "code", "authority_grant",
})


@dataclass(frozen=True)
class IntegrityReport:
    workspace_id: str
    valid: bool
    checks: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        return "ok" if self.valid else "violation"


def validate_declarative_mapping(mapping: Mapping[str, Any], *, label: str, max_items: int = 32) -> tuple[str, ...]:
    """Reject obvious authority/code channels and unbounded metadata."""
    if len(mapping) > max_items:
        raise ValueError(f"{label} exceeds {max_items} entries")
    violations = tuple(
        f"{label}.{key} is forbidden"
        for key in mapping
        if str(key).strip().lower() in FORBIDDEN_AUTHORITY_KEYS
    )
    if violations:
        raise ValueError("; ".join(violations))
    return ()


def validate_cycle_result(result: Any) -> IntegrityReport:
    """Validate a completed cycle without granting it any authority."""
    workspace_id = str(result.workspace_id)
    violations: list[str] = []
    checks: list[str] = ["workspace", "bounded_collections", "tenant_isolation", "authority_separation"]

    if not workspace_id.strip():
        violations.append("workspace_id is empty")

    bounded = {
        "observations": (result.observations, 2048),
        "learning": (result.learning, 32),
        "predictions": (result.predictions, 20),
        "dreams": (result.dreams, 1),
        "capability_checks": (result.capability_checks, 1),
        "homeostasis": (result.homeostasis, 8),
        "immune": (result.immune, 8),
    }
    for name, (items, limit) in bounded.items():
        if len(items) > limit:
            violations.append(f"{name} exceeds cycle bound {limit}")

    if result.state.workspace_id != workspace_id:
        violations.append("state workspace_id mismatch")
    for item in result.observations:
        if item.workspace_id != workspace_id:
            violations.append("observation crosses tenant boundary")
            break
    for item in result.learning:
        if item.workspace_id != workspace_id or item.experience.workspace_id != workspace_id:
            violations.append("learning result crosses tenant boundary")
            break
    if result.evolution is not None and result.evolution.workspace_id != workspace_id:
        violations.append("evolution result crosses tenant boundary")
    for check in result.capability_checks:
        if check.workspace_id != workspace_id:
            violations.append("capability check crosses tenant boundary")
            break

    # A cognitive decision is a proposal/governance artifact, never an execution handle.
    decision = result.decision
    if decision is not None:
        if getattr(decision, "workspace_id", workspace_id) != workspace_id:
            violations.append("decision crosses tenant boundary")
        for forbidden in ("execute", "credential", "token", "permission"):
            if hasattr(decision, forbidden) and forbidden == "execute":
                violations.append("decision exposes an execution method")

    return IntegrityReport(workspace_id, not violations, tuple(checks), tuple(violations))


__all__ = ["FORBIDDEN_AUTHORITY_KEYS", "IntegrityReport", "validate_cycle_result", "validate_declarative_mapping"]
