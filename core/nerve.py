"""
Phase A/B — Nerve contract.

Every event on the organism nervous system must carry:
  - workspace_id  (or "platform" for species-level events)
  - correlation_id
  - source

Species-level event names (heartbeat of workers, meta infra) may use
workspace_id="platform". All tenant activity must use a real workspace id.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("kanyoza.core.nerve")

PLATFORM_WORKSPACE = "platform"

# Events that are allowed to use workspace_id="platform"
# Platform / infrastructure signals — not tenant-scoped.
SPECIES_EVENT_PREFIXES = (
    "species.",
    "meta.",
    "worker.",
    "heartbeat.platform",
    "heartbeat.system",      # system pulse (HeartbeatPublisher)
    "heartbeat.beat",        # canonical nerve form
    "service_heartbeat",     # legacy meta/service pulse
    "engine_alert",          # organism-level engine status
    "engine_status_update",
    "chaos.",
    "system.",
    "universal.",
    "reliability.",
    "trend.",
    "circulation.",
)


@dataclass
class NerveEnvelope:
    """Canonical envelope for UnifiedEventBus publications."""

    event_name: str
    workspace_id: str
    source: str
    event_id: Optional[str] = None
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    payload: Dict[str, Any] = field(default_factory=dict)
    brand_id: Optional[str] = None
    decision_id: Optional[str] = None
    parent_event_id: Optional[str] = None
    schema_version: str = "1.0"
    published_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_payload(self) -> Dict[str, Any]:
        """Payload handed to handlers — always includes nerve fields."""
        body = dict(self.payload)
        body["_nerve"] = {
            "event_id": self.event_id,
            "event_name": self.event_name,
            "workspace_id": self.workspace_id,
            "correlation_id": self.correlation_id,
            "source": self.source,
            "brand_id": self.brand_id,
            "decision_id": self.decision_id,
            "parent_event_id": self.parent_event_id,
            "schema_version": self.schema_version,
            "published_at": self.published_at.isoformat(),
        }
        # Also top-level for easy access / legacy handlers
        body.setdefault("workspace_id", self.workspace_id)
        body.setdefault("correlation_id", self.correlation_id)
        if self.brand_id:
            body.setdefault("brand_id", self.brand_id)
        if self.decision_id:
            body.setdefault("decision_id", self.decision_id)
        return body


class NerveContractError(ValueError):
    """Raised when an event violates the nerve contract."""


def is_species_event(event_name: str) -> bool:
    return any(event_name.startswith(p) for p in SPECIES_EVENT_PREFIXES)


def validate_and_normalize(
    event_name: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    source: str = "unknown",
    event_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    brand_id: Optional[str] = None,
    decision_id: Optional[str] = None,
    parent_event_id: Optional[str] = None,
    schema_version: str = "1.0",
    strict: bool = True,
) -> NerveEnvelope:
    """
    Build a valid NerveEnvelope or raise NerveContractError.

    strict=True (default for new code): missing workspace_id is an error
    unless the event is species-scoped.
    """
    payload = dict(payload or {})
    # Treat malformed legacy metadata as absent rather than allowing a string,
    # list, or null value to crash normalization for the entire event.
    nerve_metadata = payload.get("_nerve")
    if not isinstance(nerve_metadata, dict):
        nerve_metadata = {}

    # Prefer explicit args, then payload fields
    ws = workspace_id or payload.get("workspace_id") or nerve_metadata.get("workspace_id")
    corr = (
        correlation_id
        or payload.get("correlation_id")
        or nerve_metadata.get("correlation_id")
        or uuid.uuid4().hex
    )
    brand = brand_id or payload.get("brand_id")
    decision = decision_id or payload.get("decision_id")
    src = source or payload.get("source") or "unknown"

    if not ws:
        # Bind from trusted request/worker context when present
        try:
            from core.workspace_context import get_workspace_context
            ctx = get_workspace_context()
            if ctx and getattr(ctx, "workspace_id", None):
                ws = ctx.workspace_id
        except Exception:
            pass

    if not ws:
        if is_species_event(event_name):
            ws = PLATFORM_WORKSPACE
        elif strict:
            raise NerveContractError(
                f"Event '{event_name}' missing workspace_id — "
                "every organ event must name its organism "
                "(pass workspace_id or bind WorkspaceContext)"
            )
        else:
            logger.warning(
                "Nerve: event '%s' published without workspace_id (legacy)",
                event_name,
            )
            ws = "unbound"

    # Never treat empty / unbound as tenant isolation in strict mode
    if strict and str(ws) in ("", "unbound", "default") and not is_species_event(event_name):
        # Strict tenant events must never use synthetic or legacy workspace IDs.
        # A caller-supplied "default" is not proof of authenticated workspace binding.
        raise NerveContractError(
            f"Event '{event_name}' has invalid workspace_id={ws!r}"
        )

    if ws != PLATFORM_WORKSPACE and is_species_event(event_name):
        # species events should stay platform-scoped
        pass

    return NerveEnvelope(
        event_name=event_name,
        workspace_id=str(ws),
        source=str(src),
        event_id=str(event_id) if event_id else None,
        correlation_id=str(corr),
        payload=payload,
        brand_id=str(brand) if brand else None,
        decision_id=str(decision) if decision else None,
        parent_event_id=(
            parent_event_id
            or payload.get("parent_event_id")
            or nerve_metadata.get("parent_event_id")
        ),
        schema_version=str(schema_version or "1.0"),
    )


def workspace_from_payload(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    if not payload:
        return None
    if "workspace_id" in payload:
        return payload.get("workspace_id")
    nerve = payload.get("_nerve")
    if not isinstance(nerve, dict):
        return None
    return nerve.get("workspace_id")
