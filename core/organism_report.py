"""
Sprint 5 — Weekly (or on-demand) organism report from Dreams + SLOs + outcomes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kanyoza.core.organism_report")


def build_organism_report(
    workspace_id: str,
    *,
    window_days: int = 7,
) -> Dict[str, Any]:
    ws = workspace_id or "default"
    window_hours = float(window_days) * 24.0

    from core.slo_metrics import compute_workspace_slos
    from core.decision_gate import decision_gate
    from core.organism import organism_registry

    slo = compute_workspace_slos(ws, window_hours=window_hours)
    decisions = decision_gate.list_for_workspace(ws, limit=500)
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    recent = [d for d in decisions if d.created_at >= cutoff]

    outcomes = {
        "success": sum(1 for d in recent if d.outcome == "success"),
        "failure": sum(1 for d in recent if d.outcome == "failure"),
        "partial": sum(1 for d in recent if d.outcome == "partial"),
        "unknown": sum(1 for d in recent if not d.outcome),
    }

    # Dreams insights
    dream_insights: List[Dict[str, Any]] = []
    dream_status: Dict[str, Any] = {}
    try:
        org = organism_registry.get_or_create(ws)
        dreams = org.dreams
        if dreams:
            dream_status = dreams.get_status() if hasattr(dreams, "get_status") else {}
            insights = list(getattr(dreams, "_insights", []) or [])
            dream_insights = insights[-20:]
    except Exception as exc:
        logger.debug("dream report soft: %s", exc)

    # Narrative
    rate = (slo.get("publish") or {}).get("success_rate")
    narrative_parts = [
        f"Workspace `{ws}` — status **{slo.get('status')}** over {window_days}d.",
    ]
    if rate is not None:
        narrative_parts.append(f"Publish success rate: {rate:.0%}.")
    narrative_parts.append(
        f"Outcomes: {outcomes['success']} ok, {outcomes['failure']} fail, "
        f"{outcomes['partial']} partial, {outcomes['unknown']} pending."
    )
    if slo.get("breaches"):
        narrative_parts.append(f"SLO breaches: {', '.join(slo['breaches'])}.")
    if dream_insights:
        last = dream_insights[-1]
        narrative_parts.append(f"Latest dream insight: {last.get('message') or last.get('type')}.")
    else:
        narrative_parts.append("No dream insights yet — run a learning cycle or wait for quiet hours.")

    return {
        "workspace_id": ws,
        "window_days": window_days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "narrative": " ".join(narrative_parts),
        "slo": slo,
        "outcomes": outcomes,
        "decisions_in_window": len(recent),
        "dreams": {
            "status": dream_status,
            "insights": dream_insights,
        },
        "organism": _organism_snapshot(ws),
    }


def _organism_snapshot(workspace_id: str) -> Dict[str, Any]:
    try:
        from core.organism import organism_registry
        org = organism_registry.get(workspace_id)
        if not org:
            return {}
        return {
            "plan": getattr(org.config, "plan", None),
            "stats": dict(getattr(org, "stats", {}) or {}),
            "director_authority": getattr(org.config, "director_authority", None),
        }
    except Exception:
        return {}


async def run_weekly_reports(workspace_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Build reports for listed workspaces (or known census)."""
    from core.organism import organism_registry
    from core.decision_gate import decision_gate

    if not workspace_ids:
        workspace_ids = list({d.workspace_id for d in decision_gate._decisions.values()})
        try:
            workspace_ids = list(set(workspace_ids) | set(organism_registry._organisms.keys()))
        except Exception:
            pass
    if not workspace_ids:
        workspace_ids = ["default"]

    reports = [build_organism_report(ws) for ws in workspace_ids]
    # Publish on bus for archival
    try:
        from core.unified_bus import get_bus_sync
        bus = get_bus_sync()
        if bus:
            for r in reports:
                await bus.publish(
                    "organism.weekly_report",
                    {
                        "workspace_id": r["workspace_id"],
                        "narrative": r["narrative"],
                        "slo_status": (r.get("slo") or {}).get("status"),
                        "outcomes": r.get("outcomes"),
                    },
                    source="organism_report",
                    workspace_id=r["workspace_id"],
                )
    except Exception:
        pass
    return reports
