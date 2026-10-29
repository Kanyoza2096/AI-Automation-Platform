"""
KANYOZA META ENGINE v7.1 — FULLY POLISHED (COMPLETE + FIXED + INTELLIGENT)
============================================================================

RESOURCE-SAFE INTELLIGENT SUPERVISOR — ASYNC-NATIVE

V7.1 CHANGES (Additive — nothing removed):
  - ADDED: Context-aware health learning (engine + time + resource-mode)
  - ADDED: Seasonal pattern detection (learns which engines fail when)
  - ADDED: Causal chain reasoning (root dependencies with confidence)
  - ADDED: Decay-weighted confidence (recent failures weigh more)
  - ADDED: Adaptive recovery (learns which fixes work)
  - ADDED: Recovery effectiveness tracking
  - ADDED: 7 new introspection endpoints
  - ADDED: Enhanced get_status with learning insights

V7.0.3 CHANGES:
  - FIXED: Topology drift false positives for external services (supabase, redis, etc.)
  - FIXED: Deferred topology validation waits 15s for all services to register
  - ADDED: External service filtering in topology validation
  - ADDED: Filtered topology drift in get_impact_assessment
  - FIXED: Optional plugin services no longer trigger false drift warnings
  - FIXED: Self-services (meta_engine, director, guardian) excluded from drift

V7.0.2 CHANGES:
  - FIXED: _ping_engine_inner returns False for unknown engines (not True)
  - FIXED: check_health_async returns "unknown" on exception (not "degraded")
  - FIXED: WARNING alerts now sent to dashboard
  - FIXED: OFFLINE infra services alert even with 0 errors
  - FIXED: Recovery handler handles async stop/start properly
  - FIXED: Added missing _monitoring_loop_sync method
  - FIXED: _send_telegram_alert indentation and placement
  - FIXED: asyncio.create_task called outside event loop - now uses run_coroutine_threadsafe
  - FIXED: StatePersistence._serialize references _topology_drift - now uses meta_engine_ref
  - ADDED: Synchronous health check methods for MetaEngineV7 self-monitoring
  - FIXED: Self-referential monitoring excluded from registration
  - FIXED: Improved ping logic for services without standard health methods
  - ADDED: _is_service_alive method for better health detection
"""

from __future__ import annotations

import asyncio
import dataclasses
import atexit
import hashlib
import inspect
import json
import logging
import math
import os
import random
import statistics
import threading
import time
import traceback
import uuid

from collections import defaultdict, deque
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from statistics import mean, median, pstdev
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger("kanyoza.meta_engine")

# ═══════════════════════════════════════════════════════════════════════════
# EVENT BUS INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

try:
    from core.unified_bus import get_unified_bus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False


async def _publish_event_async(event_name: str, payload: Dict[str, Any]) -> None:
    """Async publish on UnifiedEventBus."""
    if not HAS_EVENT_BUS:
        return
    pl = dict(payload or {})
    pl.setdefault("workspace_id", "platform")
    try:
        bus = await get_unified_bus()
        try:
            await bus.publish(event_name, pl, source="meta_engine", workspace_id="platform")
        except TypeError:
            await bus.publish(event_name, pl)
    except Exception as exc:
        logger.debug("Meta Engine publish failed for %s: %s", event_name, exc)


from .models import EngineStatus, ResourceMode

class HealthPatternLearner:
    """
    Learns WHEN each engine tends to fail or degrade.
    
    Provides:
      - Time-of-day failure patterns
      - Resource-mode correlation
      - Recovery effectiveness tracking
      - Decay-weighted success rates
    """
    
    def __init__(self, decay_days: int = 7):
        self.decay_days = decay_days
        
        # Per-engine per-hour failure counts
        # engine_hour_stats[engine][hour] = {"failures": N, "checks": M}
        self.engine_hour_stats: Dict[str, Dict[int, Dict[str, int]]] = defaultdict(
            lambda: defaultdict(lambda: {"failures": 0, "checks": 0})
        )
        
        # Per-engine per-resource-mode failure counts
        self.engine_resource_stats: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(
            lambda: defaultdict(lambda: {"failures": 0, "checks": 0})
        )
        
        # Recovery effectiveness: which action worked for which engine
        # recovery_effectiveness[engine][action] = {"successes": N, "failures": M}
        self.recovery_effectiveness: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(
            lambda: defaultdict(lambda: {"successes": 0, "failures": 0})
        )
        
        # Recent outcomes per engine for decay-weighted rates
        self.recent_outcomes: Dict[str, Deque[Dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=100)
        )
        
        self._lock = threading.RLock()
    
    def record_check(
        self,
        engine: str,
        status: EngineStatus,
        resource_mode: ResourceMode,
        dt: Optional[datetime] = None,
    ) -> None:
        """Record a health check outcome."""
        dt = dt or datetime.now(timezone.utc)
        hour = dt.hour
        is_failure = status in (EngineStatus.DEGRADED, EngineStatus.OFFLINE)
        
        with self._lock:
            # Time-of-day stats
            hour_stats = self.engine_hour_stats[engine][hour]
            hour_stats["checks"] += 1
            if is_failure:
                hour_stats["failures"] += 1
            
            # Resource-mode stats
            mode_key = resource_mode.value
            res_stats = self.engine_resource_stats[engine][mode_key]
            res_stats["checks"] += 1
            if is_failure:
                res_stats["failures"] += 1
            
            # Recent outcomes for decay
            self.recent_outcomes[engine].append({
                "status": status.value,
                "is_failure": is_failure,
                "hour": hour,
                "resource_mode": mode_key,
                "timestamp": dt.isoformat(),
            })
    
    def record_recovery(
        self,
        engine: str,
        action: str,
        success: bool,
        dt: Optional[datetime] = None,
    ) -> None:
        """Record whether a recovery action worked."""
        dt = dt or datetime.now(timezone.utc)
        with self._lock:
            stats = self.recovery_effectiveness[engine][action]
            if success:
                stats["successes"] += 1
            else:
                stats["failures"] += 1
    
    def get_risky_hours(self, engine: str, threshold: float = 0.3) -> List[int]:
        """Get hours where this engine tends to fail more often."""
        stats = self.engine_hour_stats.get(engine, {})
        risky = []
        for hour, data in stats.items():
            if data["checks"] < 5:
                continue
            failure_rate = data["failures"] / data["checks"]
            if failure_rate >= threshold:
                risky.append(hour)
        return sorted(risky)
    
    def get_time_risk(self, engine: str, dt: Optional[datetime] = None) -> float:
        """Get failure probability for current hour (0-1)."""
        dt = dt or datetime.now(timezone.utc)
        hour = dt.hour
        stats = self.engine_hour_stats.get(engine, {}).get(hour)
        if not stats or stats["checks"] < 3:
            return 0.0
        return stats["failures"] / stats["checks"]
    
    def get_recent_failure_rate(self, engine: str, days: Optional[int] = None) -> Optional[float]:
        """Decay-weighted failure rate."""
        days = days or self.decay_days
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        
        outcomes = self.recent_outcomes.get(engine)
        if not outcomes:
            return None
        
        recent = []
        for o in outcomes:
            try:
                ts = datetime.fromisoformat(o["timestamp"].replace("Z", "+00:00"))
                if ts >= cutoff:
                    recent.append(o)
            except Exception:
                continue
        
        if not recent:
            return None
        
        failures = sum(1 for o in recent if o["is_failure"])
        return failures / len(recent)
    
    def get_best_recovery_action(self, engine: str) -> Optional[str]:
        """Get the recovery action with highest success rate for this engine."""
        actions = self.recovery_effectiveness.get(engine, {})
        if not actions:
            return None
        
        best_action = None
        best_rate = -1.0
        for action, stats in actions.items():
            total = stats["successes"] + stats["failures"]
            if total < 2:
                continue
            rate = stats["successes"] / total
            if rate > best_rate:
                best_rate = rate
                best_action = action
        
        return best_action
    
    def get_recovery_stats(self, engine: str) -> Dict[str, Any]:
        """Get recovery effectiveness stats for an engine."""
        actions = self.recovery_effectiveness.get(engine, {})
        return {
            action: {
                "successes": stats["successes"],
                "failures": stats["failures"],
                "success_rate": round(stats["successes"] / max(stats["successes"] + stats["failures"], 1), 2),
                "total": stats["successes"] + stats["failures"],
            }
            for action, stats in actions.items()
        }
    
    def get_learning_report(self) -> Dict[str, Any]:
        """Full learning report."""
        return {
            "engines_tracked": len(self.engine_hour_stats),
            "recovery_patterns_tracked": len(self.recovery_effectiveness),
            "engines_with_risky_hours": sum(
                1 for engine in self.engine_hour_stats if self.get_risky_hours(engine)
            ),
            "top_risky_engines": sorted(
                [
                    {
                        "engine": engine,
                        "recent_failure_rate": self.get_recent_failure_rate(engine),
                        "risky_hours": self.get_risky_hours(engine),
                    }
                    for engine in self.engine_hour_stats
                    if self.get_recent_failure_rate(engine) is not None
                ],
                key=lambda x: x["recent_failure_rate"] or 0,
                reverse=True,
            )[:10],
        }


# ═══════════════════════════════════════════════════════════════════════════
# UTILITIES (Retained)
# ═══════════════════════════════════════════════════════════════════════════
