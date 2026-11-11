"""Service-role adapter for versioned Platform State snapshots.

Reads are workspace/scope-keyed. Writes use the database compare-and-swap RPC;
there is deliberately no direct table write path.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from core.platform_state import (
    CapabilityState,
    EvidenceRef,
    PlatformStateSnapshot,
    Readiness,
    ServiceState,
)


class PlatformStateConflict(RuntimeError):
    """Another writer advanced the snapshot revision."""


class PlatformStateStoreError(RuntimeError):
    """The shared state snapshot could not be safely loaded or persisted."""


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("platform state timestamps must be timezone-aware")
    return value.isoformat()


def _state_payload(snapshot: PlatformStateSnapshot) -> dict[str, Any]:
    return {
        "schema_version": snapshot.schema_version,
        "updated_at": _iso(snapshot.updated_at),
        "services": [
            {
                "service_id": item.service_id,
                "owner": item.owner,
                "readiness": item.readiness.value,
                "observed_at": _iso(item.observed_at) if item.observed_at else None,
                "source_version": item.source_version,
                "dependencies": list(item.dependencies),
                "capabilities": list(item.capabilities),
                "evidence": [
                    {
                        "source": evidence.source,
                        "observed_at": _iso(evidence.observed_at),
                        "source_version": evidence.source_version,
                        "correlation_id": evidence.correlation_id,
                        "reference": evidence.reference,
                    }
                    for evidence in item.evidence
                ],
                "reason": item.reason,
            }
            for item in snapshot.services
        ],
        "capabilities": [
            {
                "capability_id": item.capability_id,
                "owner_service": item.owner_service,
                "readiness": item.readiness.value,
                "observed_at": _iso(item.observed_at) if item.observed_at else None,
                "reason": item.reason,
            }
            for item in snapshot.capabilities
        ],
    }


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("persisted timestamp is not timezone-aware")
    return parsed


def _snapshot_from_row(row: Mapping[str, Any]) -> PlatformStateSnapshot:
    state = row.get("state")
    if not isinstance(state, Mapping):
        raise PlatformStateStoreError("persisted_platform_state_invalid")
    services = []
    for item in state.get("services", []):
        evidence = tuple(
            EvidenceRef(
                source=str(e["source"]),
                observed_at=_datetime(e["observed_at"]),
                source_version=str(e["source_version"]),
                correlation_id=e.get("correlation_id"),
                reference=e.get("reference"),
            )
            for e in item.get("evidence", [])
        )
        services.append(ServiceState(
            service_id=str(item["service_id"]),
            owner=str(item["owner"]),
            readiness=Readiness(str(item["readiness"])),
            observed_at=_datetime(item.get("observed_at")),
            source_version=str(item["source_version"]),
            dependencies=tuple(item.get("dependencies", [])),
            capabilities=tuple(item.get("capabilities", [])),
            evidence=evidence,
            reason=item.get("reason"),
        ))
    capabilities = tuple(
        CapabilityState(
            capability_id=str(item["capability_id"]),
            owner_service=str(item["owner_service"]),
            readiness=Readiness(str(item["readiness"])),
            observed_at=_datetime(item.get("observed_at")),
            reason=item.get("reason"),
        )
        for item in state.get("capabilities", [])
    )
    return PlatformStateSnapshot(
        scope_type=str(row["scope_type"]),
        scope_id=str(row["scope_id"]),
        revision=int(row["revision"]),
        schema_version=int(row.get("schema_version") or state.get("schema_version") or 1),
        updated_at=_datetime(row.get("updated_at") or state.get("updated_at")),
        services=tuple(services),
        capabilities=capabilities,
    )


class PlatformStateStore:
    """Durable state adapter; requires a backend service-role client."""

    def __init__(self, service_role_client: Any) -> None:
        if service_role_client is None:
            raise ValueError("service-role client is required")
        self._client = service_role_client

    async def load(self, *, scope_type: str = "platform", scope_id: str = "platform") -> PlatformStateSnapshot | None:
        return await asyncio.to_thread(self._load_sync, scope_type, scope_id)

    def _load_sync(self, scope_type: str, scope_id: str) -> PlatformStateSnapshot | None:
        try:
            result = (
                self._client.table("platform_state_snapshots")
                .select("scope_type,scope_id,revision,schema_version,state,updated_at")
                .eq("scope_type", scope_type)
                .eq("scope_id", scope_id)
                .limit(1)
                .execute()
            )
            rows = list(getattr(result, "data", None) or [])
            if not rows:
                return None
            return _snapshot_from_row(rows[0])
        except PlatformStateStoreError:
            raise
        except Exception as exc:
            raise PlatformStateStoreError("platform state load failed") from exc

    async def save(self, snapshot: PlatformStateSnapshot, *, expected_revision: int | None = None) -> int:
        expected = snapshot.revision - 1 if expected_revision is None else expected_revision
        if expected < 0 or snapshot.revision != expected + 1:
            raise ValueError("snapshot revision must be exactly expected_revision + 1")
        return await asyncio.to_thread(self._save_sync, snapshot, expected)

    def _save_sync(self, snapshot: PlatformStateSnapshot, expected_revision: int) -> int:
        try:
            result = self._client.rpc(
                "write_platform_state_snapshot",
                {
                    "p_scope_type": snapshot.scope_type,
                    "p_scope_id": snapshot.scope_id,
                    "p_expected_revision": expected_revision,
                    "p_state": _state_payload(snapshot),
                },
            ).execute()
            data = getattr(result, "data", None)
            if isinstance(data, list) and data:
                data = data[0]
            if isinstance(data, Mapping):
                data = data.get("write_platform_state_snapshot")
            if data is None:
                raise PlatformStateStoreError("platform state write returned no revision")
            revision = int(data)
            if revision != snapshot.revision:
                raise PlatformStateStoreError("platform state write revision mismatch")
            return revision
        except PlatformStateStoreError:
            raise
        except Exception as exc:
            message = str(exc).lower()
            if "platform_state_revision_conflict" in message or "40001" in message:
                raise PlatformStateConflict("platform state revision conflict") from exc
            raise PlatformStateStoreError("platform state persistence failed") from exc


__all__ = ["PlatformStateStore", "PlatformStateConflict", "PlatformStateStoreError"]
