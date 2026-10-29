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


class EngineStatus(str, Enum):
    NOMINAL = "nominal"
    DEGRADED = "degraded"
    STALE = "stale"
    OFFLINE = "offline"
    UNKNOWN = "unknown"

class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

class IncidentStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    HEALING = "healing"
    RESOLVED = "resolved"
    ESCALATED = "escalated"

class RecoveryStatus(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    VERIFIED = "verified"

class AnomalyType(str, Enum):
    ERROR_SPIKE = "error_spike"
    LATENCY_SPIKE = "latency_spike"
    THROUGHPUT_DROP = "throughput_drop"
    SILENCE = "unexpected_silence"
    HEALTH_REGRESSION = "health_regression"
    DEPENDENCY_FAILURE = "dependency_failure"
    CASCADING_FAILURE = "cascading_failure"
    RESOURCE_PRESSURE = "resource_pressure"
    MEMORY_PRESSURE = "memory_pressure"
    CPU_PRESSURE = "cpu_pressure"
    RECOVERY_LOOP = "recovery_loop"

class EscalationTier(str, Enum):
    NONE = "none"
    LOG_ONLY = "log_only"
    DASHBOARD = "dashboard"
    TELEGRAM = "telegram"
    EMAIL = "email"
    SMS = "sms"
    ON_CALL = "on_call"

class ResourceMode(str, Enum):
    NORMAL = "normal"
    CAUTIOUS = "cautious"
    PRESSURED = "pressured"
    EMERGENCY = "emergency"


# ═══════════════════════════════════════════════════════════════════════════
# DATACLASSES (Retained + Enhanced)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class EngineHealth:
    name: str
    status: EngineStatus = EngineStatus.UNKNOWN
    health_score: float = 0.0
    risk_score: float = 0.0
    last_active: Optional[datetime] = None
    error_count_5m: int = 0
    error_count_1h: int = 0
    success_count_5m: int = 0
    success_count_1h: int = 0
    throughput_5m: float = 0.0
    throughput_1h: float = 0.0
    avg_latency_ms: float = 0.0
    median_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    queue_depth: int = 0
    memory_mb: float = 0.0
    cpu_percent: float = 0.0
    disk_percent: float = 0.0
    dependencies: List[str] = field(default_factory=list)
    dependents: List[str] = field(default_factory=list)
    restart_count: int = 0
    last_restart: Optional[datetime] = None
    version: str = ""
    anomaly_flags: List[str] = field(default_factory=list)
    failure_class: str = "healthy"
    root_dependency: Optional[str] = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

@dataclass
class SystemResources:
    rss_mb: float = 0.0
    memory_percent: float = 0.0
    cpu_percent: float = 0.0
    available_memory_mb: float = 0.0
    disk_percent: float = 0.0
    mode: ResourceMode = ResourceMode.NORMAL
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

@dataclass
class SystemSnapshot:
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    engines: Dict[str, EngineHealth] = field(default_factory=dict)
    overall_status: EngineStatus = EngineStatus.UNKNOWN
    health_score: float = 0.0
    risk_score: float = 0.0
    active_alerts: int = 0
    critical_alerts: int = 0
    active_incidents: int = 0
    uptime_seconds: float = 0.0
    resources: SystemResources = field(default_factory=SystemResources)

@dataclass
class Diagnosis:
    id: str
    engine: str
    symptom: str
    root_cause: str
    severity: AlertLevel
    affected_engines: List[str] = field(default_factory=list)
    dependency_chain: List[str] = field(default_factory=list)
    recommended_action: str = ""
    auto_fixable: bool = False
    confidence: float = 0.0
    risk_score: float = 0.0
    escalation: EscalationTier = EscalationTier.DASHBOARD
    matched_pattern: Optional[str] = None
    narrative: Optional[str] = None
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)

@dataclass
class MetricEvent:
    timestamp: datetime
    kind: str
    engine: str
    value: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Anomaly:
    id: str
    engine: str
    anomaly_type: AnomalyType
    description: str
    severity: AlertLevel
    observed_value: float = 0.0
    expected_value: float = 0.0
    deviation: float = 0.0
    confidence: float = 0.0
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved: bool = False
    linked_diagnosis: Optional[str] = None

@dataclass
class RecoveryAttempt:
    id: str
    engine: str
    reason: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: RecoveryStatus = RecoveryStatus.NOT_ATTEMPTED
    action: str = ""
    error: Optional[str] = None
    verification_passed: bool = False

@dataclass
class Incident:
    id: str
    title: str
    severity: AlertLevel
    status: IncidentStatus
    primary_engine: str
    affected_engines: List[str] = field(default_factory=list)
    diagnoses: List[str] = field(default_factory=list)
    anomalies: List[str] = field(default_factory=list)
    root_cause: str = ""
    confidence: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None
    recovery_attempts: List[str] = field(default_factory=list)

@dataclass
class CircuitState:
    engine: str
    failures: int = 0
    state: str = "closed"
    opened_at: Optional[datetime] = None
    half_open_at: Optional[datetime] = None
    cooldown_seconds: int = 60

@dataclass
class Prediction:
    engine: str
    predicted_failure: str
    confidence: float
    estimated_time_to_failure_minutes: float
    contributing_factors: List[str] = field(default_factory=list)
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

@dataclass
class FailurePattern:
    id: str
    signature: str
    conditions: Dict[str, Any] = field(default_factory=dict)
    root_cause: str = ""
    fix_applied: str = ""
    success_rate: float = 0.5
    occurrences: int = 0
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

@dataclass
class CachedHealth:
    health: EngineHealth
    expires_at: float


# ═══════════════════════════════════════════════════════════════════════════
# v7.1 — CONTEXT-AWARE HEALTH LEARNER
# ═══════════════════════════════════════════════════════════════════════════
