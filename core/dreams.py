"""
Kanyoza Dream System v1.0
==========================

Processes past decisions when the system is idle.
Like the brain consolidating memories during sleep.

DREAMS:
  - Review all decisions from the day
  - Calculate success rates
  - Update persona reliability
  - Generate insights for tomorrow
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kanyoza.dreams")


class DreamSystem:
    """
    Runs "dreams" — offline learning during idle periods.
    
    Processes past decisions, updates reliability,
    and generates insights without affecting live operations.
    """
    
    VERSION = "1.0.0"
    
    def __init__(self, container=None, council=None):
        self._container = container
        self._council = council
        self._running = False
        self._dream_task: Optional[asyncio.Task] = None
        self._dream_count = 0
        self._last_dream_time: Optional[datetime] = None
        self._insights: List[Dict[str, Any]] = []
    
    @property
    def service_name(self) -> str:
        return "dream_system"
    
    async def start(self) -> None:
        """Start dreaming (runs nightly)."""
        if self._running:
            return
        
        self._running = True
        self._dream_task = asyncio.create_task(
            self._dream_loop(),
            name="dream-loop",
        )
        logger.info("💭 Dream system started — will dream during idle hours")
    
    async def stop(self) -> None:
        """Stop dreaming."""
        self._running = False
        if self._dream_task and not self._dream_task.done():
            self._dream_task.cancel()
            try:
                await self._dream_task
            except asyncio.CancelledError:
                pass
    
    async def _dream_loop(self) -> None:
        """Main dream loop — runs every hour, checks if it's dream time."""
        while self._running:
            try:
                hour = datetime.now(timezone.utc).hour
                
                # Dream between 1 AM and 5 AM (quiet hours)
                if 1 <= hour <= 5:
                    await self._dream()
                else:
                    logger.debug("💭 Not dream time yet (hour=%d)", hour)
                
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Dream loop error: %s", exc)
            
            await asyncio.sleep(3600)  # Check every hour
    
    async def _dream(self) -> None:
        """Execute a dream — process past data and learn."""
        self._dream_count += 1
        self._last_dream_time = datetime.now(timezone.utc)
        
        logger.info("💭 Dream #%d starting...", self._dream_count)
        
        # Dream Phase 1: Review decisions
        decisions = await self._collect_today_decisions()
        
        # Dream Phase 2: Calculate success rates
        insights = self._analyze_decisions(decisions)
        
        # Dream Phase 3: Update reliability
        if self._council:
            await self._update_council_reliability(insights)
        
        # Dream Phase 4: Generate tomorrow's insights
        self._insights.extend(insights)
        self._insights = self._insights[-100:]  # Keep last 100
        
        logger.info("💭 Dream #%d complete — %d insights generated", self._dream_count, len(insights))
    
    async def _collect_today_decisions(self) -> List[Dict[str, Any]]:
        """Collect decisions from council memory, decision_gate, and bus outcomes."""
        decisions = []
        today = datetime.now(timezone.utc).date()
        ws = getattr(self, "workspace_id", None)

        # 1. Council memory (classic path)
        try:
            if self._council and hasattr(self._council, "memory"):
                records = self._council.memory.get_recent(limit=200)
                for record in records:
                    record_date = record.measured_at.date() if hasattr(record, "measured_at") else None
                    if record_date and record_date == today:
                        decisions.append({
                            "proposal_id": getattr(record, "proposal_id", None),
                            "decision": record.decision.value if hasattr(getattr(record, "decision", None), "value") else str(getattr(record, "decision", "")),
                            "actual_outcome": getattr(record, "actual_outcome", None),
                            "success_metric": getattr(record, "success_metric", None),
                            "source": "council_memory",
                        })
        except Exception as exc:
            logger.debug("Dream: council memory collect failed: %s", exc)

        # 2. DecisionGate in-process decisions
        try:
            from core.decision_gate import decision_gate
            for d in list(getattr(decision_gate, "_decisions", {}).values()):
                created = getattr(d, "created_at", None)
                if created and created.date() != today:
                    continue
                if ws and getattr(d, "workspace_id", None) not in (ws, "platform", None):
                    continue
                decisions.append({
                    "proposal_id": d.decision_id,
                    "decision": "approved" if d.approved else "rejected",
                    "actual_outcome": None,  # filled from bus if available
                    "success_metric": None,
                    "source": d.source.value if hasattr(d.source, "value") else str(d.source),
                    "action_name": d.action_name,
                    "workspace_id": d.workspace_id,
                })
        except Exception as exc:
            logger.debug("Dream: decision_gate collect failed: %s", exc)

        # 3. Bus outcomes (post.published / content.failed) → attach outcomes
        try:
            from core.unified_bus import get_bus_sync
            bus = get_bus_sync()
            if bus:
                published_corr = set()
                failed_corr = set()
                for entry in list(getattr(bus, "_event_store", []))[-500:]:
                    name = getattr(entry, "event_name", "")
                    payload = getattr(entry, "payload", {}) or {}
                    pub_at = getattr(entry, "published_at", None)
                    if pub_at and hasattr(pub_at, "date") and pub_at.date() != today:
                        continue
                    pws = payload.get("workspace_id") or (payload.get("_nerve") or {}).get("workspace_id")
                    if ws and pws and pws not in (ws, "platform"):
                        continue
                    corr = payload.get("correlation_id") or payload.get("decision_id")
                    if "published" in name:
                        if corr:
                            published_corr.add(corr)
                        decisions.append({
                            "proposal_id": payload.get("decision_id") or corr,
                            "decision": "approved",
                            "actual_outcome": "success",
                            "success_metric": 1.0,
                            "source": "bus_post_published",
                        })
                    elif "failed" in name or "fail" in name:
                        if corr:
                            failed_corr.add(corr)
                        decisions.append({
                            "proposal_id": payload.get("decision_id") or corr,
                            "decision": "approved",
                            "actual_outcome": "failure",
                            "success_metric": 0.0,
                            "source": "bus_content_failed",
                        })
                    elif "outcome" in name:
                        oc = payload.get("outcome") or "unknown"
                        sc = payload.get("score")
                        decisions.append({
                            "proposal_id": payload.get("decision_id") or corr,
                            "decision": "approved",
                            "actual_outcome": "success" if oc == "success" else ("failure" if oc == "failure" else "partial"),
                            "success_metric": sc,
                            "source": "bus_outcome_recorded",
                        })
                # Backfill outcomes onto gate decisions
                for d in decisions:
                    pid = d.get("proposal_id")
                    if d.get("actual_outcome") is None and pid:
                        if pid in published_corr:
                            d["actual_outcome"] = "success"
                        elif pid in failed_corr:
                            d["actual_outcome"] = "failure"
        except Exception as exc:
            logger.debug("Dream: bus collect failed: %s", exc)

        return decisions
    
    def _analyze_decisions(self, decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Analyze decisions and generate insights."""
        insights = []
        
        if not decisions:
            insights.append({
                "type": "no_data",
                "message": "No decisions to analyze today",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return insights
        
        # Calculate overall success rate
        successes = sum(1 for d in decisions if d.get("actual_outcome") == "success")
        total = len(decisions)
        success_rate = successes / total if total > 0 else 0
        
        insights.append({
            "type": "daily_summary",
            "message": f"Today: {successes}/{total} decisions succeeded ({success_rate:.0%})",
            "success_rate": round(success_rate, 4),
            "total_decisions": total,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        
        # Identify patterns
        approved_success = [
            d for d in decisions
            if d.get("decision") == "approved" and d.get("actual_outcome") == "success"
        ]
        approved_fail = [
            d for d in decisions
            if d.get("decision") == "approved" and d.get("actual_outcome") == "failure"
        ]
        rejected_success = [
            d for d in decisions
            if d.get("decision") in ("rejected", "blocked") and d.get("actual_outcome") == "failure"
        ]
        
        if approved_fail:
            insights.append({
                "type": "false_approval",
                "message": f"⚠️ {len(approved_fail)} approvals led to failures — Council may be too lenient",
                "count": len(approved_fail),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        
        if rejected_success:
            insights.append({
                "type": "false_rejection",
                "message": f"⚠️ {len(rejected_success)} rejections blocked successful outcomes — Council may be too strict",
                "count": len(rejected_success),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        
        if success_rate < 0.5 and total >= 5:
            insights.append({
                "type": "poor_performance",
                "message": f"🔴 Overall success rate below 50% — review Council thresholds",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        
        return insights
    
    async def _update_council_reliability(self, insights: List[Dict[str, Any]]) -> None:
        """Update Council persona reliability and config thresholds from dream analysis."""
        try:
            success_rate = None
            for insight in insights:
                if insight.get("type") == "daily_summary":
                    success_rate = insight.get("success_rate")
                if insight.get("type") == "false_approval":
                    logger.info("💭 Dream insight: Council may be too lenient — consider raising approval threshold")
                elif insight.get("type") == "false_rejection":
                    logger.info("💭 Dream insight: Council may be too strict — consider lowering rejection threshold")

            # Push reliability into personas if they expose update_reliability
            if self._council and success_rate is not None:
                personas = getattr(self._council, "_personas", None) or getattr(self._council, "personas", None)
                if isinstance(personas, dict):
                    for persona in personas.values():
                        if hasattr(persona, "update_reliability"):
                            try:
                                persona.update_reliability(float(success_rate))
                            except Exception:
                                pass
                cfg = getattr(self._council, "config", None) or getattr(self._council, "_config", None)
                if cfg is not None and success_rate is not None:
                    # Nudge thresholds gently (bounded)
                    if success_rate < 0.5 and hasattr(cfg, "approval_threshold"):
                        cfg.approval_threshold = min(0.75, float(cfg.approval_threshold) + 0.02)
                    elif success_rate > 0.8 and hasattr(cfg, "approval_threshold"):
                        cfg.approval_threshold = max(0.45, float(cfg.approval_threshold) - 0.01)

            # Publish dream.insight on the nerve for Director morning briefing
            try:
                from core.unified_bus import get_bus_sync
                bus = get_bus_sync()
                # A platform-level DreamSystem has no tenant authority.
                # Only a workspace-owned DreamSystem may publish tenant events.
                ws = getattr(self, "workspace_id", None)
                if bus and insights and isinstance(ws, str) and ws.strip():
                    await bus.publish(
                        "dream.insight",
                        {
                            "workspace_id": ws,
                            "insights": insights[-10:],
                            "dream_count": self._dream_count,
                        },
                        source="dream_system",
                        workspace_id=ws,
                    )
            except Exception:
                pass
        except Exception as exc:
            logger.debug("Dream: Failed to update reliability: %s", exc)
    
    async def run_once(self) -> Dict[str, Any]:
        """Force a single dream cycle (Director learning capability)."""
        await self._dream()
        return self.get_status()

    def get_status(self) -> Dict[str, Any]:
        """Get dream system status."""
        return {
            "version": self.VERSION,
            "running": self._running,
            "dream_count": self._dream_count,
            "last_dream_time": self._last_dream_time.isoformat() if self._last_dream_time else None,
            "insights_count": len(self._insights),
            "recent_insights": self._insights[-5:],
        }


# Global dream system
dream_system = DreamSystem()


async def init_dream_system(container=None, council=None) -> DreamSystem:
    """Initialize dream system."""
    dream_system._container = container
    dream_system._council = council
    await dream_system.start()
    return dream_system


async def shutdown_dream_system() -> None:
    """Stop dreaming."""
    await dream_system.stop()


__all__ = [
    "DreamSystem",
    "dream_system",
    "init_dream_system",
    "shutdown_dream_system",
]