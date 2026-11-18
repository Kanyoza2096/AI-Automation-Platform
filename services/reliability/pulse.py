"""Pulse — collect physiological signals for diagnosis."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("kanyoza.reliability.pulse")


class PulseCollector:
    """Gathers health/evidence snapshots without mutating state."""

    def __init__(self, container=None):
        self._container = container

    async def collect(self) -> Dict[str, Any]:
        evidence: Dict[str, Any] = {
            "circulation": None,
            "meta": None,
            "health_registry": None,
            "services": {},
        }

        # DFMO / circulation
        try:
            from core.circulation_monitor import circulation_monitor
            if hasattr(circulation_monitor, "get_status"):
                evidence["circulation"] = circulation_monitor.get_status()
            elif hasattr(circulation_monitor, "_snapshots") and circulation_monitor._snapshots:
                snap = circulation_monitor._snapshots[-1]
                evidence["circulation"] = {
                    "flow_score": getattr(snap, "flow_score", None),
                    "blockages": len(getattr(snap, "active_blockages", []) or []),
                }
        except Exception as exc:
            evidence["circulation"] = {"error": str(exc)[:120]}

        # Meta engine
        try:
            if self._container:
                from services.meta_engine import MetaEngineV7
                meta = self._container.try_resolve(MetaEngineV7)
            else:
                meta = None
            if meta is None:
                try:
                    from services import meta_engine as me
                    meta = getattr(me, "meta_engine", None)
                except Exception:
                    meta = None
            if meta and hasattr(meta, "check_health_async"):
                evidence["meta"] = await meta.check_health_async()
            elif meta and hasattr(meta, "_current_snapshot") and meta._current_snapshot:
                s = meta._current_snapshot
                evidence["meta"] = {
                    "overall": getattr(getattr(s, "overall_status", None), "value", None),
                    "health_score": getattr(s, "health_score", None),
                    "critical_alerts": getattr(s, "critical_alerts", 0),
                    "active_alerts": getattr(s, "active_alerts", 0),
                }
        except Exception as exc:
            evidence["meta"] = {"error": str(exc)[:120]}

        # Lifecycle health registry
        try:
            from lifecycle import health_registry
            import asyncio
            reports = health_registry.check_all()
            if asyncio.iscoroutine(reports):
                reports = await reports
            evidence["health_registry"] = {
                name: {
                    "status": getattr(getattr(r, "status", None), "value", str(getattr(r, "status", ""))),
                    "error": getattr(r, "error_message", None),
                    # HealthReport.metadata carries the provider's actual
                    # evidence. Dropping it here reduced diagnosis to a label
                    # such as "job_service=degraded".
                    "metadata": (
                        getattr(r, "metadata", None)
                        if isinstance(getattr(r, "metadata", None), dict)
                        else {}
                    ),
                }
                for name, r in (reports or {}).items()
            }
        except Exception as exc:
            evidence["health_registry"] = {"error": str(exc)[:120]}

        # Quick service flags
        try:
            from config.settings import settings
            evidence["services"] = {
                "gemini_configured": bool(getattr(settings, "gemini_key", None) or getattr(settings, "gemini_configured", False)),
                "supabase_enabled": bool(getattr(settings, "supabase_enabled", False)),
                "redis_configured": bool(getattr(settings, "redis_configured", False)),
            }
        except Exception:
            pass

        return evidence
