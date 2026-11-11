"""Durable publication coordinator for the canonical Platform State snapshot.

The snapshot is authoritative. Events are revision notifications only and are
published only after the compare-and-swap write succeeds.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import replace
from typing import Any

from core.platform_state import PlatformStateSnapshot
from core.platform_state_store import (
    PlatformStateConflict,
    PlatformStateStore,
    PlatformStateStoreError,
    _state_payload,
)

logger = logging.getLogger("kanyoza.platform_state.publisher")


class PlatformStatePublisher:
    """Single-writer coordinator: load -> compare -> CAS save -> notify."""

    def __init__(
        self,
        store: PlatformStateStore,
        event_bus: Any,
        *,
        max_conflict_retries: int = 3,
    ) -> None:
        if store is None or event_bus is None:
            raise ValueError("PlatformStateStore and UnifiedEventBus are required")
        if max_conflict_retries < 1 or max_conflict_retries > 5:
            raise ValueError("max_conflict_retries must be between 1 and 5")
        self._store = store
        self._event_bus = event_bus
        self._max_conflict_retries = max_conflict_retries

    @staticmethod
    def _state_signature(snapshot: PlatformStateSnapshot) -> str:
        payload = _state_payload(snapshot)
        # updated_at is projection metadata; evidence timestamps remain part
        # of the signature because they establish freshness/provenance.
        payload.pop("updated_at", None)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _changed_ids(
        previous: PlatformStateSnapshot | None,
        current: PlatformStateSnapshot,
    ) -> tuple[list[str], list[str]]:
        if previous is None:
            return (
                sorted(item.service_id for item in current.services),
                sorted(item.capability_id for item in current.capabilities),
            )
        old_services = {item.service_id: item for item in previous.services}
        new_services = {item.service_id: item for item in current.services}
        old_capabilities = {item.capability_id: item for item in previous.capabilities}
        new_capabilities = {item.capability_id: item for item in current.capabilities}
        service_ids = sorted(
            key for key in old_services.keys() | new_services.keys()
            if old_services.get(key) != new_services.get(key)
        )
        capability_ids = sorted(
            key for key in old_capabilities.keys() | new_capabilities.keys()
            if old_capabilities.get(key) != new_capabilities.get(key)
        )
        return service_ids, capability_ids

    async def publish(self, candidate: PlatformStateSnapshot) -> PlatformStateSnapshot:
        """Persist the latest facts and emit a durable revision notification.

        A notification failure never rolls back a successful snapshot write;
        consumers can recover by loading the authoritative snapshot.
        """
        if candidate.scope_type != "platform" or candidate.scope_id != "platform":
            raise ValueError("HealthMonitor may publish only platform scope")
        for attempt in range(self._max_conflict_retries):
            previous = await self._store.load(scope_type="platform", scope_id="platform")
            if previous and self._state_signature(previous) == self._state_signature(candidate):
                return previous

            expected_revision = previous.revision if previous else 0
            next_snapshot = replace(candidate, revision=expected_revision + 1)
            try:
                await self._store.save(next_snapshot, expected_revision=expected_revision)
            except PlatformStateConflict:
                if attempt + 1 >= self._max_conflict_retries:
                    raise
                continue

            changed_services, changed_capabilities = self._changed_ids(previous, next_snapshot)
            digest = self._state_signature(next_snapshot)
            payload = {
                "schema_version": next_snapshot.schema_version,
                "scope_type": "platform",
                "scope_id": "platform",
                "revision": next_snapshot.revision,
                "updated_at": next_snapshot.updated_at.isoformat(),
                "snapshot_digest": digest,
                "changed_service_ids": changed_services,
                "changed_capability_ids": changed_capabilities,
            }
            if getattr(self._event_bus, "supabase", None) is None:
                logger.error(
                    "Platform State revision %s persisted but durable event transport is unavailable; "
                    "consumers must reconcile from the snapshot store",
                    next_snapshot.revision,
                )
                return next_snapshot
            try:
                event_id = await self._event_bus.publish(
                    "platform.state.updated",
                    payload,
                    source="health_monitor",
                    workspace_id="platform",
                    correlation_id=next(
                        (
                            evidence.correlation_id
                            for item in next_snapshot.services
                            for evidence in item.evidence
                            if evidence.correlation_id
                        ),
                        None,
                    ),
                    schema_version=str(next_snapshot.schema_version),
                )
                if not event_id:
                    logger.error(
                        "Platform State revision %s persisted but notification was rejected; "
                        "consumers must reconcile from the snapshot store",
                        next_snapshot.revision,
                    )
            except Exception:
                logger.exception(
                    "Platform State revision %s persisted but notification failed",
                    next_snapshot.revision,
                )
            return next_snapshot

        raise PlatformStateConflict("platform state revision conflict retry budget exhausted")


__all__ = ["PlatformStatePublisher"]
