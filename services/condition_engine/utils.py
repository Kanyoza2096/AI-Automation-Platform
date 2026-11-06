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
    """Publish with the originating workspace and causal execution context."""
    if not HAS_EVENT_BUS:
        return
    pl = dict(payload or {})
    workspace_id = str(pl.get("workspace_id") or "platform")
    pl["workspace_id"] = workspace_id
    publish_kwargs = {"source": "condition_engine", "workspace_id": workspace_id}
    for field_name in (
        "correlation_id", "parent_event_id", "brand_id", "decision_id",
        "execution_id", "intent_id",
    ):
        value = pl.get(field_name)
        if value not in (None, ""):
            publish_kwargs[field_name] = str(value)
    try:
        bus = await get_unified_bus()
        try:
            await bus.publish(event_name, pl, **publish_kwargs)
        except TypeError:
            # Compatibility with legacy bus adapters; causal data remains in payload.
            await bus.publish(event_name, pl)
    except Exception as exc:
        logger.warning("Condition Engine publish failed for %s: %s", event_name, exc)


def _publish_event(event_name: str, payload: Dict[str, Any]) -> None:
    """Publish event — Condition Engine is async-native."""
    if not HAS_EVENT_BUS:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_publish_event_async(event_name, payload))
    except RuntimeError:
        logger.debug("Condition Engine: no running loop, skipping publish %s", event_name)
# ═══════════════════════════════════════════════════════════════════════════
# TIME HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def utc_now() -> datetime:
    """Current UTC time."""
    return datetime.now(timezone.utc)


def iso_now() -> str:
    """Current UTC time as ISO string."""
    return utc_now().isoformat()


def parse_datetime(value: Any) -> Optional[datetime]:
    """Parse datetime from various formats."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            normalized = value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None
    return None


# ═══════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════
def safe_mean(values: Sequence[float]) -> float:
    """Safe mean calculation."""
    return statistics.mean(values) if values else 0.0


def safe_median(values: Sequence[float]) -> float:
    """Safe median calculation."""
    return statistics.median(values) if values else 0.0


def safe_stddev(values: Sequence[float]) -> float:
    """Safe standard deviation."""
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    """Clamp value to range."""
    return max(minimum, min(maximum, value))


def generate_id(prefix: str) -> str:
    """Generate unique ID."""
    raw = f"{prefix}:{time.time_ns()}:{random.random()}"
    return f"{prefix}_{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def time_bucket(dt: Optional[datetime] = None) -> str:
    """Classify time-of-day into a bucket (v5.1)."""
    h = (dt or utc_now()).hour
    if 0 <= h < 6:
        return "night"
    if 6 <= h < 12:
        return "morning"
    if 12 <= h < 18:
        return "afternoon"
    return "evening"


def day_context(dt: Optional[datetime] = None) -> str:
    """Classify day into weekday/weekend (v5.1)."""
    d = (dt or utc_now()).weekday()
    return "weekend" if d >= 5 else "weekday"


# ═══════════════════════════════════════════════════════════════════════════
# ANOMALY CLASSIFIER
# ═══════════════════════════════════════════════════════════════════════════
