"""Read-only adapter from the existing health registry to Platform State.

It consumes only the registry's last published HealthReport snapshot. It never
starts probes, reads providers directly, or writes to persistence. The
canonical dependency graph remains the sole dependency declaration.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from core.engine_topology import CANONICAL_ENGINE_DEPENDENCIES
from core.platform_state import (
    EvidenceRef,
    ObserverObservation,
    PlatformStateSnapshot,
    Readiness,
    ServiceManifest,
    ServiceObservation,
    build_platform_state,
)


_STATUS_MAP = {
    "healthy": Readiness.HEALTHY,
    "nominal": Readiness.HEALTHY,
    "ok": Readiness.HEALTHY,
    "ready": Readiness.HEALTHY,
    "running": Readiness.HEALTHY,
    "degraded": Readiness.DEGRADED,
    "unhealthy": Readiness.UNAVAILABLE,
    "offline": Readiness.UNAVAILABLE,
    "failed": Readiness.UNAVAILABLE,
    "critical": Readiness.UNAVAILABLE,
    "stopped": Readiness.UNAVAILABLE,
    "unknown": Readiness.UNKNOWN,
    "starting": Readiness.UNKNOWN,
    "stale": Readiness.STALE,
}


def _field(report: Any, name: str, default: Any = None) -> Any:
    if isinstance(report, Mapping):
        return report.get(name, default)
    return getattr(report, name, default)


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _readiness(report: Any) -> Readiness:
    raw = _field(report, "status")
    value = getattr(raw, "value", raw)
    return _STATUS_MAP.get(str(value or "unknown").strip().lower(), Readiness.UNKNOWN)


def build_platform_state_from_health_reports(
    reports: Mapping[str, Any],
    *,
    previous_revision: int = 0,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(minutes=5),
    dependencies: Mapping[str, tuple[str, ...]] = CANONICAL_ENGINE_DEPENDENCIES,
    supplemental_statuses: Mapping[str, str] | None = None,
    supplemental_observed_at: datetime | None = None,
    service_ownership: Mapping[str, tuple[str, bool]] | None = None,
) -> PlatformStateSnapshot:
    """Build a conservative projection from owner-named health reports.

    Only exact canonical service names are accepted. The health provider's
    service_name must match its registry key, which prevents accidental
    aliasing from attributing one provider's report to another service.
    """
    owner_overrides = {
        "redis": "health_monitor",
        "supabase": "health_monitor",
        "gemini": "health_monitor",
    }
    if service_ownership is None:
        try:
            from core.service_definitions import SERVICE_DEFINITIONS
            service_ownership = {
                name: (name, bool(critical))
                for name, _resolver, critical in SERVICE_DEFINITIONS
            }
        except Exception:
            service_ownership = {}

    manifest_metadata = {}
    for service_id in dependencies:
        if service_id in owner_overrides:
            manifest_metadata[service_id] = (owner_overrides[service_id], False)
        else:
            manifest_metadata[service_id] = service_ownership.get(
                service_id, ("unassigned", False)
            )
    manifests = tuple(
        ServiceManifest(
            service_id=service_id,
            owner=manifest_metadata[service_id][0],
            dependencies=tuple(required),
            critical=manifest_metadata[service_id][1],
        )
        for service_id, required in dependencies.items()
    )
    manifest_by_id = {item.service_id: item for item in manifests}
    observations: list[ServiceObservation] = []
    for service_id, report in reports.items():
        manifest = manifest_by_id.get(service_id)
        if manifest is None or manifest.owner == "unassigned" or service_id in owner_overrides:
            # Infrastructure probes are owned by HealthMonitor and supplied
            # separately below; do not accept a similarly named provider.
            continue
        reported_name = str(_field(report, "service_name", service_id) or "")
        if reported_name != service_id:
            continue
        observed_at = _timestamp(_field(report, "checked_at"))
        if observed_at is None:
            continue
        correlation_id = _field(report, "correlation_id")
        metadata = _field(report, "metadata", {})
        metadata = metadata if isinstance(metadata, Mapping) else {}
        source_version = str(metadata.get("version") or "health-report-v1")
        evidence = EvidenceRef(
            source=f"health_registry:{service_id}",
            observed_at=observed_at,
            source_version=source_version,
            correlation_id=str(correlation_id) if correlation_id else None,
        )
        observations.append(ServiceObservation(
            service_id=service_id,
            owner=manifest.owner,
            readiness=_readiness(report),
            observed_at=observed_at,
            source_version=source_version,
            evidence=(evidence,),
            correlation_id=str(correlation_id) if correlation_id else None,
        ))

    observed_at = _timestamp(supplemental_observed_at) if supplemental_observed_at else None
    for service_id, raw_status in (supplemental_statuses or {}).items():
        manifest = manifest_by_id.get(service_id)
        if manifest is None or manifest.owner != "health_monitor" or observed_at is None:
            continue
        status = str(raw_status or "unknown").strip().lower()
        readiness = {
            "ok": Readiness.HEALTHY,
            "healthy": Readiness.HEALTHY,
            "down": Readiness.UNAVAILABLE,
            "unhealthy": Readiness.UNAVAILABLE,
        }.get(status, Readiness.UNKNOWN)
        evidence = EvidenceRef(
            source=f"health_monitor_probe:{service_id}",
            observed_at=observed_at,
            source_version="health-monitor-probe-v1",
        )
        observations.append(ServiceObservation(
            service_id=service_id,
            owner=manifest.owner,
            readiness=readiness,
            observed_at=observed_at,
            source_version="health-monitor-probe-v1",
            evidence=(evidence,),
        ))

    # Meta Engine is an independent supervisor, not the owner of the services
    # it observes. Preserve its per-engine assessments as advisory evidence;
    # they must never promote or replace an owner's readiness observation.
    current = now or datetime.now(timezone.utc)
    meta_report = reports.get("meta_engine")
    meta_metadata = _field(meta_report, "metadata", {})
    if not isinstance(meta_metadata, Mapping):
        meta_metadata = {}
    observer_observations: list[ObserverObservation] = []
    raw_meta_observations = meta_metadata.get("engine_observations", ())
    if isinstance(raw_meta_observations, (list, tuple)):
        for item in raw_meta_observations:
            if not isinstance(item, Mapping):
                continue
            service_id = str(item.get("service_id") or "").strip()
            if not service_id or service_id == "meta_engine" or service_id not in dependencies:
                continue
            observed_at = _timestamp(item.get("observed_at"))
            if observed_at is None:
                continue
            raw_status = str(item.get("status") or "unknown").strip().lower()
            readiness = _STATUS_MAP.get(raw_status, Readiness.UNKNOWN)
            if observed_at > current + timedelta(seconds=30):
                readiness = Readiness.UNKNOWN
            elif current - observed_at > stale_after:
                readiness = Readiness.STALE
            source_version = str(item.get("source_version") or meta_metadata.get("version") or "meta-engine-observer-v1")
            correlation_id = item.get("correlation_id") or _field(meta_report, "correlation_id")
            evidence = EvidenceRef(
                source="meta_engine:engine_health",
                observed_at=observed_at,
                source_version=source_version,
                correlation_id=str(correlation_id) if correlation_id else None,
            )
            observer_observations.append(ObserverObservation(
                service_id=service_id,
                observer_id="meta_engine",
                readiness=readiness,
                observed_at=observed_at,
                source_version=source_version,
                evidence=(evidence,),
                correlation_id=str(correlation_id) if correlation_id else None,
            ))

    snapshot = build_platform_state(
        manifests,
        observations,
        previous_revision=previous_revision,
        now=current,
        stale_after=stale_after,
    )
    return replace(
        snapshot,
        observer_observations=tuple(sorted(
            observer_observations,
            key=lambda item: (item.service_id, item.observer_id),
        )),
    )


def read_platform_state_from_health_registry(
    *,
    registry: Any = None,
    previous_revision: int = 0,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(minutes=5),
    supplemental_statuses: Mapping[str, str] | None = None,
    dependencies: Mapping[str, tuple[str, ...]] = CANONICAL_ENGINE_DEPENDENCIES,
    service_ownership: Mapping[str, tuple[str, bool]] | None = None,
) -> PlatformStateSnapshot:
    """Read the cached health snapshot without triggering any health probes."""
    if registry is None:
        from lifecycle import health_registry as registry
    reader = getattr(registry, "read_snapshot", None)
    if not callable(reader):
        raise RuntimeError("health_registry_cached_snapshot_unavailable")
    reports = reader()
    if not isinstance(reports, Mapping):
        raise RuntimeError("health_registry_snapshot_invalid")
    return build_platform_state_from_health_reports(
        reports,
        previous_revision=previous_revision,
        now=now,
        stale_after=stale_after,
        supplemental_statuses=supplemental_statuses,
        supplemental_observed_at=now or datetime.now(timezone.utc),
        dependencies=dependencies,
        service_ownership=service_ownership,
    )


__all__ = [
    "build_platform_state_from_health_reports",
    "read_platform_state_from_health_registry",
]
