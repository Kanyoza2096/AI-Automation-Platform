"""Typed shared operational state contract for the KDO platform.

This is a platform-level truth projection, not a replacement for subsystem
records or the tenant-scoped OrganismState snapshot. Facts enter through
owner-attributed observations; readiness is derived from freshness and the
canonical dependency graph. Registration alone never means readiness.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterable, Mapping


class Readiness(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EvidenceRef:
    source: str
    observed_at: datetime
    source_version: str
    correlation_id: str | None = None
    reference: str | None = None

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.source_version.strip():
            raise ValueError("evidence source and source_version are required")
        if self.observed_at.tzinfo is None:
            raise ValueError("evidence observed_at must be timezone-aware")


@dataclass(frozen=True)
class ServiceManifest:
    """Stable ownership contract; it does not assert runtime health."""

    service_id: str
    owner: str
    dependencies: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    critical: bool = False

    def __post_init__(self) -> None:
        if not self.service_id.strip() or not self.owner.strip():
            raise ValueError("service_id and owner are required")
        if self.service_id in self.dependencies:
            raise ValueError("a service cannot depend on itself")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("service dependencies must be unique")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("service capabilities must be unique")


@dataclass(frozen=True)
class ServiceObservation:
    service_id: str
    owner: str
    readiness: Readiness
    observed_at: datetime
    source_version: str
    evidence: tuple[EvidenceRef, ...] = ()
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.service_id.strip() or not self.owner.strip() or not self.source_version.strip():
            raise ValueError("service_id, owner and source_version are required")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if any(item.observed_at > self.observed_at for item in self.evidence):
            raise ValueError("evidence cannot be newer than its observation")


@dataclass(frozen=True)
class ServiceState:
    service_id: str
    owner: str
    readiness: Readiness
    observed_at: datetime | None
    source_version: str
    dependencies: tuple[str, ...]
    capabilities: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class CapabilityState:
    capability_id: str
    owner_service: str
    readiness: Readiness
    observed_at: datetime | None
    reason: str | None = None


@dataclass(frozen=True)
class PlatformStateSnapshot:
    """Immutable, versioned platform-wide operational projection."""

    scope_type: str = "platform"
    scope_id: str = "platform"
    revision: int = 0
    schema_version: int = 1
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    services: tuple[ServiceState, ...] = ()
    capabilities: tuple[CapabilityState, ...] = ()

    def __post_init__(self) -> None:
        if self.scope_type not in {"platform", "workspace"}:
            raise ValueError("scope_type must be platform or workspace")
        if not self.scope_id.strip():
            raise ValueError("scope_id is required")
        if self.scope_type == "platform" and self.scope_id != "platform":
            raise ValueError("platform scope_id must be 'platform'")
        if self.revision < 0 or self.schema_version < 1:
            raise ValueError("invalid snapshot revision or schema_version")
        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        service_ids = [item.service_id for item in self.services]
        capability_ids = [item.capability_id for item in self.capabilities]
        if len(service_ids) != len(set(service_ids)):
            raise ValueError("snapshot service ids must be unique")
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("snapshot capability ids must be unique")


def build_platform_state(
    manifests: Iterable[ServiceManifest],
    observations: Iterable[ServiceObservation],
    *,
    previous_revision: int = 0,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(minutes=5),
    scope_type: str = "platform",
    scope_id: str = "platform",
) -> PlatformStateSnapshot:
    """Derive service and capability readiness from owner evidence + dependencies.

    Missing observations remain UNKNOWN. A stale observation is STALE.
    Dependency degradation propagates to dependants; unavailable dependencies
    make a service unavailable. This function performs no I/O or actions.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or stale_after.total_seconds() <= 0:
        raise ValueError("now must be timezone-aware and stale_after positive")

    manifest_list = tuple(manifests)
    manifest_by_id = {item.service_id: item for item in manifest_list}
    if len(manifest_by_id) != len(manifest_list):
        raise ValueError("service manifests must have unique service ids")
    for manifest in manifest_list:
        missing = set(manifest.dependencies) - set(manifest_by_id)
        if missing:
            raise ValueError(f"undeclared dependencies for {manifest.service_id}: {sorted(missing)}")

    observation_by_id: dict[str, ServiceObservation] = {}
    for observation in observations:
        manifest = manifest_by_id.get(observation.service_id)
        if manifest is None:
            raise ValueError(f"observation for undeclared service: {observation.service_id}")
        if observation.owner != manifest.owner:
            raise ValueError(f"observation owner mismatch for {observation.service_id}")
        prior = observation_by_id.get(observation.service_id)
        if prior is None or observation.observed_at > prior.observed_at:
            observation_by_id[observation.service_id] = observation

    # Resolve dependency readiness iteratively; reject cycles instead of
    # publishing a partially derived state.
    resolved: dict[str, ServiceState] = {}
    pending = set(manifest_by_id)
    while pending:
        progressed = False
        for service_id in sorted(tuple(pending)):
            manifest = manifest_by_id[service_id]
            if any(dependency not in resolved for dependency in manifest.dependencies):
                continue
            observation = observation_by_id.get(service_id)
            reason = None
            if observation is None:
                readiness, observed_at, version, evidence = Readiness.UNKNOWN, None, "none", ()
                reason = "no_owner_observation"
            else:
                observed_at, version, evidence = (
                    observation.observed_at, observation.source_version, observation.evidence
                )
                if current - observed_at > stale_after:
                    readiness, reason = Readiness.STALE, "observation_expired"
                else:
                    readiness = observation.readiness

            dependency_states = [resolved[name].readiness for name in manifest.dependencies]
            if Readiness.UNAVAILABLE in dependency_states:
                readiness, reason = Readiness.UNAVAILABLE, "dependency_unavailable"
            elif Readiness.DEGRADED in dependency_states and readiness == Readiness.HEALTHY:
                readiness, reason = Readiness.DEGRADED, "dependency_degraded"
            elif Readiness.UNKNOWN in dependency_states and readiness == Readiness.HEALTHY:
                readiness, reason = Readiness.UNKNOWN, "dependency_unknown"
            elif Readiness.STALE in dependency_states and readiness == Readiness.HEALTHY:
                readiness, reason = Readiness.STALE, "dependency_stale"

            resolved[service_id] = ServiceState(
                service_id=service_id, owner=manifest.owner, readiness=readiness,
                observed_at=observed_at, source_version=version,
                dependencies=manifest.dependencies, capabilities=manifest.capabilities,
                evidence=evidence, reason=reason,
            )
            pending.remove(service_id)
            progressed = True
        if not progressed:
            raise ValueError(f"cyclic service dependencies: {sorted(pending)}")

    capability_states = []
    for manifest in manifest_list:
        service = resolved[manifest.service_id]
        for capability in manifest.capabilities:
            capability_states.append(CapabilityState(
                capability_id=capability,
                owner_service=manifest.service_id,
                readiness=service.readiness,
                observed_at=service.observed_at,
                reason=service.reason,
            ))

    return PlatformStateSnapshot(
        scope_type=scope_type, scope_id=scope_id,
        revision=previous_revision + 1, updated_at=current,
        services=tuple(resolved[name] for name in sorted(resolved)),
        capabilities=tuple(sorted(capability_states, key=lambda item: item.capability_id)),
    )


__all__ = [
    "Readiness", "EvidenceRef", "ServiceManifest", "ServiceObservation",
    "ServiceState", "CapabilityState", "PlatformStateSnapshot",
    "build_platform_state",
]
