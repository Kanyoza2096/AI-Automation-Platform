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



def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None

def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    try:
        return max(low, min(high, float(value)))
    except Exception:
        return low

def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def percentile(values: Iterable[float], p: float) -> float:
    data = sorted(float(v) for v in values)
    if not data:
        return 0.0
    if len(data) == 1:
        return data[0]
    p = clamp(p, 0.0, 100.0)
    index = (len(data) - 1) * p / 100.0
    low = int(math.floor(index))
    high = int(math.ceil(index))
    if low == high:
        return data[low]
    return data[low] + (data[high] - data[low]) * (index - low)

def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


# ═══════════════════════════════════════════════════════════════════════════
# RESOURCE MONITOR (Retained)
# ═══════════════════════════════════════════════════════════════════════════
def _publish_event(event_name: str, payload: Dict[str, Any]) -> None:
    """Publish event — works from threads by scheduling on the loop."""
    if not HAS_EVENT_BUS:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_publish_event_async(event_name, payload))
        return
    except RuntimeError:
        pass
    # We're in a thread — try the main loop captured at startup
    main_loop = getattr(_publish_event, "_main_loop", None)
    if main_loop and not main_loop.is_closed():
        try:
            asyncio.run_coroutine_threadsafe(
                _publish_event_async(event_name, payload), main_loop
            )
        except Exception as exc:
            logger.debug("Meta Engine schedule failed: %s", exc)

# ═══════════════════════════════════════════════════════════════════════════
# OPTIONAL PSUTIL
# ═══════════════════════════════════════════════════════════════════════════

try:
    import psutil
except Exception:
    psutil = None


# ═══════════════════════════════════════════════════════════════════════════
# ENUMS (Retained)
# ═══════════════════════════════════════════════════════════════════════════
