"""Read-only cross-engine interface to the canonical Platform State snapshot."""
from __future__ import annotations

from typing import Any

from core.platform_state import CapabilityState, PlatformStateSnapshot, ServiceState
from core.platform_state_store import PlatformStateStore


class PlatformStateReader:
    """Expose platform facts without exposing the store's write operation."""

    def __init__(self, service_role_client: Any) -> None:
        self._store = PlatformStateStore(service_role_client)

    async def get_snapshot(self) -> PlatformStateSnapshot | None:
        return await self._store.load(scope_type="platform", scope_id="platform")

    async def get_service(self, service_id: str) -> ServiceState | None:
        if not service_id or not service_id.strip():
            raise ValueError("service_id is required")
        snapshot = await self.get_snapshot()
        if snapshot is None:
            return None
        return next(
            (item for item in snapshot.services if item.service_id == service_id),
            None,
        )

    async def get_capability(self, capability_id: str) -> CapabilityState | None:
        if not capability_id or not capability_id.strip():
            raise ValueError("capability_id is required")
        snapshot = await self.get_snapshot()
        if snapshot is None:
            return None
        return next(
            (item for item in snapshot.capabilities if item.capability_id == capability_id),
            None,
        )


__all__ = ["PlatformStateReader"]
