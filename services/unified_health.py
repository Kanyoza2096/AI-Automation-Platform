"""
Unified health model — one place that answers:

  Can the platform accomplish intended work, and prove it?

Dimensions (0-100):
  infrastructure  — DFMO infra score / services
  event_schema    — workspace-aware events / probe ack
  workflow        — path probes (publish dry-run, db read, …)
  autonomous      — Meta overall + Director running
  content         — recent trend/content activity signal

overall = weighted blend
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("kanyoza.unified_health")

WEIGHTS = {
    "infrastructure": 0.30,
    "event_schema": 0.15,
    "workflow": 0.25,
    "autonomous": 0.15,
    "content": 0.15,
}


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, float(x)))


async def collect_unified_health(container=None) -> Dict[str, Any]:
    dims: Dict[str, float] = {
        "infrastructure": 50.0,
        "event_schema": 50.0,
        "workflow": 50.0,
        "autonomous": 50.0,
        "content": 50.0,
    }
    details: Dict[str, Any] = {}

    # DFMO
    try:
        from core.circulation_monitor import dfmo
        st = dfmo.get_status() if dfmo else {}
        dims["infrastructure"] = float(st.get("infrastructure_score", st.get("flow_score", 50)))
        dims["event_schema"] = float(st.get("event_schema_score", 50))
        dims["workflow"] = float(st.get("workflow_score", 50))
        details["dfmo"] = {
            "flow_score": st.get("flow_score"),
            "probe_acked": st.get("probe_acked"),
            "blockages": st.get("active_blockages"),
            "path_probes": st.get("path_probes"),
        }
    except Exception as exc:
        details["dfmo_error"] = str(exc)[:120]

    # Meta / Director
    try:
        from services.meta_engine import meta_engine
        if meta_engine and hasattr(meta_engine, "check_health_async"):
            mh = await meta_engine.check_health_async()
            details["meta"] = mh if isinstance(mh, dict) else {"raw": str(mh)[:200]}
            status = str((mh or {}).get("status", "")).lower()
            overall = str((mh or {}).get("overall", "")).lower()
            if status in ("healthy", "ok", "nominal") or overall == "nominal":
                dims["autonomous"] = 90.0
            elif status in ("degraded",) or overall == "degraded":
                dims["autonomous"] = 55.0
            elif status in ("starting",):
                dims["autonomous"] = 70.0
            else:
                dims["autonomous"] = 40.0
    except Exception as exc:
        details["meta_error"] = str(exc)[:120]

    try:
        from services.director import director
        if director is not None:
            running = bool(getattr(director, "running", False) or getattr(director, "_running", False))
            details["director_running"] = running
            if running:
                dims["autonomous"] = min(100.0, dims["autonomous"] + 10)
            else:
                dims["autonomous"] = max(0.0, dims["autonomous"] - 20)
    except Exception as exc:
        details["director_error"] = str(exc)[:120]

    # Content activity (published posts in the last 24h soft signal)
    try:
        from services.supabase import get_service_client_from_settings
        client = get_service_client_from_settings()
        if client:
            from datetime import timedelta
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            try:
                posts = (
                    client.table("posts")
                    .select("id")
                    .gte("created_at", since)
                    .limit(20)
                    .execute()
                )
                n = len(posts.data or [])
                dims["content"] = min(100.0, 40.0 + n * 3)
                details["posts_24h"] = n
            except Exception as post_error:
                details["posts_error"] = str(post_error)[:120]
    except Exception as exc:
        details["content_error"] = str(exc)[:120]

    overall = 0.0
    for k, w in WEIGHTS.items():
        overall += _clamp(dims[k]) * w
    overall = round(_clamp(overall), 1)
    for k in dims:
        dims[k] = round(_clamp(dims[k]), 1)

    return {
        "ok": True,
        "overall": overall,
        "dimensions": dims,
        "weights": WEIGHTS,
        "details": details,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "message": (
            "Platform workflow healthy"
            if overall >= 80
            else "Platform partially healthy — inspect dimensions"
            if overall >= 55
            else "Platform health weak — inspect blockages and content pipeline"
        ),
    }
