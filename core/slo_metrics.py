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
    request_slos = _request_slos()

    # SLO targets are intentionally explicit and machine-readable.
    targets = {
        "publish_success_rate_min": 0.95,
        "outbox_pending_max": 50,
        "outbox_dead_max": 0,
        "bus_dead_letters_max": 10,
        "api_5xx_rate_max": 0.01,
        "api_p95_latency_ms_max": 2000.0,
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
    if request_slos.get("five_xx_rate") is not None and request_slos["five_xx_rate"] > targets["api_5xx_rate_max"]:
        breaches.append("api_5xx_rate")
    if request_slos.get("p95_ms") is not None and request_slos["p95_ms"] > targets["api_p95_latency_ms_max"]:
        breaches.append("api_p95_latency")

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
        "api": request_slos,
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


def _request_slos() -> Dict[str, Any]:
    """Return aggregate inbound API latency/error evidence from existing metrics."""
    try:
        from utils.metrics import metrics
        snapshot = metrics.get_snapshot()
        timings = snapshot.get("timings", {})
        counters = snapshot.get("counters", {})
        request_categories = [k for k in timings if k.startswith("http.server.")]
        samples = []
        requests = 0
        server_errors = 0
        for cat in request_categories:
            row = timings.get(cat) or {}
            # p95 per route is not equivalent to global p95, so retain each
            # route's summary and derive a conservative max p95 for the fleet.
            if row.get("p95_ms") is not None:
                samples.append(float(row["p95_ms"]))
            requests += int(counters.get(f"{cat}.requests", 0))
            server_errors += int(counters.get(f"{cat}.server_errors", 0))
        return {
            "available": bool(request_categories),
            "route_count": len(request_categories),
            "requests": requests,
            "server_errors": server_errors,
            "five_xx_rate": round(server_errors / requests, 6) if requests else None,
            "p95_ms": round(max(samples), 2) if samples else None,
            "note": "process-local rolling evidence; not a substitute for durable telemetry",
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)[:200]}


def _metabolism(workspace_id: str) -> Dict[str, Any]:
    try:
        from core.metabolism import metabolism
        return metabolism.status(workspace_id)
    except Exception as exc:
        return {"error": str(exc)[:200]}
