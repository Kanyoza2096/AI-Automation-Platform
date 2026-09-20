"""V67.11 provider certification contract.

Defines the minimum evidence a provider adapter must expose before KDO may use
it for autonomous side-effect recovery. This is a contract, not an executor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ExternalProviderCertificationAdapter(Protocol):
    """Provider adapter surface required for autonomous certification."""

    provider_name: str
    sandbox: bool

    def submit(self, *, idempotency_key: str, correlation_id: str, **kwargs: Any) -> dict[str, Any]: ...

    def reconcile(self, *, idempotency_key: str, correlation_id: str) -> dict[str, Any]: ...

    def isolation_evidence(self) -> dict[str, Any]: ...

    def idempotency_evidence(self) -> dict[str, Any]: ...

    def termination_evidence(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CertificationResult:
    status: str
    checks: dict[str, bool]
    evidence: dict[str, Any]
    errors: tuple[str, ...] = ()


def certify_adapter(adapter: ExternalProviderCertificationAdapter) -> CertificationResult:
    """Run structural certification without causing a provider side effect."""
    errors: list[str] = []
    checks: dict[str, bool] = {}
    evidence: dict[str, Any] = {}

    checks["protocol"] = isinstance(adapter, ExternalProviderCertificationAdapter)
    if not checks["protocol"]:
        errors.append("adapter does not implement the certification protocol")
        return CertificationResult("blocked", checks, evidence, tuple(errors))

    checks["named_provider"] = bool(str(getattr(adapter, "provider_name", "")).strip())
    checks["sandbox_declared"] = getattr(adapter, "sandbox", False) is True
    if not checks["named_provider"]:
        errors.append("provider_name is required")
    if not checks["sandbox_declared"]:
        errors.append("certification adapters must explicitly declare sandbox=True")

    for name, fn in (
        ("idempotency", adapter.idempotency_evidence),
        ("isolation", adapter.isolation_evidence),
        ("termination", adapter.termination_evidence),
    ):
        try:
            value = fn()
            evidence[name] = value
            checks[f"{name}_evidence"] = isinstance(value, dict) and bool(value)
        except Exception as exc:  # certification is fail-closed
            checks[f"{name}_evidence"] = False
            errors.append(f"{name} evidence failed: {exc}")

    required_idempotency = evidence.get("idempotency", {})
    checks["provider_idempotency_documented"] = required_idempotency.get("atomic") is True and required_idempotency.get("key_scoped") is True
    if not checks["provider_idempotency_documented"]:
        errors.append("provider idempotency must be atomic and key-scoped")

    isolation = evidence.get("isolation", {})
    checks["sandbox_isolation"] = isolation.get("isolated") is True and isolation.get("production_access") is False
    if not checks["sandbox_isolation"]:
        errors.append("sandbox must be isolated and deny production access")

    termination = evidence.get("termination", {})
    checks["controlled_termination"] = termination.get("worker_termination_supported") is True and termination.get("side_effect_after_kill_testable") is True
    if not checks["controlled_termination"]:
        errors.append("controlled worker termination evidence is required")

    checks["reconciliation_surface"] = callable(getattr(adapter, "reconcile", None))
    if not checks["reconciliation_surface"]:
        errors.append("reconciliation surface is required")

    status = "passed" if all(checks.values()) and not errors else "blocked"
    return CertificationResult(status, checks, evidence, tuple(errors))
