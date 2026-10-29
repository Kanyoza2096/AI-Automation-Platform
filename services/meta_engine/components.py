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


import psutil  # ← was missing and used by ResourceMonitor

from typing import TYPE_CHECKING

from .models import (
    EngineStatus,
    IncidentStatus,
    ResourceMode,
    SystemResources,
    SystemSnapshot,
)
from .utils import iso

if TYPE_CHECKING:
    from .core import MetaEngineV7

class ResourceMonitor:
    def __init__(self, cautious_memory_percent: float = 70.0, pressured_memory_percent: float = 82.0, emergency_memory_percent: float = 92.0, cautious_rss_mb: float = 250.0, pressured_rss_mb: float = 350.0, emergency_rss_mb: float = 450.0):
        self.cautious_memory_percent = cautious_memory_percent
        self.pressured_memory_percent = pressured_memory_percent
        self.emergency_memory_percent = emergency_memory_percent
        self.cautious_rss_mb = cautious_rss_mb
        self.pressured_rss_mb = pressured_rss_mb
        self.emergency_rss_mb = emergency_rss_mb
        self._process = psutil.Process(os.getpid()) if psutil else None

    def _linux_rss_mb(self) -> float:
        try:
            with open("/proc/self/status", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        return float(line.split()[1]) / 1024.0
        except Exception:
            return 0.0
        return 0.0

    def sample(self) -> SystemResources:
        rss_mb = 0.0
        memory_percent = 0.0
        cpu_percent = 0.0
        available_memory_mb = 0.0
        disk_percent = 0.0

        try:
            if self._process is not None:
                rss_mb = self._process.memory_info().rss / 1024 / 1024
                cpu_percent = self._process.cpu_percent(interval=None)
                vm = psutil.virtual_memory()
                memory_percent = float(vm.percent)
                available_memory_mb = vm.available / 1024 / 1024
                disk = psutil.disk_usage("/")
                disk_percent = float(disk.percent)
            else:
                rss_mb = self._linux_rss_mb()
        except Exception:
            pass

        if memory_percent >= self.emergency_memory_percent or rss_mb >= self.emergency_rss_mb:
            mode = ResourceMode.EMERGENCY
        elif memory_percent >= self.pressured_memory_percent or rss_mb >= self.pressured_rss_mb:
            mode = ResourceMode.PRESSURED
        elif memory_percent >= self.cautious_memory_percent or rss_mb >= self.cautious_rss_mb:
            mode = ResourceMode.CAUTIOUS
        else:
            mode = ResourceMode.NORMAL

        return SystemResources(rss_mb=round(rss_mb, 2), memory_percent=round(memory_percent, 2), cpu_percent=round(cpu_percent, 2), available_memory_mb=round(available_memory_mb, 2), disk_percent=round(disk_percent, 2), mode=mode)


# ═══════════════════════════════════════════════════════════════════════════
# DEPENDENCY GRAPH (Retained)
# ═══════════════════════════════════════════════════════════════════════════

class DependencyGraph:
    def __init__(self):
        self.edges: Dict[str, List[str]] = {}
        self.reverse: Dict[str, List[str]] = {}

    def add(self, engine: str, depends_on: Iterable[str]) -> None:
        dependencies = list(dict.fromkeys(depends_on))
        self.edges[engine] = dependencies
        for dependency in dependencies:
            self.reverse.setdefault(dependency, [])
            if engine not in self.reverse[dependency]:
                self.reverse[dependency].append(engine)

    def get_dependencies(self, engine: str) -> List[str]:
        return list(self.edges.get(engine, []))

    def get_dependents(self, engine: str) -> List[str]:
        return list(self.reverse.get(engine, []))

    def nodes(self) -> Set[str]:
        result = set(self.edges)
        for dependencies in self.edges.values():
            result.update(dependencies)
        result.update(self.reverse)
        return result


# ═══════════════════════════════════════════════════════════════════════════
# BOUNDED PROBE EXECUTOR (Retained)
# ═══════════════════════════════════════════════════════════════════════════

class BoundedProbeExecutor:
    def __init__(self, timeout_seconds: float = 3.0):
        self.timeout_seconds = timeout_seconds
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="meta-health")
        self._lock = threading.RLock()
        self._busy = False

    def run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Tuple[bool, Any]:
        with self._lock:
            if self._busy:
                return False, TimeoutError("Health probe worker busy")
            self._busy = True
        try:
            future = self._executor.submit(fn, *args, **kwargs)
            try:
                result = future.result(timeout=self.timeout_seconds)
                return True, result
            except FutureTimeout:
                return False, TimeoutError(f"Health probe timeout after {self.timeout_seconds}s")
            except Exception as exc:
                return False, exc
        finally:
            with self._lock:
                self._busy = False

    def shutdown(self) -> None:
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._executor.shutdown(wait=False)


# ═══════════════════════════════════════════════════════════════════════════
# BOUNDED RECOVERY EXECUTOR (Retained)
# ═══════════════════════════════════════════════════════════════════════════

class BoundedRecoveryExecutor:
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="meta-recovery")
        self._lock = threading.RLock()
        self._busy = False

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Optional[Future]:
        with self._lock:
            if self._busy:
                return None
            self._busy = True
        future = self._executor.submit(fn, *args, **kwargs)
        def _done(_: Future) -> None:
            with self._lock:
                self._busy = False
        future.add_done_callback(_done)
        return future

    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def shutdown(self) -> None:
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._executor.shutdown(wait=False)


# ═══════════════════════════════════════════════════════════════════════════
# ALERT RATE LIMITER (Retained)
# ═══════════════════════════════════════════════════════════════════════════

class GlobalAlertRateLimiter:
    def __init__(self, max_alerts_per_minute: int = 3, max_alerts_per_hour: int = 15):
        self.max_alerts_per_minute = max_alerts_per_minute
        self.max_alerts_per_hour = max_alerts_per_hour
        self._minute: Deque[float] = deque(maxlen=max_alerts_per_minute * 2)
        self._hour: Deque[float] = deque(maxlen=max_alerts_per_hour * 2)
        self._lock = threading.RLock()

    def can_send(self) -> bool:
        with self._lock:
            now = time.monotonic()
            while self._minute and now - self._minute[0] >= 60:
                self._minute.popleft()
            while self._hour and now - self._hour[0] >= 3600:
                self._hour.popleft()
            return len(self._minute) < self.max_alerts_per_minute and len(self._hour) < self.max_alerts_per_hour

    def record_send(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._minute.append(now)
            self._hour.append(now)


# ═══════════════════════════════════════════════════════════════════════════
# STATE PERSISTENCE (Retained with FIX)
# ═══════════════════════════════════════════════════════════════════════════

class StatePersistence:
    def __init__(self, supabase_client: Any = None, file_path: str = "/tmp/kanyoza_meta_engine_state.json", minimum_save_interval_seconds: int = 300, meta_engine_ref: Any = None):
        self.supabase = supabase_client
        self.file_path = file_path
        self.minimum_save_interval_seconds = minimum_save_interval_seconds
        self._last_save_monotonic = 0.0
        self._lock = threading.RLock()
        self._meta_engine_ref = meta_engine_ref

    def _serialize(self, snapshot: SystemSnapshot) -> Dict[str, Any]:
        topology_drift = None
        if self._meta_engine_ref:
            topology_drift = getattr(self._meta_engine_ref, '_topology_drift', None)
        
        return {
            "timestamp": iso(snapshot.timestamp),
            "overall_status": snapshot.overall_status.value,
            "health_score": snapshot.health_score,
            "risk_score": snapshot.risk_score,
            "engine_count": len(snapshot.engines),
            "topology_drift": dataclasses.asdict(topology_drift) if topology_drift else None,
            "resources": {"rss_mb": snapshot.resources.rss_mb, "memory_percent": snapshot.resources.memory_percent, "cpu_percent": snapshot.resources.cpu_percent, "mode": snapshot.resources.mode.value},
            "engines": {name: {"status": health.status.value, "health_score": health.health_score, "risk_score": health.risk_score, "error_count_1h": health.error_count_1h} for name, health in snapshot.engines.items()},
        }

    def save_snapshot(self, snapshot: SystemSnapshot, force: bool = False) -> bool:
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_save_monotonic < self.minimum_save_interval_seconds:
                return False
            data = self._serialize(snapshot)
            saved = False
            if self.supabase:
                try:
                    self.supabase.table("meta_engine_snapshots").upsert({"timestamp": iso(snapshot.timestamp), "payload": json.dumps(data, separators=(",", ":"))}).execute()
                    saved = True
                except Exception as exc:
                    logger.warning("Meta Engine Supabase persistence failed: %s", exc)
            try:
                temporary = self.file_path + ".tmp"
                with open(temporary, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, separators=(",", ":"))
                os.replace(temporary, self.file_path)
                saved = True
            except Exception:
                pass
            if saved:
                self._last_save_monotonic = now
            return saved

    def load_recent_snapshot(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            try:
                with open(self.file_path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                pass
            if self.supabase:
                try:
                    result = self.supabase.table("meta_engine_snapshots").select("*").order("timestamp", desc=True).limit(1).execute()
                    if result.data:
                        return json.loads(result.data[0].get("payload", "{}"))
                except Exception:
                    pass
            return None


# ═══════════════════════════════════════════════════════════════════════════
# INCIDENT MANAGER (Retained)
# ═══════════════════════════════════════════════════════════════════════════

class IncidentManager:
    def __init__(self, engine: "MetaEngineV7"):
        self.engine = engine

    def resolve_healthy_incidents(self, snapshot: SystemSnapshot) -> int:
        resolved = 0
        for incident in list(self.engine._incidents.values()):
            if incident.status == IncidentStatus.RESOLVED:
                continue
            health = snapshot.engines.get(incident.primary_engine)
            if not health or health.status != EngineStatus.NOMINAL:
                continue
            affected_healthy = True
            for affected in incident.affected_engines:
                affected_health = snapshot.engines.get(affected)
                if affected_health and affected_health.status not in (EngineStatus.NOMINAL, EngineStatus.UNKNOWN):
                    affected_healthy = False
                    break
            if affected_healthy:
                now = datetime.now(timezone.utc)
                incident.status = IncidentStatus.RESOLVED
                incident.resolved_at = now
                incident.updated_at = now
                resolved += 1
                self.engine.learn_from_incident(incident)
        return resolved

    def escalate_old_incidents(self) -> int:
        escalated = 0
        now = datetime.now(timezone.utc)
        for incident in list(self.engine._incidents.values()):
            if incident.status in (IncidentStatus.RESOLVED, IncidentStatus.ESCALATED):
                continue
            age = (now - incident.created_at).total_seconds()
            if age >= self.engine.INCIDENT_ESCALATION_SECONDS:
                incident.status = IncidentStatus.ESCALATED
                incident.updated_at = now
                escalated += 1
        return escalated


# ═══════════════════════════════════════════════════════════════════════════
# META ENGINE V7.1 — COMPLETE + INTELLIGENT
# ═══════════════════════════════════════════════════════════════════════════
