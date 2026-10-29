"""V71.14 — Production route/event wiring convergence.

The purpose of this layer is deliberately narrow: establish one canonical
business-event admission facade for production ingress points and make route
wiring observable. It does not execute providers, grant authority, or create a
second workflow/execution engine.

Canonical event path:
  ingress route/webhook -> UnifiedEventBus -> canonical event router ->
  BusinessAutomationEngine -> existing governance/Action Kernel/outbox path.

Objective-scoped V70 operating workflows remain available through the V70
BusinessOperatingLayer; this adapter does not invent an objective/workflow when
an event has none.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
import hashlib
import json
import logging

logger = logging.getLogger("kanyoza.v71.14")


@dataclass(frozen=True)
class RouteBinding:
    name: str
    ingress: str
    transport: str
    destination: str
    canonical: bool
    notes: str = ""


class ProductionRouteEventWiring:
    VERSION = "v71.14"

    def __init__(self, *, container: Any, runtime: Any):
        if container is None or runtime is None:
            raise ValueError("container and runtime are required")
        self.container = container
        self.runtime = runtime
        self._wired = False
        self._events_admitted = 0
        self._events_rejected = 0
        self._last_event: dict[str, Any] | None = None

    @property
    def bindings(self) -> tuple[RouteBinding, ...]:
        return (
            RouteBinding("KCPG Action Intent", "KCPG", "HTTP", "Action Kernel durable outbox", True,
                         "Consequential browser commands enter through KCPG; no inline side effect."),
            RouteBinding("Business/Event API", "authenticated event routes", "UnifiedEventBus", "BusinessAutomation event bridge", True,
                         "Transport only; business automation owns matching and governed admission."),
            RouteBinding("Provider Webhooks", "provider webhook routes", "verified webhook ingress", "reconciliation/event fabric", True,
                         "Provider evidence is admitted only after signature/idempotency/tenant checks."),
            RouteBinding("Business Operating Layer", "workspace objective/workflow", "in-process", "V70 governed catalog", True,
                         "Objective-scoped orchestration; no direct provider dependency."),
            RouteBinding("Legacy AutonomousLoop", "legacy internal loop", "in-process", "legacy intelligence only", False,
                         "Must not become an independent authority/executor."),
        )

    def _engine(self):
        from services.business_automation import BusinessAutomationEngine
        engine = self.container.try_resolve(BusinessAutomationEngine)
        if engine is None:
            raise RuntimeError("BusinessAutomationEngine is not registered")
        return engine

    @staticmethod
    def _validate_event(event_name: str, event: Mapping[str, Any], workspace_id: str) -> tuple[str, dict[str, Any], str]:
        name = str(event_name or "").strip()
        ws = str(workspace_id or event.get("workspace_id") or "").strip()
        if not name or len(name) > 160:
            raise ValueError("invalid_event_name")
        if not ws or ws == "platform":
            raise ValueError("workspace_id_required")
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            raise ValueError("event_id_required")
        payload = dict(event)
        payload["event_id"] = event_id
        payload["event_name"] = name
        payload["workspace_id"] = ws
        return name, payload, ws

    async def admit_event(self, *, event_name: str, event: Mapping[str, Any], workspace_id: str,
                          approved: bool = False) -> Any:
        """Admit an event into the existing governed business automation path."""
        name, payload, ws = self._validate_event(event_name, event, workspace_id)
        try:
            # V73.1: the V65.9 handoff is the canonical intelligence-to-execution
            # admission. It must not be routed through a second business policy or
            # sales orchestration authority. The existing Action Kernel remains the
            # only consequential execution boundary.
            if name == "business.decision.action.handoff.ready":
                from services.v73_intelligence_execution_convergence import intelligence_execution_convergence
                result = [await intelligence_execution_convergence.admit(event=payload, workspace_id=ws)]
            else:
                engine = self._engine()
                result = await engine.run_event(
                    event_name=name,
                    event=payload,
                    workspace_id=ws,
                    approved=approved,
                )
            self._events_admitted += 1
            self._last_event = {
                "event_name": name,
                "workspace_id": ws,
                "event_id": payload["event_id"],
                "admitted_at": datetime.now(timezone.utc).isoformat(),
                "execution_count": len(result or ()),
            }
            return result
        except Exception:
            self._events_rejected += 1
            raise

    def wire(self) -> dict[str, Any]:
        """Install this facade as the production event admission service."""
        self._wired = True
        return self.status()

    def status(self) -> dict[str, Any]:
        material = json.dumps([b.__dict__ for b in self.bindings], sort_keys=True)
        return {
            "version": self.VERSION,
            "wired": self._wired,
            "canonical_event_admission": True,
            "parallel_execution_plane": False,
            "events_admitted": self._events_admitted,
            "events_rejected": self._events_rejected,
            "last_event": self._last_event,
            "binding_fingerprint": hashlib.sha256(material.encode()).hexdigest(),
            "bindings": [b.__dict__ for b in self.bindings],
            "execution_law": "event ingress -> UnifiedEventBus -> BusinessAutomation -> governance -> Action Kernel -> durable outbox",
        }


def install_route_event_wiring(app: Any, container: Any) -> ProductionRouteEventWiring:
    runtime = getattr(app.state, "production_runtime", None)
    if runtime is None:
        raise RuntimeError("V71.13 production runtime must be installed first")
    wiring = ProductionRouteEventWiring(container=container, runtime=runtime)
    app.state.route_event_wiring = wiring
    try:
        from services.business_automation_event_bridge import business_automation_event_bridge
        business_automation_event_bridge.set_canonical_router(wiring)
    except Exception as exc:
        logger.warning("V71.14 event bridge router could not be installed: %s", exc)
    wiring.wire()
    logger.info("V71.14 production route/event wiring installed")
    return wiring
