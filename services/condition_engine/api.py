"""
KANYOZA AUTONOMOUS CONDITION + DECISION ENGINE v5.1
====================================================

COMPLETE ASYNC-NATIVE REWRITE + ENHANCED LEARNING

Version: 5.1.0
Purpose:
    Complete autonomous decision intelligence for social-media/content systems.

v5.1 CHANGES (Additive — nothing removed):
    - ADDED: Context-aware learning (topic + content_type + style tracking)
    - ADDED: Time-of-day pattern learning (when content works best)
    - ADDED: Causal chain reasoning (why decisions succeeded/failed)
    - ADDED: Recency-weighted strategy scoring (7-day decay)
    - ADDED: Automatic strategy escalation for unreliable contexts
    - ADDED: Bayesian success tracking (per-topic priors)
    - ADDED: Duration-aware strategy ranking
    - ADDED: 7 new audit endpoints for learning introspection
    - All v5.0 features preserved

v5.0 CHANGES (Additive — nothing removed):
    - Async-native health check integration
    - Bounded collections (deque everywhere)
    - Correlation ID tracking
    - Enhanced thread safety
    - Service name property for health registry
    - Clean code organization

RETAINED FROM V4.0.1:
    - Multi-metric baselines
    - Per-topic baselines
    - Predictive trend analysis
    - Anomaly classification (SPIKE, DIP, DRIFT, LEVEL_SHIFT)
    - Root cause hypotheses
    - Brand clustering
    - Adaptive thresholds
    - Confidence calibration
    - Automated experiments with auto-deployment
    - UCB strategy selection
    - Community Council integration
    - Event bus integration
    - Supabase persistence
    - Goal tracking
    - Decision memory
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import random
import statistics
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("kanyoza.condition_engine")


# ═══════════════════════════════════════════════════════════════════════════
# COUNCIL INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

try:
    from council.integration import get_council
    HAS_COUNCIL = True
except ImportError:
    HAS_COUNCIL = False
    logger.debug("Council not available — continuing without community oversight")


# ═══════════════════════════════════════════════════════════════════════════
# EVENT BUS INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════


try:
    from core.unified_bus import get_unified_bus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False
    logger.warning("UnifiedEventBus not available — events will be skipped")


async def _publish_event_async(event_name: str, payload: Dict[str, Any]) -> None:
    """Async publish on UnifiedEventBus."""
    if not HAS_EVENT_BUS:
        return
    pl = dict(payload or {})
    pl.setdefault("workspace_id", "platform")
    try:
        bus = await get_unified_bus()
        try:
            await bus.publish(event_name, pl, source="condition_engine", workspace_id="platform")
        except TypeError:
            await bus.publish(event_name, pl)
    except Exception as exc:
        logger.debug("Condition Engine publish failed for %s: %s", event_name, exc)


from .utils import *
from .models import *
from .analysis import *
from .core import *

def default_action_executor(plan: ActionPlan) -> Dict[str, Any]:
    """Safe default: no real side effect is performed here."""
    logger.warning("No concrete action executor bound for action=%s", plan.action)
    return {
        "ok": False,
        "error": "action_executor_unavailable",
        "action": plan.action,
        "parameters": plan.parameters,
    }


default_action_executor.condition_engine_unavailable = True


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

autonomous_engine = AutonomousConditionEngine(
    config=EngineConfig(
        dry_run=False,
        allow_low_risk_autonomy=True,
        allow_medium_risk_autonomy=True,
        allow_high_risk_autonomy=False,
        allow_critical_autonomy=False,
        enable_adaptive_thresholds=True,
        enable_multi_metric=True,
        enable_predictive_trend=True,
        enable_automated_experiments=True,
        enable_brand_clustering=True,
        enable_anomaly_classification=True,
        enable_confidence_calibration=True,
        enable_context_learning=True,
        enable_time_patterns=True,
        enable_causal_chains=True,
        enable_decay_weighting=True,
    ),
    action_executor=default_action_executor,
)


# ═══════════════════════════════════════════════════════════════════════════
# CONVENIENCE API
# ═══════════════════════════════════════════════════════════════════════════

def evaluate_engagement(brand_id: str, post_id: str, likes: int = 0, comments: int = 0, shares: int = 0, clicks: int = 0, impressions: int = 0, reach: int = 0, topic: str = "", content_type: str = "", style: str = "", hook_type: str = "", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Convenience function to evaluate engagement."""
    observation = Observation(brand_id=brand_id, post_id=post_id, likes=likes, comments=comments, shares=shares, clicks=clicks, impressions=impressions, reach=reach, topic=topic, content_type=content_type, style=style, hook_type=hook_type, metadata=metadata or {})
    return autonomous_engine.observe(observation)


def get_engine_status(brand_id: Optional[str] = None) -> Dict[str, Any]:
    """Get engine status."""
    return autonomous_engine.get_status(brand_id)


def register_default_goal(brand_id: str) -> Goal:
    """Register default goal."""
    return autonomous_engine.register_goal(Goal(id=generate_id("goal"), brand_id=brand_id, name="Increase meaningful engagement", metric="engagement", target_value=120.0, baseline_value=100.0, deadline=(utc_now() + timedelta(days=30))))


def create_hook_experiment(brand_id: str) -> Experiment:
    """Create hook experiment."""
    return autonomous_engine.create_experiment(brand_id=brand_id, name="Hook Performance Experiment", variable="hook_type", control="direct_technical_hook", variant="story_based_hook")


def health_check() -> Dict[str, Any]:
    """Expose core health and capability readiness without hardcoded green status."""
    health = autonomous_engine.check_health()
    return {
        "service": "autonomous_condition_engine",
        "status": health.get("status", "unknown"),
        "version": health.get("version", "5.1.0"),
        "timestamp": iso_now(),
        "readiness": health.get("readiness", autonomous_engine.readiness_report()),
        "engine": autonomous_engine.get_status(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# EXPORTS
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    "EngineConfig",
    "Observation",
    "Baseline",
    "Trend",
    "Anomaly",
    "Goal",
    "Strategy",
    "Decision",
    "ActionPlan",
    "Experiment",
    "DecisionMemory",
    "DecisionState",
    "ActionRisk",
    "ActionStatus",
    "GoalStatus",
    "AnomalyType",
    "AutonomousConditionEngine",
    "AnomalyClassifier",
    "PredictiveTrendAnalyzer",
    "BrandClusterer",
    "AdaptiveThresholdManager",
    "ContextLearner",
    "autonomous_engine",
    "evaluate_engagement",
    "get_engine_status",
    "register_default_goal",
    "create_hook_experiment",
    "health_check",
    "time_bucket",
    "day_context",
]
