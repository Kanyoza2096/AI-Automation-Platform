"""
Sprint 4 — Service Level Objectives for the organism.

Metrics are process-local (decision_gate, outbox, bus). When Supabase is
wired, callers can merge DB counters later.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kanyoza.core.slo_metrics")


def compute_workspace_slos(workspace_id: str, *, window_hours: float = 24.0) -> Dict[str, Any]:
    ws = workspace_id or "default"
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    decisions = _decisions_in_window(ws, cutoff)
    publish_decisions = [
        d for d in decisions
        if (d.action_name or "").startswith("publish") or d.action_name == "publish_content"
    ]

    with_outcome = [d for d in publish_decisions if d.outcome]
    successes = [d for d in with_outcome if d.outcome == "success"]
    failures = [d for d in with_outcome if d.outcome == "failure"]
    partials = [d for d in with_outcome if d.outcome == "partial"]

    outcome_n = len(with_outcome) or 1
    publish_success_rate = len(successes) / outcome_n if with_outcome else None
    publish_failure_rate = len(failures) / outcome_n if with_outcome else None

    outbox = _outbox_depth(ws)
    bus = _bus_slos()
    metabolism = _metabolism(ws)

    # SLO targets (elite defaults)
    targets = {
        "publish_success_rate_min": 0.95,
        "outbox_pending_max": 50,
        "outbox_dead_max": 0,
        "bus_dead_letters_max": 10,
    }

    breaches: List[str] = []
    if publish_success_rate is not None and publish_success_rate < targets["publish_success_rate_min"]:
        breaches.append("publish_success_rate")
    if outbox["pending"] > targets["outbox_pending_max"]:
        breaches.append("outbox_pending")
    if outbox["dead"] > targets["outbox_dead_max"]:
        breaches.append("outbox_dead")
    if bus.get("dead_letters", 0) > targets["bus_dead_letters_max"]:
        breaches.append("bus_dead_letters")

    status = "healthy" if not breaches else ("degraded" if len(breaches) < 3 else "critical")

    return {
        "workspace_id": ws,
        "window_hours": window_hours,
        "at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "breaches": breaches,
        "targets": targets,
        "publish": {
            "decisions": len(publish_decisions),
            "with_outcome": len(with_outcome),
            "successes": len(successes),
            "failures": len(failures),
            "partials": len(partials),
            "success_rate": round(publish_success_rate, 4) if publish_success_rate is not None else None,
            "failure_rate": round(publish_failure_rate, 4) if publish_failure_rate is not None else None,
        },
        "outbox": outbox,
        "bus": bus,
        "metabolism": metabolism,
        "decisions_total_in_window": len(decisions),
    }


def compute_platform_slos(*, window_hours: float = 24.0) -> Dict[str, Any]:
    """Species-level rollup across in-memory decisions + global outbox/bus."""
    from core.decision_gate import decision_gate
    from core.publish_outbox import publish_outbox

    workspaces = set()
    for d in decision_gate._decisions.values():
        workspaces.add(d.workspace_id)
    with publish_outbox._lock:
        for i in publish_outbox._mem.values():
            workspaces.add(i.workspace_id)

    per = {ws: compute_workspace_slos(ws, window_hours=window_hours) for ws in workspaces}
    if not per:
        per = {"default": compute_workspace_slos("default", window_hours=window_hours)}

    statuses = [v["status"] for v in per.values()]
    if any(s == "critical" for s in statuses):
        overall = "critical"
    elif any(s == "degraded" for s in statuses):
        overall = "degraded"
    else:
        overall = "healthy"

    bus = _bus_slos()
    with publish_outbox._lock:
        items = list(publish_outbox._mem.values())
    global_outbox = {
        "pending": sum(1 for i in items if i.status == "pending"),
        "processing": sum(1 for i in items if i.status == "processing"),
        "published": sum(1 for i in items if i.status == "published"),
        "failed": sum(1 for i in items if i.status == "failed"),
        "dead": sum(1 for i in items if i.status == "dead"),
        "depth": sum(1 for i in items if i.status in ("pending", "processing", "failed")),
    }

    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "window_hours": window_hours,
        "status": overall,
        "workspace_count": len(per),
        "workspaces": per,
        "global_outbox": global_outbox,
        "bus": bus,
    }


def _decisions_in_window(workspace_id: str, cutoff: datetime) -> list:
    from core.decision_gate import decision_gate
    rows = []
    for d in decision_gate._decisions.values():
        if d.workspace_id != workspace_id:
            continue
        if d.created_at and d.created_at >= cutoff:
            rows.append(d)
    return rows


def _outbox_depth(workspace_id: str) -> Dict[str, int]:
    from core.publish_outbox import publish_outbox
    items = publish_outbox.list_for_workspace(workspace_id, limit=5000)
    return {
        "pending": sum(1 for i in items if i.status == "pending"),
        "processing": sum(1 for i in items if i.status == "processing"),
        "published": sum(1 for i in items if i.status == "published"),
        "failed": sum(1 for i in items if i.status == "failed"),
        "dead": sum(1 for i in items if i.status == "dead"),
        "depth": sum(1 for i in items if i.status in ("pending", "processing", "failed")),
    }


def _bus_slos() -> Dict[str, Any]:
    try:
        from core.unified_bus import get_bus_sync
        bus = get_bus_sync()
        if not bus:
            return {"available": False}
        stats = bus.get_stats() if hasattr(bus, "get_stats") else {}
        return {
            "available": True,
            "dead_letters": stats.get("dead_letters", len(getattr(bus, "_dead_letters", []))),
            "event_store_size": stats.get("event_store_size", len(getattr(bus, "_event_store", []))),
            "handler_errors": stats.get("handler_errors", 0),
            "circuit_state": stats.get("circuit_state"),
            "active_correlations": stats.get("active_correlations", 0),
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)[:200]}


def _metabolism(workspace_id: str) -> Dict[str, Any]:
    try:
        from core.metabolism import metabolism
        return metabolism.status(workspace_id)
    except Exception as exc:
        return {"error": str(exc)[:200]}
